"""
Rule-based checks: threshold and range validation.

Alarm management improvements (ISA-18.2 alignment):

  Deadband  — the alarm only activates when the value has moved beyond
              (range_limit ± deadband).  This prevents nuisance alarms
              from values that just graze the range limit.

  Time delay — the condition must persist for at least
               settings.alarm_time_delay_seconds before the alarm is
               raised.  Transient spikes do not cause alarms.

  Hysteresis — once in normal state the value must return inside the
               range (not just inside the deadband) to stay clear.
               This is implicit: deadband only applies to the trigger
               direction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from backend.models import Instrument
from config import settings

logger = logging.getLogger(__name__)

# Per-tag violation onset tracking  { tag: (raw_status, onset_datetime) }
_onset: dict[str, tuple[str, datetime]] = {}


def _raw_status(value: float, instrument: Instrument) -> str:
    """
    Immediate rule status with deadband applied.

    Returns "critical", "warning", or "normal".
    Normal is returned as soon as the value is back inside the
    range boundaries (no deadband on the return path — hysteresis
    is already provided by the forward deadband).
    """
    rmin  = instrument.range_min
    rmax  = instrument.range_max
    span  = rmax - rmin
    if span <= 0:
        return "normal"

    deadband        = (settings.alarm_deadband_pct / 100.0) * span
    critical_margin = 0.05 * span   # 5 % beyond range = critical

    if value < rmin - critical_margin or value > rmax + critical_margin:
        return "critical"
    if value < rmin - deadband or value > rmax + deadband:
        return "warning"
    # Value is within the deadband or inside the range — normal
    return "normal"


def rule_based_status(value: float | None, instrument: Instrument) -> str:
    """
    Return time-delayed, deadband-filtered alarm status.

    "normal"   — value is within acceptable range
    "warning"  — persistent minor exceedance (> deadband, time-confirmed)
    "critical" — persistent major exceedance (> 5 % outside range, time-confirmed)
    """
    if value is None:
        return "normal"

    raw = _raw_status(value, instrument)
    tag = instrument.tag_number

    if raw == "normal":
        # Clear any pending onset — value is back in range
        _onset.pop(tag, None)
        return "normal"

    # Violation detected — apply time delay
    now   = datetime.utcnow()
    delay = timedelta(seconds=settings.alarm_time_delay_seconds)
    prev  = _onset.get(tag)

    if prev is None:
        # First detection: start the clock
        _onset[tag] = (raw, now)
        return "normal"

    prev_status, onset_time = prev
    if prev_status != raw:
        # Status changed (e.g. warning → critical): restart the clock
        _onset[tag] = (raw, now)
        return "normal"

    # Same raw status — check if delay has elapsed
    if (now - onset_time) >= delay:
        return raw

    return "normal"


def signal_range_check(value: float, instrument: Instrument) -> bool:
    """
    Return True if the value is physically impossible given the instrument range.

    A value more than 20 % of span beyond the configured range indicates a
    hardware fault (broken wire, short circuit, failed transmitter) rather
    than a process exceedance.  This triggers an 'instrument_fault' alert,
    which is kept separate from process alarms.
    """
    span        = instrument.range_max - instrument.range_min
    hard_margin = 0.20 * span
    return (
        value < instrument.range_min - hard_margin
        or value > instrument.range_max + hard_margin
    )


def clear_onset(tag: str) -> None:
    """Remove onset record for a tag (e.g. after instrument is deleted or suppressed)."""
    _onset.pop(tag, None)
