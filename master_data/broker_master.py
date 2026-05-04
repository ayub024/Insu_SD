"""Broker master data generation with month-by-month onboarding growth."""

from __future__ import annotations
from functools import lru_cache
import os

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np

from core.id_factory import IdFactory

_ALLOWED_REGIONS = ["Midwest", "Southeast", "Northeast", "Southwest", "West"]
_BROKER_TYPES = ["Agent", "Broker", "Bancassurance"]

_FIRST_NAMES = [
    "Alex",
    "Jordan",
    "Taylor",
    "Morgan",
    "Casey",
    "Riley",
    "Avery",
    "Quinn",
    "Parker",
    "Cameron",
    "Drew",
    "Reese",
]
_LAST_NAMES = [
    "Smith",
    "Johnson",
    "Lee",
    "Brown",
    "Miller",
    "Davis",
    "Wilson",
    "Clark",
    "Lewis",
    "Hall",
    "Young",
    "Allen",
]


@dataclass(frozen=True)
class MonthWindow:
    start: date
    end: date


def _parse_month(month_value: str | date) -> date:
    if isinstance(month_value, date):
        return month_value.replace(day=1)
    if len(month_value) == 7:
        return date.fromisoformat(f"{month_value}-01")
    return date.fromisoformat(month_value).replace(day=1)


def _month_iter(start: date, end: date) -> list[date]:
    months: list[date] = []
    cursor = start
    while cursor <= end:
        months.append(cursor)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


@lru_cache(maxsize=None)
def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required for broker master configuration loading") from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Configuration file not found: {p}")

    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


