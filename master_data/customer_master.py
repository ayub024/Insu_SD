"""Customer master data generation for policy-driven customer creation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np
from faker import Faker

from core.id_factory import IdFactory

_ALLOWED_REGIONS = ["Midwest", "Southeast", "Northeast", "Southwest", "West"]

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

_INDIVIDUAL_AGE_GROUPS = ["18-25", "26-35", "36-45", "46-60", "60+"]
_INDIVIDUAL_AGE_PROBS = np.array([0.16, 0.24, 0.23, 0.24, 0.13], dtype=float)

_GENDERS = ["Female", "Male", "Non-Binary"]
_GENDER_PROBS = np.array([0.49, 0.49, 0.02], dtype=float)

_INCOME_BANDS = ["Low", "Lower-Middle", "Middle", "Upper-Middle", "High"]
_INCOME_PROBS_INDIVIDUAL = np.array([0.15, 0.24, 0.31, 0.20, 0.10], dtype=float)
_INCOME_PROBS_BUSINESS = np.array([0.05, 0.10, 0.25, 0.35, 0.25], dtype=float)


class CustomerMaster:
    """Create Dim_Customer rows on demand from policy context."""

    def __init__(
        self,
        geo_distribution_path: str = "config/geo_distribution.yaml",
        seed: int = 20260215,
    ) -> None:
        self._rng = np.random.default_rng(int(seed))
        self._ids = IdFactory()
        self._faker = Faker()
        self._faker.seed_instance(int(seed))

        self._region_values, self._region_probs = self._load_geo_distribution(geo_distribution_path)

        self._rows: list[dict] = []
        self._stable_customer_map: dict[str, dict] = {}

    def _load_geo_distribution(self, path: str) -> tuple[list[str], np.ndarray]:
        try:
            import yaml
        except ImportError as exc:
            raise ImportError("PyYAML is required to load geo_distribution.yaml") from exc

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Geo distribution config not found: {p}")

        payload = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        geo = payload.get("geo_distribution", {})

        if set(geo.keys()) != set(_ALLOWED_REGIONS):
            raise ValueError(
                "geo_distribution.yaml must contain exactly: " + ", ".join(_ALLOWED_REGIONS)
            )

        total = float(sum(geo.values()))
        if abs(total - 100.0) > 1e-9:
            raise ValueError("geo_distribution percentages must sum to 100")

        values = _ALLOWED_REGIONS
        probs = np.array([float(geo[r]) / 100.0 for r in values], dtype=float)
        return values, probs

    def _resolve_customer_type(self, policy_context: dict[str, Any]) -> str:
        line_of_business = str(policy_context.get("line_of_business", "")).strip()
        if line_of_business.startswith("Personal"):
            return "Individual"
        if line_of_business.startswith("Commercial"):
            return "Business Entity"

        segment = str(policy_context.get("segment", "")).strip()
        if segment == "Retail":
            return "Individual"
        if segment in {"SME", "Enterprise"}:
            return "Business Entity"

        raise ValueError(
            "policy_context must include recognizable 'line_of_business' or 'segment' "
            "to derive customer_type"
        )

    def _resolve_geography(self, policy_context: dict[str, Any]) -> str:
        segment_geo = policy_context.get("segment_geography")
        customer_geo = policy_context.get("geography") or policy_context.get("customer_geography")

        if segment_geo and customer_geo and str(segment_geo) != str(customer_geo):
            raise ValueError("Customer geography must align with segment geography")

        geography = str(segment_geo or customer_geo or "").strip()
        if not geography:
            geography = str(self._rng.choice(self._region_values, p=self._region_probs))

        if geography not in _ALLOWED_REGIONS:
            raise ValueError(f"geography must be one of {_ALLOWED_REGIONS}")

        return geography

    def _build_customer_name(self, customer_type: str, gender: Optional[str] = None) -> str:
        if customer_type == "Individual":
            if gender == "Female":
                return self._faker.name_female()
            elif gender == "Male":
                return self._faker.name_male()
            else:
                return self._faker.name()
        return self._faker.company()

    def _build_customer_row(self, policy_context: dict[str, Any]) -> dict:
        customer_type = self._resolve_customer_type(policy_context)
        geography = self._resolve_geography(policy_context)

        if customer_type == "Individual":
            age_group: Optional[str] = str(self._rng.choice(_INDIVIDUAL_AGE_GROUPS, p=_INDIVIDUAL_AGE_PROBS))
            gender: Optional[str] = str(self._rng.choice(_GENDERS, p=_GENDER_PROBS))
            income_band = str(self._rng.choice(_INCOME_BANDS, p=_INCOME_PROBS_INDIVIDUAL))
            customer_name = self._build_customer_name(customer_type, gender)
        else:
            age_group = None
            gender = None
            income_band = str(self._rng.choice(_INCOME_BANDS, p=_INCOME_PROBS_BUSINESS))
            customer_name = self._build_customer_name(customer_type, None)

        return {
            "customer_key": self._ids.next_customer_key(),
            "customer_name": customer_name,
            "customer_entity_type": customer_type,
            "age_group": age_group,
            "gender": gender,
            "customer_geography": geography,
            "income_band": income_band,
        }

    def create_customer_for_policy(self, policy_context: dict[str, Any]) -> dict:
        """Create (or reuse) one customer row from policy context.

        Required policy_context semantics:
        - line_of_business or segment to derive customer_type
        - segment_geography and/or geography for alignment

        Optional stable reference fields for reuse:
        - customer_ref
        - external_customer_id
        - party_id
        - customer_source_key
        """
        stable_ref = (
            policy_context.get("customer_ref")
            or policy_context.get("external_customer_id")
            or policy_context.get("party_id")
            or policy_context.get("customer_source_key")
        )

        if stable_ref is not None:
            stable_key = str(stable_ref)
            existing = self._stable_customer_map.get(stable_key)
            if existing is not None:
                return existing

            row = self._build_customer_row(policy_context)
            self._stable_customer_map[stable_key] = row
            self._rows.append(row)
            return row

        row = self._build_customer_row(policy_context)
        self._rows.append(row)
        return row


_default_master: Optional[CustomerMaster] = None


def _instance() -> CustomerMaster:
    global _default_master
    if _default_master is None:
        _default_master = CustomerMaster()
    return _default_master


def create_customer_for_policy(policy_context: dict[str, Any]) -> dict:
    """Module-level API for policy-driven customer generation."""
    return _instance().create_customer_for_policy(policy_context)
