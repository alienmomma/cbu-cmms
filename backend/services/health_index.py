"""Compute a 0–100 numerical health index for an instrument."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.models import Instrument
from backend.services.rules import rule_based_status
from config import settings


def _calibration_penalty(next_due: Optional[datetime]) -> tuple[float, Optional[str]]:
    if not next_due:
        return 5.0, "No calibration history - schedule initial calibration."
    delta_days = (next_due - datetime.utcnow()).total_seconds() / 86400.0
    if delta_days < 0:
        return 25.0, "Calibration overdue - schedule immediately."
    if delta_days <= 7:
        return 10.0, "Calibration due within 7 days - plan now."
    return 0.0, None


def _rule_penalty(rule_status: str) -> tuple[float, Optional[str]]:
    if rule_status == "critical":
        return 40.0, "Measurement outside safe operating range."
    if rule_status == "warning":
        return 20.0, "Measurement approaching range limits."
    return 0.0, None


def _ml_penalty(score: Optional[float]) -> tuple[float, Optional[str]]:
    if score is None:
        return 0.0, None
    if score >= settings.anomaly_critical_threshold:
        return 35.0, "Strong statistical anomaly detected by ML model."
    if score >= settings.anomaly_warning_threshold:
        return 20.0, "Unusual behaviour detected by ML model."
    return float(10.0 * score), None


def _cusum_penalty(cusum_state: str) -> tuple[float, Optional[str]]:
    """Drift is a maintenance concern but not an immediate safety issue."""
    if cusum_state in ("drift_high", "drift_low"):
        direction = "upward" if cusum_state == "drift_high" else "downward"
        return 15.0, f"Sustained {direction} drift detected — calibration check recommended."
    return 0.0, None


def _stuck_penalty(is_stuck: bool) -> tuple[float, Optional[str]]:
    if is_stuck:
        return 30.0, "Sensor output is not changing — possible hardware fault."
    return 0.0, None


def _label_from_health(health: float) -> str:
    if health >= 90: return "excellent"
    if health >= 80: return "good"
    if health >= 65: return "fair"
    if health >= 45: return "attention"
    if health >= 25: return "poor"
    return "critical"


def compute_health_index(
    instrument: Instrument,
    last_value:   Optional[float],
    anomaly_score: Optional[float],
    next_cal_due:  Optional[datetime],
    cusum_state:   str = "normal",
    is_stuck:      bool = False,
) -> tuple[float, str, Optional[str]]:
    """
    Aggregate all detection layers into a 0–100 health score.

    Penalty components (max 100):
      Calibration overdue   25
      Rule violation        40
      ML anomaly            35
      CUSUM drift           15
      Stuck sensor          30

    The worst-case total exceeds 100; the result is clamped to [0, 100].
    """
    rule_status = (
        rule_based_status(last_value, instrument)
        if last_value is not None else "normal"
    )

    cal_pen,   cal_msg   = _calibration_penalty(next_cal_due)
    rule_pen,  rule_msg  = _rule_penalty(rule_status)
    ml_pen,    ml_msg    = _ml_penalty(anomaly_score)
    cusum_pen, cusum_msg = _cusum_penalty(cusum_state)
    stuck_pen, stuck_msg = _stuck_penalty(is_stuck)

    total_penalty = cal_pen + rule_pen + ml_pen + cusum_pen + stuck_pen
    health = max(0.0, min(100.0, 100.0 - total_penalty))
    label  = _label_from_health(health)

    # Return the most severe recommendation
    recommendation = (
        stuck_msg or rule_msg or cal_msg or cusum_msg or ml_msg
    )

    return health, label, recommendation
