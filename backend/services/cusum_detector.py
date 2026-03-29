"""
CUSUM (Cumulative Sum) control chart — per-instrument drift and bias detection.

The CUSUM chart is the industry-standard method for detecting slow, sustained
shifts in a process mean — the primary failure mode for process transmitters
(calibration drift, fouling, reference junction deterioration, etc.).

Algorithm
---------
For each new reading x:
    z   = (x - mu0) / sigma           # standardise against reference baseline
    C⁺  = max(0,  C⁺_prev + z - k)   # upper accumulator (upward drift)
    C⁻  = min(0,  C⁻_prev + z + k)   # lower accumulator (downward drift)

Alert when C⁺ > h (drift_high) or C⁻ < -h (drift_low).

Parameters
----------
mu0   : reference mean  (set from baseline data or nominal value)
sigma : reference std   (estimated from baseline data)
k     : allowable slack (~0.5σ typical)
h     : decision threshold (~5σ typical — roughly 5× the slack)

Both k and h default from settings (cusum_k, cusum_h).

State persistence
-----------------
CUSUM state is saved to disk periodically so drift accumulates correctly across
app restarts. State is reset (C⁺ = C⁻ = 0) when a calibration is recorded,
since the instrument has been verified at that point.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────

@dataclass
class CusumState:
    tag_number:    str   = ""
    mu0:           float = 0.0      # reference mean
    sigma:         float = 1.0      # reference std
    c_pos:         float = 0.0      # upper accumulator C⁺
    c_neg:         float = 0.0      # lower accumulator C⁻
    k:             float = 0.5      # allowable slack
    h:             float = 5.0      # decision threshold
    alert_state:   str   = "normal" # "normal" | "drift_high" | "drift_low"
    sample_count:  int   = 0
    initialized:   bool  = False
    last_saved_at: int   = 0        # sample_count at last save


_states: dict[str, CusumState] = {}
_SAVE_INTERVAL = 100  # persist every N readings


# ── Paths ──────────────────────────────────────────────────────────────────────

def _state_path(tag: str) -> Path:
    safe = tag.replace("/", "_").replace("\\", "_")
    return settings.models_dir / f"cusum_{safe}.json"


# ── Persistence ────────────────────────────────────────────────────────────────

def _save_state(state: CusumState) -> None:
    try:
        _state_path(state.tag_number).write_text(
            json.dumps(asdict(state)), encoding="utf-8"
        )
        state.last_saved_at = state.sample_count
    except OSError as exc:
        logger.warning("CUSUM: could not save state for %s: %s", state.tag_number, exc)


def _load_state(tag: str) -> CusumState | None:
    path = _state_path(tag)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CusumState(**data)
    except Exception as exc:
        logger.warning("CUSUM: could not load state for %s: %s", tag, exc)
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

def get_or_create_state(tag: str) -> CusumState:
    """Return the in-memory state for a tag, loading from disk if needed."""
    if tag not in _states:
        loaded = _load_state(tag)
        if loaded:
            _states[tag] = loaded
        else:
            _states[tag] = CusumState(
                tag_number=tag,
                k=settings.cusum_k,
                h=settings.cusum_h,
            )
    return _states[tag]


def initialize_from_data(
    tag: str,
    readings: list[tuple],
    nominal_value: float | None = None,
) -> None:
    """
    Set CUSUM reference baseline from historical readings.
    Called after ML training so both detectors share the same reference window.
    """
    if not readings or len(readings) < 10:
        return
    import numpy as np
    values = np.array([v for _, v in readings], dtype=float)
    mu0    = float(np.mean(values))
    sigma  = float(np.std(values))
    if sigma < 1e-6:
        sigma = max(abs(mu0) * 0.01, 0.01)

    state = get_or_create_state(tag)
    state.mu0         = nominal_value if nominal_value is not None else mu0
    state.sigma       = sigma
    state.c_pos       = 0.0
    state.c_neg       = 0.0
    state.alert_state = "normal"
    state.initialized = True
    state.sample_count = 0
    _save_state(state)
    logger.info("CUSUM: initialized for %s  mu0=%.4f  sigma=%.4f", tag, state.mu0, state.sigma)


def update(tag: str, value: float, range_min: float | None = None, range_max: float | None = None) -> str:
    """
    Feed one reading to the CUSUM detector.

    Returns the current alert state: "normal" | "drift_high" | "drift_low".
    """
    state = get_or_create_state(tag)

    if not state.initialized:
        # Bootstrap: use the reading itself as a provisional reference.
        # We need sigma > 0; estimate 2% of range span if available, else 2% of value.
        if range_min is not None and range_max is not None and range_max > range_min:
            sigma_est = (range_max - range_min) * 0.02
        else:
            sigma_est = max(abs(value) * 0.02, 0.01)
        state.mu0       = value
        state.sigma     = sigma_est
        state.initialized = True
        state.sample_count += 1
        return "normal"

    # Standardise
    z = (value - state.mu0) / state.sigma if state.sigma > 1e-9 else 0.0

    # Update accumulators
    state.c_pos = max(0.0, state.c_pos + z - state.k)
    state.c_neg = min(0.0, state.c_neg + z + state.k)
    state.sample_count += 1

    # Evaluate threshold
    if state.c_pos > state.h:
        state.alert_state = "drift_high"
    elif state.c_neg < -state.h:
        state.alert_state = "drift_low"
    else:
        state.alert_state = "normal"

    # Periodic persistence
    if state.sample_count - state.last_saved_at >= _SAVE_INTERVAL:
        _save_state(state)

    return state.alert_state


def reset(tag: str) -> None:
    """
    Reset accumulators after a calibration event.
    Keeps the reference mu0/sigma — they remain valid.
    Drift has been corrected; start accumulating fresh.
    """
    state = get_or_create_state(tag)
    state.c_pos       = 0.0
    state.c_neg       = 0.0
    state.alert_state = "normal"
    state.sample_count = 0
    _save_state(state)
    logger.info("CUSUM: reset for %s after calibration", tag)


def get_alert_state(tag: str) -> str:
    """Return current drift alert state without updating."""
    return get_or_create_state(tag).alert_state


def get_accumulators(tag: str) -> tuple[float, float]:
    """Return (C⁺, C⁻) — useful for dashboards and diagnostics."""
    state = get_or_create_state(tag)
    return state.c_pos, state.c_neg


def save_all() -> None:
    """Persist all in-memory states — call on app shutdown."""
    for state in _states.values():
        _save_state(state)
    logger.info("CUSUM: saved %d states", len(_states))
