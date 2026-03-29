"""Feature extraction from time-windowed instrument readings."""
import numpy as np
from datetime import datetime


def extract_features(
    readings: list[tuple[datetime, float]],
    nominal_value: float | None = None,
    range_min: float | None = None,
    range_max: float | None = None,
) -> dict[str, float] | None:
    """
    Compute statistical features for a single time window.

    Returns a dict with 10 features, or None if insufficient data.

    Features
    --------
    mean, std, min, max                 — basic statistics
    rate_of_change                      — (last-first) / time_span per second
    deviation_from_nominal              — |mean - nominal|
    trend_slope                         — linear regression slope (units/second)
    autocorrelation_lag1                — Pearson autocorrelation at lag 1
    coeff_of_variation                  — std / |mean| (normalised noise)
    range_utilisation                   — (mean - range_min) / span (0–1 position)
    """
    if not readings:
        return None

    values = np.array([v for _, v in readings], dtype=float)
    times  = np.array([t.timestamp() for t, _ in readings])
    n = len(values)

    mean    = float(np.mean(values))
    std     = float(np.std(values))        if n > 1 else 0.0
    min_val = float(np.min(values))
    max_val = float(np.max(values))

    # Rate of change: (last - first) / elapsed seconds
    if n >= 2 and times[-1] > times[0]:
        rate = float((values[-1] - values[0]) / (times[-1] - times[0]))
    else:
        rate = 0.0

    # Deviation from nominal
    deviation = float(np.abs(mean - nominal_value)) if nominal_value is not None else 0.0

    # Trend slope via linear regression
    if n >= 3:
        t_norm = times - times[0]
        slope, _ = np.polyfit(t_norm, values, 1)
        trend_slope = float(slope)
    else:
        trend_slope = rate  # fall back to simple rate

    # Autocorrelation at lag 1
    if n >= 3 and std > 0:
        v_norm = values - mean
        acf = float(np.dot(v_norm[:-1], v_norm[1:]) / ((n - 1) * std ** 2))
        acf = float(np.clip(acf, -1.0, 1.0))
    else:
        acf = 0.0

    # Coefficient of variation (normalised noise)
    cov = float(std / abs(mean)) if abs(mean) > 1e-9 else 0.0
    cov = float(np.clip(cov, 0.0, 10.0))  # cap at 10× to avoid inf

    # Range utilisation — position within operating band (0 = min, 1 = max)
    if range_min is not None and range_max is not None:
        span = range_max - range_min
        ru = float((mean - range_min) / span) if span > 0 else 0.5
        ru = float(np.clip(ru, -0.5, 1.5))  # allow slight out-of-range
    else:
        ru = 0.5  # unknown — use midpoint

    return {
        "mean":                  mean,
        "std":                   std,
        "min":                   min_val,
        "max":                   max_val,
        "rate_of_change":        rate,
        "deviation_from_nominal": deviation,
        "trend_slope":           trend_slope,
        "autocorrelation_lag1":  acf,
        "coeff_of_variation":    cov,
        "range_utilisation":     ru,
    }


def feature_vector(features: dict[str, float]) -> np.ndarray:
    """Ordered feature vector for ML model — order MUST match training order."""
    return np.array([
        features["mean"],
        features["std"],
        features["min"],
        features["max"],
        features["rate_of_change"],
        features["deviation_from_nominal"],
        features["trend_slope"],
        features["autocorrelation_lag1"],
        features["coeff_of_variation"],
        features["range_utilisation"],
    ], dtype=np.float64).reshape(1, -1)
