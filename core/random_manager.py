"""Deterministic random manager for reproducible synthetic data generation."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Optional, Sequence

import numpy as np


class RandomManager:
    """Centralized RNG manager with global and per-entity deterministic streams."""

    def __init__(self, global_seed: int) -> None:
        self.global_seed = int(global_seed)
        self._global_rng = np.random.default_rng(self.global_seed)

    def _derive_entity_seed(self, entity_seed: Any) -> int:
        """Create a stable 64-bit seed from the global seed and an entity identifier."""
        payload = f"{self.global_seed}|{entity_seed}".encode("utf-8")
        digest = hashlib.sha256(payload).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=False)

    def _rng(self, entity_seed: Optional[Any] = None) -> np.random.Generator:
        """Return the global RNG or a deterministic per-entity RNG."""
        if entity_seed is None:
            return self._global_rng
        return np.random.default_rng(self._derive_entity_seed(entity_seed))

    def randint(
        self,
        low: int,
        high: Optional[int] = None,
        size: Optional[int] = None,
        entity_seed: Optional[Any] = None,
    ) -> np.ndarray | int:
        """Integer samples from [low, high) if high is set, else [0, low)."""
        return self._rng(entity_seed).integers(low=low, high=high, size=size)

    def uniform(
        self,
        low: float = 0.0,
        high: float = 1.0,
        size: Optional[int] = None,
        entity_seed: Optional[Any] = None,
    ) -> np.ndarray | float:
        """Uniform samples from [low, high)."""
        return self._rng(entity_seed).uniform(low=low, high=high, size=size)

    def choice_weighted(
        self,
        values: Sequence[Any],
        weights: Sequence[float],
        size: Optional[int] = None,
        replace: bool = True,
        entity_seed: Optional[Any] = None,
    ) -> Any:
        """Weighted choice helper with probability normalization."""
        if not values:
            raise ValueError("values must not be empty")
        if len(values) != len(weights):
            raise ValueError("values and weights must have the same length")

        probs = np.asarray(weights, dtype=float)
        if np.any(probs < 0):
            raise ValueError("weights must be non-negative")

        total = probs.sum()
        if total <= 0:
            raise ValueError("weights must sum to a positive value")

        probs = probs / total
        return self._rng(entity_seed).choice(values, size=size, replace=replace, p=probs)

    def normal(
        self,
        mean: float = 0.0,
        stddev: float = 1.0,
        size: Optional[int] = None,
        entity_seed: Optional[Any] = None,
    ) -> np.ndarray | float:
        """Normal distribution samples."""
        return self._rng(entity_seed).normal(loc=mean, scale=stddev, size=size)

    def lognormal(
        self,
        mean: float = 0.0,
        sigma: float = 1.0,
        size: Optional[int] = None,
        entity_seed: Optional[Any] = None,
    ) -> np.ndarray | float:
        """Lognormal distribution samples."""
        return self._rng(entity_seed).lognormal(mean=mean, sigma=sigma, size=size)

    def shuffle(self, values: Iterable[Any], entity_seed: Optional[Any] = None) -> list[Any]:
        """Return a shuffled list while preserving deterministic seeding behavior."""
        result = list(values)
        self._rng(entity_seed).shuffle(result)
        return result
