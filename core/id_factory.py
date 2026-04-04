"""Stable and unique ID generation for synthetic insurance entities."""

from __future__ import annotations

from typing import Any, Optional


class IdFactory:
    """Generate deterministic, prefixed IDs with uniqueness guarantees.

    IDs are unique within a run and stable when using the `get_or_create_*`
    methods with a stable reference key.
    """

    _ENTITY_CONFIG = {
        "policy": {"prefix": "POL", "width": 7},
        "customer": {"prefix": "CUST", "width": 7},
        "broker": {"prefix": "BRK", "width": 7},
        "underwriter": {"prefix": "UW", "width": 7},
        "fact": {"prefix": "CLM", "width": 10},
    }

    def __init__(
        self,
        policy_start: int = 1,
        customer_start: int = 1,
        broker_start: int = 1,
        underwriter_start: int = 1,
        fact_start: int = 1,
    ) -> None:
        self._counters = {
            "policy": int(policy_start),
            "customer": int(customer_start),
            "broker": int(broker_start),
            "underwriter": int(underwriter_start),
            "fact": int(fact_start),
        }

        self._used = {name: set() for name in self._ENTITY_CONFIG}
        self._stable_maps = {name: {} for name in self._ENTITY_CONFIG}

    def _max_for(self, entity: str) -> int:
        width = self._ENTITY_CONFIG[entity]["width"]
        return (10**width) - 1

    def _format_id(self, entity: str, number: int) -> str:
        config = self._ENTITY_CONFIG[entity]
        return f"{config['prefix']}{number:0{config['width']}d}"

    def _next_number(self, entity: str) -> int:
        next_value = self._counters[entity]
        if next_value > self._max_for(entity):
            raise ValueError(
                f"{entity} ID capacity exceeded ({self._max_for(entity)} max for configured width)"
            )
        self._counters[entity] += 1
        return next_value

    def _next_id(self, entity: str) -> str:
        while True:
            candidate = self._format_id(entity, self._next_number(entity))
            if candidate not in self._used[entity]:
                self._used[entity].add(candidate)
                return candidate

    def _get_or_create(self, entity: str, stable_ref: Any) -> str:
        key = str(stable_ref)
        existing = self._stable_maps[entity].get(key)
        if existing is not None:
            return existing
        new_id = self._next_id(entity)
        self._stable_maps[entity][key] = new_id
        return new_id

    def next_policy_key(self) -> str:
        return self._next_id("policy")

    def next_customer_key(self) -> str:
        return self._next_id("customer")

    def next_broker_key(self) -> str:
        return self._next_id("broker")

    def next_underwriter_key(self) -> str:
        return self._next_id("underwriter")

    def next_fact_id(self, numeric: bool = False) -> str | int:
        number = self._next_number("fact")
        fact_id = self._format_id("fact", number)
        self._used["fact"].add(fact_id)
        return number if numeric else fact_id

    def get_or_create_policy_key(self, stable_ref: Any) -> str:
        return self._get_or_create("policy", stable_ref)

    def get_or_create_customer_key(self, stable_ref: Any) -> str:
        return self._get_or_create("customer", stable_ref)

    def get_or_create_broker_key(self, stable_ref: Any) -> str:
        return self._get_or_create("broker", stable_ref)

    def get_or_create_underwriter_key(self, stable_ref: Any) -> str:
        return self._get_or_create("underwriter", stable_ref)

    def get_or_create_fact_id(self, stable_ref: Any, numeric: bool = False) -> str | int:
        fact_id = self._get_or_create("fact", stable_ref)
        if not numeric:
            return fact_id
        return int(fact_id.replace(self._ENTITY_CONFIG["fact"]["prefix"], "", 1))

    def reserve_id(self, entity: str, id_value: str) -> None:
        """Reserve an externally generated ID to keep future IDs unique."""
        if entity not in self._ENTITY_CONFIG:
            raise ValueError(f"Unknown entity '{entity}'")
        self._used[entity].add(id_value)

    def peek_next(self, entity: str) -> Optional[str]:
        """Inspect the next generated ID for an entity without consuming it."""
        if entity not in self._ENTITY_CONFIG:
            raise ValueError(f"Unknown entity '{entity}'")
        if self._counters[entity] > self._max_for(entity):
            return None
        return self._format_id(entity, self._counters[entity])
