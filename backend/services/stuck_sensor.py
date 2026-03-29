"""
Stuck sensor detection.

A 'stuck' sensor is one whose output has not changed beyond a minimum deadband
for longer than a configurable time window.  Common causes:

  - Thermocouple or RTD burnout (output freezes at last valid value or fails to rail)
  - Plugged impulse line on a pressure/DP transmitter
  - Transmitter power failure with output held at last value
  - Frozen SCADA/DCS value (communications lost, stale value forwarded)

The detector tracks the last *distinct* reading per tag.  A reading counts as
distinct if it differs from the previous distinct value by more than the
configured deadband (default: 0.1 % of instrument span).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from config import settings

logger = logging.getLogger(__name__)

_DEADBAND_FRACTION = 0.001   # 0.1 % of span


@dataclass
class StuckState:
    last_distinct_value: float | None  = None
    last_distinct_time:  datetime | None = None
    is_stuck:            bool            = False


_states: dict[str, StuckState] = {}


def check(tag: str, value: float, range_span: float) -> bool:
    """
    Update stuck-sensor state for one new reading.

    Parameters
    ----------
    tag        : instrument tag number
    value      : current reading in engineering units
    range_span : instrument full range (range_max - range_min); used for deadband

    Returns
    -------
    True  if the sensor is currently flagged as stuck.
    False otherwise.
    """
    state = _states.setdefault(tag, StuckState())
    now   = datetime.utcnow()
    dead  = max(range_span * _DEADBAND_FRACTION, 1e-6)

    if state.last_distinct_value is None:
        state.last_distinct_value = value
        state.last_distinct_time  = now
        state.is_stuck = False
        return False

    if abs(value - state.last_distinct_value) > dead:
        # Value moved — reset the stuck timer
        state.last_distinct_value = value
        state.last_distinct_time  = now
        if state.is_stuck:
            logger.info("Stuck sensor cleared: %s  new_value=%.4f", tag, value)
        state.is_stuck = False
        return False

    # Value has not changed enough — check elapsed time
    elapsed_min = (now - state.last_distinct_time).total_seconds() / 60.0
    was_stuck   = state.is_stuck
    state.is_stuck = elapsed_min >= settings.stuck_sensor_minutes

    if state.is_stuck and not was_stuck:
        logger.warning(
            "Stuck sensor detected: %s  unchanged_for=%.1f min  value=%.4f",
            tag, elapsed_min, value,
        )

    return state.is_stuck


def get_state(tag: str) -> bool:
    """Return current stuck state without updating (True = stuck)."""
    return _states.get(tag, StuckState()).is_stuck


def reset(tag: str) -> None:
    """Clear stuck state after maintenance or instrument replacement."""
    _states.pop(tag, None)
    logger.info("Stuck sensor state cleared for %s", tag)
