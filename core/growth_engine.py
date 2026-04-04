"""Growth engine for non-static monthly policy/broker/underwriter targets."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np


def _parse_month(value: str | date) -> date:
    if isinstance(value, date):
        return value.replace(day=1)
    if len(value) == 7:
        return date.fromisoformat(f"{value}-01")
    return date.fromisoformat(value).replace(day=1)


def _month_iter(start: date, end: date) -> list[date]:
    months: list[date] = []
    cursor = start.replace(day=1)
    end_month = end.replace(day=1)
    while cursor <= end_month:
        months.append(cursor)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required for growth engine configuration loading") from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Configuration file not found: {p}")

    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _normalize_curve_type(curve_type: str) -> str:
    normalized = curve_type.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "ramp_with_waves": "quarterly_hiring_waves",
        "quarterly_waves": "quarterly_hiring_waves",
        "exp_with_damping": "exponential_with_damping",
    }
    return aliases.get(normalized, normalized)


def _month_ordinal(value: date) -> int:
    """Convert date month to sortable integer (year*12 + month)."""
    return value.year * 12 + value.month


class GrowthEngine:
    """Build monthly new-entity targets from configurable non-static curves."""

    def __init__(
        self,
        growth_curves_path: str = "config/growth_curves.yaml",
        scenario_path: str = "config/scenario.yaml",
    ) -> None:
        growth_payload = _load_yaml(growth_curves_path)
        scenario_payload = _load_yaml(scenario_path)

        self._growth = growth_payload.get("growth_curves", {})
        scenario = scenario_payload.get("scenario", {})
        presets = scenario.get("presets", {})
        dev_mode = bool(scenario.get("dev_mode", False))
        prod_mode = bool(scenario.get("prod_mode", False))

        active_preset = {}
        if dev_mode and not prod_mode:
            active_preset = presets.get("dev", {}) or {}
        elif prod_mode and not dev_mode:
            active_preset = presets.get("prod", {}) or {}

        date_range = active_preset.get("date_range", scenario.get("date_range", {}))
        self._target_scale = active_preset.get("target_scale", scenario.get("target_scale", {}))
        target_scale = self._target_scale

        start = date.fromisoformat(str(date_range.get("start", "2022-01-01")))
        end = date.fromisoformat(str(date_range.get("end", "2025-12-31")))

        self._months = _month_iter(start, end)
        if not self._months:
            raise ValueError("Scenario date range produced no months")

        self._month_keys = [m.strftime("%Y-%m") for m in self._months]
        self._month_lookup = {k: i for i, k in enumerate(self._month_keys)}

        self._policy_total = int(target_scale.get("total_policies", 0))

        self._policy_targets = self._build_policy_targets(self._growth.get("policies", {}))
        self._broker_targets = self._build_staff_targets(self._growth.get("brokers", {}), "min_policy_to_broker_ratio")
        self._underwriter_targets = self._build_staff_targets(self._growth.get("underwriters", {}), "min_policy_to_underwriter_ratio")

    def _curve_weights(self, cfg: dict) -> np.ndarray:
        curve = _normalize_curve_type(str(cfg.get("curve_type", "logistic")))
        n = len(self._months)
        t = np.linspace(0.0, 1.0, n)
        idx = np.arange(n)

        if curve == "logistic":
            midpoint_year = int(cfg.get("midpoint_year", self._months[n // 2].year))
            midpoint_month = int(cfg.get("midpoint_month", 7))
            midpoint_month = min(max(midpoint_month, 1), 12)

            # If midpoint is outside the active range, fall back to the middle
            # of the selected window so time-range changes remain stable.
            target = date(midpoint_year, midpoint_month, 1)
            first_ord = _month_ordinal(self._months[0])
            last_ord = _month_ordinal(self._months[-1])
            target_ord = _month_ordinal(target)

            if target_ord < first_ord or target_ord > last_ord:
                midpoint_index = n // 2
            else:
                midpoint_index = min(
                    range(n),
                    key=lambda i: abs(_month_ordinal(self._months[i]) - target_ord),
                )
            midpoint_t = midpoint_index / max(1, n - 1)
            growth_rate = float(cfg.get("growth_rate", 1.0))
            k = max(0.5, 10.0 * growth_rate)
            sig = 1.0 / (1.0 + np.exp(-k * (t - midpoint_t)))
            weights = sig * (1.0 - sig)

        elif curve == "exponential_with_damping":
            alpha = float(cfg.get("alpha", cfg.get("growth_rate", 2.2)))
            damping = float(cfg.get("damping", 1.6))
            weights = np.exp(alpha * t) * np.exp(-damping * (t**2))

        elif curve == "ramp_with_seasonality":
            start_level = float(cfg.get("start_level", 0.8))
            end_level = float(cfg.get("end_level", 2.0))
            amplitude = float(cfg.get("seasonality_amplitude", 0.18))
            phase = float(cfg.get("seasonality_phase", 0.0))
            base = np.linspace(start_level, end_level, n)
            seasonal = 1.0 + amplitude * np.sin(2.0 * np.pi * (idx / 12.0) + phase)
            weights = base * seasonal

        elif curve == "quarterly_hiring_waves":
            start_level = float(cfg.get("start_level", 0.9))
            end_level = float(cfg.get("end_level", 1.7))
            amplitude = float(cfg.get("seasonality_amplitude", 0.10))
            quarter_wave_pct = float(cfg.get("quarter_wave_pct", 0.25))

            base = np.linspace(start_level, end_level, n)
            seasonal = 1.0 + amplitude * np.sin(2.0 * np.pi * (idx / 12.0))
            weights = base * seasonal

            quarter_mask = np.array([m.month in (3, 6, 9, 12) for m in self._months], dtype=float)
            weights *= 1.0 + quarter_mask * quarter_wave_pct

            for wave in cfg.get("hiring_waves", []) or []:
                year = int(wave.get("year"))
                inc = float(wave.get("increment_pct", 0.0))
                wave_mask = np.array(
                    [m.year == year and m.month in (3, 6, 9, 12) for m in self._months],
                    dtype=float,
                )
                weights *= 1.0 + wave_mask * inc

        else:
            raise ValueError(
                "Unsupported curve_type. Use one of: "
                "logistic, exponential with damping, ramp with seasonality, quarterly hiring waves"
            )

        weights = np.clip(weights, 1e-8, None)
        return weights

    def _allocate_total(self, total_new: int, weights: np.ndarray) -> list[int]:
        if total_new < 0:
            raise ValueError("total_new must be >= 0")

        scaled = (weights / weights.sum()) * total_new
        counts = np.floor(scaled).astype(int)

        remainder = int(total_new - counts.sum())
        if remainder > 0:
            order = np.argsort(-(scaled - counts))
            for i in range(remainder):
                counts[order[i]] += 1

        result = counts.tolist()
        self._ensure_non_constant(result)
        return result

    def _ensure_non_constant(self, counts: list[int]) -> None:
        """Guardrail to avoid flat month-to-month outputs when avoidable."""
        if len(counts) <= 1:
            return
        if len(set(counts)) > 1:
            return
        total = sum(counts)
        if total <= 1:
            return

        donor = len(counts) - 1
        while donor > 0 and counts[donor] == 0:
            donor -= 1
        if counts[donor] == 0:
            return

        counts[donor] -= 1
        counts[0] += 1

    def _build_policy_targets(self, cfg: dict) -> dict[str, int]:
        total_policies = self._policy_total
        if total_policies <= 0:
            floor = int(cfg.get("floor", 0))
            ceiling = int(cfg.get("ceiling", floor))
            total_policies = max(0, ceiling - floor)

        n = len(self._months)
        if n <= 0:
            return {}

        # Stable monthly policy creation with mild seasonality and small trend.
        # This avoids bell-curve collapse while keeping realistic variation.
        seasonality_amplitude = float(cfg.get("seasonality_amplitude", 0.12))
        seasonality_amplitude = min(max(seasonality_amplitude, 0.0), 0.15)
        seasonality_phase = float(cfg.get("seasonality_phase", 0.0))

        # trend_strength is the total proportional lift from first to last month.
        trend_strength = float(cfg.get("trend_strength", 0.06))
        trend_strength = min(max(trend_strength, 0.0), 0.15)

        idx = np.arange(n, dtype=float)
        trend = 1.0 + trend_strength * (idx / max(1.0, float(n - 1)))

        month_numbers = np.array([m.month for m in self._months], dtype=float)
        # 12-month periodic seasonality; centered around 1.0.
        seasonal = 1.0 + seasonality_amplitude * np.sin(
            (2.0 * np.pi * ((month_numbers - 1.0) / 12.0)) + seasonality_phase
        )

        weights = np.clip(trend * seasonal, 1e-8, None)
        monthly_new = self._allocate_total(total_policies, weights)
        return {self._month_keys[i]: int(monthly_new[i]) for i in range(len(self._month_keys))}

    def _build_staff_targets(self, cfg: dict, ratio_key: str = "") -> dict[str, int]:
        start_count = int(cfg.get("start_count", 0))
        end_count = int(cfg.get("end_count", start_count))

        ratio = float(self._target_scale.get(ratio_key, 0)) if hasattr(self, '_target_scale') else 0.0
        if ratio > 0 and getattr(self, '_policy_total', 0) > 0:
            expected_end = max(1, int(self._policy_total / ratio))
            if end_count > 0:
                scale_factor = expected_end / float(end_count)
                start_count = int(start_count * scale_factor)
                end_count = expected_end

        total_new = max(0, end_count - start_count)

        curve_weights = self._curve_weights(cfg)
        monthly_new = self._allocate_total(total_new, curve_weights)
        return {self._month_keys[i]: int(monthly_new[i]) for i in range(len(self._month_keys))}

    def _target_for(self, month: str | date, series: str) -> int:
        month_key = _parse_month(month).strftime("%Y-%m")
        if month_key not in self._month_lookup:
            raise ValueError(
                f"month '{month_key}' outside configured range "
                f"{self._month_keys[0]}..{self._month_keys[-1]}"
            )

        if series == "policies":
            return self._policy_targets[month_key]
        if series == "brokers":
            return self._broker_targets[month_key]
        if series == "underwriters":
            return self._underwriter_targets[month_key]
        raise ValueError(f"Unknown series '{series}'")

    def get_new_policy_target(self, month: str | date) -> int:
        return self._target_for(month, "policies")

    def get_new_broker_target(self, month: str | date) -> int:
        return self._target_for(month, "brokers")

    def get_new_underwriter_target(self, month: str | date) -> int:
        return self._target_for(month, "underwriters")


_default_engine: Optional[GrowthEngine] = None


def _engine() -> GrowthEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = GrowthEngine()
    return _default_engine


def get_new_policy_target(month: str | date) -> int:
    return _engine().get_new_policy_target(month)


def get_new_broker_target(month: str | date) -> int:
    return _engine().get_new_broker_target(month)


def get_new_underwriter_target(month: str | date) -> int:
    return _engine().get_new_underwriter_target(month)
