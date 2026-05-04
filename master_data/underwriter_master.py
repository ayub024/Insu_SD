"""Underwriter master data generation with month-by-month hiring growth."""

from __future__ import annotations
from functools import lru_cache
import os

from collections import Counter
from dataclasses import dataclass
from datetime import date
from math import ceil
from pathlib import Path
from typing import Optional

import numpy as np

from core.id_factory import IdFactory
from core.growth_engine import GrowthEngine

_ALLOWED_REGIONS = ["Midwest", "Southeast", "Northeast", "Southwest", "West"]
_TEAM_OPTIONS = [
    "Commercial Property UW",
    "Commercial Casualty UW",
    "Personal Lines UW",
]
_SENIORITY_LEVELS = ["Junior", "Mid", "Senior", "Lead"]
_TARGET_SENIORITY_MIX = {
    "Junior": 0.40,
    "Mid": 0.35,
    "Senior": 0.20,
    "Lead": 0.05,
}

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
        raise ImportError("PyYAML is required for underwriter master configuration loading") from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Configuration file not found: {p}")

    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


class UnderwriterMaster:
    """Generates and tracks active underwriters over time."""

    def __init__(
        self,
        growth_curves_path: str = "config/growth_curves.yaml",
        geo_distribution_path: str = "config/geo_distribution.yaml",
        scenario_path: str = os.getenv("SCENARIO_PATH", "config/scenario.yaml"),
        seed: int = 20260215,
    ) -> None:
        self._rng = np.random.default_rng(int(seed))
        self._ids = IdFactory()
        self._growth_curves_path = growth_curves_path
        self._scenario_path = scenario_path

        growth = _load_yaml(growth_curves_path).get("growth_curves", {})
        geo = _load_yaml(geo_distribution_path).get("geo_distribution", {})

        underwriter_cfg = growth.get("underwriters", {})
        scale_cfg = growth.get("scale_constraints", {})

        self.start_count = int(underwriter_cfg.get("start_count", 70))
        self.end_count = int(underwriter_cfg.get("end_count", 420))
        self.min_policy_to_uw_ratio = int(scale_cfg.get("policies_to_underwriters_min_ratio", 250))

        if self.start_count <= 0 or self.end_count < self.start_count:
            raise ValueError("Invalid underwriter growth counts in growth_curves.yaml")

        if set(geo.keys()) != set(_ALLOWED_REGIONS):
            raise ValueError(
                "geo_distribution.yaml must contain exactly: " + ", ".join(_ALLOWED_REGIONS)
            )

        geo_total = float(sum(geo.values()))
        if abs(geo_total - 100.0) > 1e-9:
            raise ValueError("geo_distribution percentages must sum to 100")

        self._region_values = _ALLOWED_REGIONS
        self._region_probs = np.array([float(geo[r]) / 100.0 for r in _ALLOWED_REGIONS], dtype=float)

        self._team_probs = np.array([0.35, 0.35, 0.30], dtype=float)

        # In-memory underwriter rows (strict Dim_Underwriter columns only).
        self._rows: list[dict] = []
        # Tracks first active month for each generated underwriter.
        self._active_from: dict[str, date] = {}
        # Tracks months already processed with planned hiring.
        self._applied_planned_months: set[date] = set()

        self._window = MonthWindow(start=date(2022, 1, 1), end=date(2025, 12, 1))
        self._planned_hires = self._build_monthly_hiring_plan()
        self._policy_capacity_by_month = self._build_policy_capacity_by_month()

    def _build_monthly_hiring_plan(self) -> dict[date, int]:
        """Create non-static monthly hiring targets with quarterly waves."""
        months = _month_iter(self._window.start, self._window.end)
        total_new = self.end_count - self.start_count
        if total_new == 0:
            return {m: 0 for m in months}

        weights: list[float] = []
        for idx, month in enumerate(months):
            t = idx / max(1, (len(months) - 1))

            # Early ramp-up then stabilization.
            if t < 0.30:
                base = 0.7 + (1.6 * (t / 0.30))
            else:
                base = 2.3 - (0.35 * ((t - 0.30) / 0.70))

            # Quarterly hiring waves at quarter-end months.
            if month.month in (3, 6, 9, 12):
                base *= 1.25

            weights.append(max(base, 0.05))

        weight_arr = np.array(weights, dtype=float)
        scaled = weight_arr / weight_arr.sum() * total_new
        floor_vals = np.floor(scaled).astype(int)
        remainder = int(total_new - floor_vals.sum())

        fractional_order = np.argsort(-(scaled - floor_vals))
        for i in range(remainder):
            floor_vals[fractional_order[i]] += 1

        return {month: int(floor_vals[i]) for i, month in enumerate(months)}

    def _build_policy_capacity_by_month(self) -> dict[date, int]:
        """Build monthly max active underwriter capacity from policy targets."""
        if self.min_policy_to_uw_ratio <= 0:
            return {}

        engine = GrowthEngine(
            growth_curves_path=self._growth_curves_path,
            scenario_path=self._scenario_path,
        )
        cumulative_policies = 0
        capacity: dict[date, int] = {}
        for month_key in engine._month_keys:
            month_dt = _parse_month(month_key)
            cumulative_policies += int(engine.get_new_policy_target(month_key))
            capacity[month_dt] = int(ceil(cumulative_policies / self.min_policy_to_uw_ratio))
        return capacity

    def _capacity_for_month(self, month_dt: date) -> Optional[int]:
        """Resolve max allowed active underwriters for a month."""
        if not self._policy_capacity_by_month:
            return None

        if month_dt in self._policy_capacity_by_month:
            return self._policy_capacity_by_month[month_dt]

        # Fallback to latest known month capacity.
        eligible = [m for m in self._policy_capacity_by_month.keys() if m <= month_dt]
        if not eligible:
            return None
        latest = max(eligible)
        return self._policy_capacity_by_month[latest]

    def _region_seniority_counts(self, region: str) -> Counter:
        counts: Counter = Counter()
        for row in self._rows:
            if str(row.get("underwriter_region")) == region:
                counts[str(row.get("seniority_level"))] += 1
        return counts

    def _choose_seniority_for_region(self, region: str) -> str:
        """Choose seniority to keep target regional mix with hard minimums."""
        counts = self._region_seniority_counts(region)
        total_current = sum(counts.values())
        total_after = total_current + 1

        # Hard minimums once region is meaningfully staffed.
        if total_after > 20:
            if counts.get("Lead", 0) < 1:
                return "Lead"
            if counts.get("Senior", 0) < 2:
                return "Senior"

        target_counts = {
            level: _TARGET_SENIORITY_MIX[level] * total_after for level in _SENIORITY_LEVELS
        }
        deficits = {
            level: target_counts[level] - counts.get(level, 0) for level in _SENIORITY_LEVELS
        }
        max_deficit = max(deficits.values())

        if max_deficit > 0:
            for level in _SENIORITY_LEVELS:
                if deficits[level] == max_deficit:
                    return level

        probs = np.array([_TARGET_SENIORITY_MIX[level] for level in _SENIORITY_LEVELS], dtype=float)
        probs = probs / probs.sum()
        return str(self._rng.choice(_SENIORITY_LEVELS, p=probs))

    def _allocate_regions(self, total_count: int) -> dict[str, int]:
        if total_count <= 0:
            return {r: 0 for r in _ALLOWED_REGIONS}
        alloc = self._rng.multinomial(total_count, self._region_probs)
        return {region: int(alloc[idx]) for idx, region in enumerate(_ALLOWED_REGIONS)}

    def _manager_name_for_team_region(self, team: str, region: str) -> str:
        # Generate a stable realistic name based on the team and region
        import hashlib
        hash_bytes = hashlib.md5((team + region).encode('utf-8')).digest()
        seed = int.from_bytes(hash_bytes[:4], 'little')
        local_rng = np.random.default_rng(seed)
        first = str(local_rng.choice(_FIRST_NAMES))
        last = str(local_rng.choice(_LAST_NAMES))
        return f"{first} {last}"

    def _make_underwriter_row(self, hire_month: date, region_override: Optional[str] = None) -> dict:
        first = str(self._rng.choice(_FIRST_NAMES))
        last = str(self._rng.choice(_LAST_NAMES))
        team = str(self._rng.choice(_TEAM_OPTIONS, p=self._team_probs))
        region = str(region_override or self._rng.choice(self._region_values, p=self._region_probs))
        seniority = self._choose_seniority_for_region(region)

        manager_name = self._manager_name_for_team_region(team, region)
        key = self._ids.next_underwriter_key()

        row = {
            "underwriter_key": key,
            "underwriter_name": f"{first} {last}",
            "team": team,
            "underwriter_region": region,
            "seniority_level": seniority,
            "manager_name": manager_name,
        }
        self._active_from[key] = hire_month
        return row

    def init_underwriters(self, start_date: str | date = "2022-01-01") -> list[dict]:
        """Initialize workforce at start month and return active underwriters."""
        start_month = _parse_month(start_date)

        # Reset state for deterministic replay.
        self._rows = []
        self._active_from = {}
        self._applied_planned_months = set()
        self._ids = IdFactory()

        start_cap = self._capacity_for_month(start_month)
        init_count = self.start_count
        if start_cap is not None:
            init_count = min(init_count, max(1, start_cap))

        init_by_region = self._allocate_regions(init_count)
        for region, count in init_by_region.items():
            for _ in range(count):
                self._rows.append(self._make_underwriter_row(start_month, region_override=region))

        return self.get_active_underwriters(start_month)

    def hire_underwriters(
        self,
        month: str | date,
        target_new_count: Optional[int] = None,
        policy_count: Optional[int] = None,
    ) -> list[dict]:
        """Hire new underwriters for a given month and return newly hired rows.

        If target_new_count is not provided, a non-static monthly growth plan is used.
        If policy_count is provided, hiring is capped to preserve policies >> underwriters.
        """
        month_dt = _parse_month(month)

        planned = self._planned_hires.get(month_dt, 0)
        if target_new_count is None:
            if month_dt in self._applied_planned_months:
                return []
            new_count = planned
            self._applied_planned_months.add(month_dt)
        else:
            new_count = int(target_new_count)

        if new_count < 0:
            raise ValueError("target_new_count must be >= 0")

        if policy_count is not None and self.min_policy_to_uw_ratio > 0:
            max_allowed = int(policy_count) // self.min_policy_to_uw_ratio
            allowed_new = max(0, max_allowed - len(self.get_active_underwriters(month_dt)))
            new_count = min(new_count, allowed_new)
        elif self.min_policy_to_uw_ratio > 0:
            month_capacity = self._capacity_for_month(month_dt)
            if month_capacity is not None:
                active_now = len(self.get_active_underwriters(month_dt))
                allowed_new = max(0, int(month_capacity) - active_now)
                new_count = min(new_count, allowed_new)

        hires_by_region = self._allocate_regions(new_count)

        new_rows: list[dict] = []
        for region, count in hires_by_region.items():
            for _ in range(count):
                row = self._make_underwriter_row(month_dt, region_override=region)
                self._rows.append(row)
                new_rows.append(row)

        return new_rows

    def get_active_underwriters(self, month: str | date) -> list[dict]:
        """Return underwriters active on or before the given month."""
        month_dt = _parse_month(month)
        return [
            row
            for row in self._rows
            if self._active_from.get(row["underwriter_key"], date.max) <= month_dt
        ]

    def create_underwriter_for_criteria(
        self,
        month: str | date,
        region: str,
        team: str,
        seniority_level: str,
    ) -> dict:
        """Create one active underwriter that matches required assignment criteria."""
        month_dt = _parse_month(month)

        if region not in _ALLOWED_REGIONS:
            raise ValueError(f"region must be one of {_ALLOWED_REGIONS}")
        if team not in _TEAM_OPTIONS:
            raise ValueError(f"team must be one of {_TEAM_OPTIONS}")
        if seniority_level not in _SENIORITY_LEVELS:
            raise ValueError(f"seniority_level must be one of {_SENIORITY_LEVELS}")

        first = str(self._rng.choice(_FIRST_NAMES))
        last = str(self._rng.choice(_LAST_NAMES))
        key = self._ids.next_underwriter_key()

        row = {
            "underwriter_key": key,
            "underwriter_name": f"{first} {last}",
            "team": team,
            "underwriter_region": region,
            "seniority_level": seniority_level,
            "manager_name": self._manager_name_for_team_region(team, region),
        }
        self._rows.append(row)
        self._active_from[key] = month_dt
        return row


# Module-level API requested by the spec user story.
_default_master: Optional[UnderwriterMaster] = None


def _instance() -> UnderwriterMaster:
    global _default_master
    if _default_master is None:
        _default_master = UnderwriterMaster()
    return _default_master


def init_underwriters(start_date: str | date = "2022-01-01") -> list[dict]:
    return _instance().init_underwriters(start_date)


def hire_underwriters(
    month: str | date,
    target_new_count: Optional[int] = None,
    policy_count: Optional[int] = None,
) -> list[dict]:
    return _instance().hire_underwriters(
        month=month,
        target_new_count=target_new_count,
        policy_count=policy_count,
    )


def get_active_underwriters(month: str | date) -> list[dict]:
    return _instance().get_active_underwriters(month)


def create_underwriter_for_criteria(
    month: str | date,
    region: str,
    team: str,
    seniority_level: str,
) -> dict:
    return _instance().create_underwriter_for_criteria(
        month=month,
        region=region,
        team=team,
        seniority_level=seniority_level,
    )