class BrokerMaster:
    """Generates and tracks active brokers over time."""

    def __init__(
        self,
        growth_curves_path: str = "config/growth_curves.yaml",
        geo_distribution_path: str = "config/geo_distribution.yaml",
        seed: int = 20260215,
        inactive_pct: float = 0.03,
    ) -> None:
        if not 0.0 <= inactive_pct < 1.0:
            raise ValueError("inactive_pct must be in [0.0, 1.0)")

        self._rng = np.random.default_rng(int(seed))
        self._ids = IdFactory()
        self._inactive_pct = float(inactive_pct)

        growth = _load_yaml(growth_curves_path).get("growth_curves", {})
        geo = _load_yaml(geo_distribution_path).get("geo_distribution", {})

        brokers_cfg = growth.get("brokers", {})

        self.start_count = int(brokers_cfg.get("start_count", 140))
        self.end_count = int(brokers_cfg.get("end_count", 620))

        scenario_payload = _load_yaml(os.getenv("SCENARIO_PATH", "config/scenario.yaml")).get("scenario", {})
        
        mode = str(scenario_payload.get("mode", "")).strip().lower()
        if mode not in {"dev", "prod"}:
            if scenario_payload.get("dev_mode"): mode = "dev"
            elif scenario_payload.get("prod_mode"): mode = "prod"
            else: mode = "dev"
            
        active_preset = scenario_payload.get("presets", {}).get(mode, {}) or {}
        target_scale = active_preset.get("target_scale", scenario_payload.get("target_scale", {}))
        
        ratio = float(target_scale.get("min_policy_to_broker_ratio", 0))
        policy_total = int(target_scale.get("total_policies", 0))

        if ratio > 0 and policy_total > 0:
            expected_end = max(1, int(policy_total / ratio))
            if self.end_count > 0:
                scale_factor = expected_end / float(self.end_count)
                self.start_count = int(self.start_count * scale_factor)
                self.end_count = expected_end

        if self.start_count <= 0 or self.end_count < self.start_count:
            raise ValueError("Invalid broker growth counts in growth_curves.yaml")

        if set(geo.keys()) != set(_ALLOWED_REGIONS):
            raise ValueError(
                "geo_distribution.yaml must contain exactly: " + ", ".join(_ALLOWED_REGIONS)
            )

        geo_total = float(sum(geo.values()))
        if abs(geo_total - 100.0) > 1e-9:
            raise ValueError("geo_distribution percentages must sum to 100")

        self._region_values = _ALLOWED_REGIONS
        self._region_probs = np.array([float(geo[r]) / 100.0 for r in _ALLOWED_REGIONS], dtype=float)

        # Agent/Broker dominate; Bancassurance is smaller.
        self._broker_type_probs = np.array([0.35, 0.50, 0.15], dtype=float)

        # Strict Dim_Broker rows only.
        self._rows: list[dict] = []
        self._active_from: dict[str, date] = {}
        self._inactive_from: dict[str, Optional[date]] = {}
        self._applied_planned_months: set[date] = set()

        self._window = MonthWindow(start=date(2022, 1, 1), end=date(2025, 12, 1))
        self._planned_onboarding = self._build_monthly_onboarding_plan()

    def _build_monthly_onboarding_plan(self) -> dict[date, int]:
        """Create non-static monthly onboarding targets with quarterly waves."""
        months = _month_iter(self._window.start, self._window.end)
        total_new = self.end_count - self.start_count
        if total_new == 0:
            return {m: 0 for m in months}

        weights: list[float] = []
        for idx, month in enumerate(months):
            t = idx / max(1, (len(months) - 1))

            # Early onboarding ramp then stabilization.
            if t < 0.30:
                base = 0.8 + (1.8 * (t / 0.30))
            else:
                base = 2.6 - (0.45 * ((t - 0.30) / 0.70))

            # Quarterly waves around quarter-end months.
            if month.month in (3, 6, 9, 12):
                base *= 1.22

            weights.append(max(base, 0.05))

        weight_arr = np.array(weights, dtype=float)
        scaled = weight_arr / weight_arr.sum() * total_new
        floor_vals = np.floor(scaled).astype(int)
        remainder = int(total_new - floor_vals.sum())

        fractional_order = np.argsort(-(scaled - floor_vals))
        for i in range(remainder):
            floor_vals[fractional_order[i]] += 1

        return {month: int(floor_vals[i]) for i, month in enumerate(months)}

    def _new_license_number(self, broker_key: str, region: str) -> str:
        region_code = region[:3].upper()
        serial = broker_key.replace("BRK", "")
        return f"LIC-{region_code}-{serial}"

    def _make_broker_row(self, onboard_month: date) -> dict:
        first = str(self._rng.choice(_FIRST_NAMES))
        last = str(self._rng.choice(_LAST_NAMES))
        region = str(self._rng.choice(self._region_values, p=self._region_probs))
        broker_type = str(self._rng.choice(_BROKER_TYPES, p=self._broker_type_probs))

        key = self._ids.next_broker_key()
        row = {
            "broker_key": key,
            "broker_name": f"{last} {broker_type} Partners",
            "broker_type": broker_type,
            "broker_region": region,
            "license_number": self._new_license_number(key, region),
        }
        self._active_from[key] = onboard_month

        # Most brokers stay active; a small percentage become inactive later.
        if self._rng.uniform(0.0, 1.0) < self._inactive_pct:
            months_out = int(self._rng.integers(6, 25))
            year = onboard_month.year + (onboard_month.month - 1 + months_out) // 12
            month = (onboard_month.month - 1 + months_out) % 12 + 1
            self._inactive_from[key] = date(year, month, 1)
        else:
            self._inactive_from[key] = None

        return row

    def init_brokers(self, start_date: str | date = "2022-01-01") -> list[dict]:
        """Initialize starting broker population at scenario start month."""
        start_month = _parse_month(start_date)

        self._rows = []
        self._active_from = {}
        self._inactive_from = {}
        self._applied_planned_months = set()
        self._ids = IdFactory()

        for _ in range(self.start_count):
            self._rows.append(self._make_broker_row(start_month))

        return self.get_active_brokers(start_month, region=None, broker_type=None)

    def onboard_brokers(self, month: str | date, target_new_count: Optional[int] = None) -> list[dict]:
        """Onboard new brokers for a given month and return new rows."""
        month_dt = _parse_month(month)

        planned = self._planned_onboarding.get(month_dt, 0)
        if target_new_count is None:
            if month_dt in self._applied_planned_months:
                return []
            new_count = planned
            self._applied_planned_months.add(month_dt)
        else:
            new_count = int(target_new_count)

        if new_count < 0:
            raise ValueError("target_new_count must be >= 0")

        new_rows: list[dict] = []
        for _ in range(new_count):
            row = self._make_broker_row(month_dt)
            self._rows.append(row)
            new_rows.append(row)

        return new_rows

    def get_active_brokers(
        self,
        month: str | date,
        region: Optional[str],
        broker_type: Optional[str],
    ) -> list[dict]:
        """Return active brokers filtered by month, region, and broker_type.

        This supports policy assignment rules requiring broker region alignment.
        """
        month_dt = _parse_month(month)

        if region is not None and region not in _ALLOWED_REGIONS:
            raise ValueError(f"region must be one of {_ALLOWED_REGIONS}")

        if broker_type is not None and broker_type not in _BROKER_TYPES:
            raise ValueError(f"broker_type must be one of {_BROKER_TYPES}")

        filtered: list[dict] = []
        for row in self._rows:
            key = row["broker_key"]
            active_from = self._active_from.get(key)
            inactive_from = self._inactive_from.get(key)

            is_active = active_from is not None and active_from <= month_dt and (
                inactive_from is None or inactive_from > month_dt
            )
            if not is_active:
                continue

            if region is not None and row["broker_region"] != region:
                continue

            if broker_type is not None and row["broker_type"] != broker_type:
                continue

            filtered.append(row)

        return filtered


# Module-level API requested by the user story.
_default_master: Optional[BrokerMaster] = None


def _instance() -> BrokerMaster:
    global _default_master
    if _default_master is None:
        _default_master = BrokerMaster()
    return _default_master


def init_brokers() -> list[dict]:
    return _instance().init_brokers()


def onboard_brokers(month: str | date, target_new_count: Optional[int] = None) -> list[dict]:
    return _instance().onboard_brokers(month=month, target_new_count=target_new_count)


def get_active_brokers(
    month: str | date,
    region: Optional[str],
    broker_type: Optional[str],
) -> list[dict]:
    return _instance().get_active_brokers(month=month, region=region, broker_type=broker_type)
