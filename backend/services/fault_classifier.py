"""
Two-tier fault classification for CBU CMMS.

Tier 1 — Rule-based (always available, zero training cost):
    Examines the feature vector and detector states to identify fault type
    from known statistical signatures.

Tier 2 — Supervised Random Forest (trained on labelled simulator data):
    A RandomForestClassifier trained on synthetic labelled windows generated
    by the realistic process simulator.  When trained and confident (≥ 70 %),
    this overrides the rule-based result.

Fault labels (shared with simulator constants):
    none        — normal operation
    stuck       — reading frozen / not changing
    spike       — single-sample outlier
    drift_high  — slow upward calibration drift
    drift_low   — slow downward calibration drift
    noisy       — elevated measurement noise
"""
from __future__ import annotations

import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Fault label constants (match simulate_mqtt.py)
FAULT_NONE       = "none"
FAULT_STUCK      = "stuck"
FAULT_SPIKE      = "spike"
FAULT_DRIFT_HIGH = "drift_high"
FAULT_DRIFT_LOW  = "drift_low"
FAULT_NOISY      = "noisy"

ALL_FAULTS = [FAULT_NONE, FAULT_STUCK, FAULT_SPIKE,
              FAULT_DRIFT_HIGH, FAULT_DRIFT_LOW, FAULT_NOISY]

# In-memory cache of trained classifiers  {tag: RandomForestClassifier}
_classifiers: dict = {}

# Human-readable fault descriptions shown in the UI
FAULT_DESCRIPTIONS = {
    FAULT_NONE:       "Normal operation",
    FAULT_STUCK:      "Stuck sensor — reading not changing",
    FAULT_SPIKE:      "Signal spike — single-sample outlier",
    FAULT_DRIFT_HIGH: "Upward calibration drift",
    FAULT_DRIFT_LOW:  "Downward calibration drift",
    FAULT_NOISY:      "Elevated measurement noise",
}


# ---------------------------------------------------------------------------
# Tier 1 — Rule-based classifier
# ---------------------------------------------------------------------------

def classify_rule_based(
    features: dict[str, float],
    fault_features: dict[str, float],
    cusum_state: str,
    stuck: bool,
    cusum_sigma: float,
) -> tuple[str, float]:
    """
    Return (fault_type, confidence) using statistical signature rules.

    Rules are ordered from most to least specific.  cusum_sigma is the
    reference std stored in the CUSUM state — used to normalise thresholds
    so the same rules apply regardless of instrument span.
    """
    std          = features.get("std", 0.0)
    max_run      = fault_features.get("max_run_identical", 1.0)
    max_step     = fault_features.get("max_adjacent_step", 0.0)
    step_to_std  = fault_features.get("step_to_std_ratio", 0.0)
    sigma        = cusum_sigma if cusum_sigma > 1e-9 else max(std, 1e-9)

    # ── Stuck sensor ─────────────────────────────────────────────────────────
    # Hard path: stuck_sensor detector already fired
    if stuck:
        return FAULT_STUCK, 0.95
    # Soft path: std near zero OR long run of identical values in this window
    if std < sigma * 0.08 or max_run >= 8:
        return FAULT_STUCK, 0.80

    # ── Spike ─────────────────────────────────────────────────────────────────
    # A spike causes a very large single step relative to the window's own std.
    # step_to_std > 5 means the jump is 5× the window noise — very distinctive.
    if step_to_std > 5.0 and max_step > sigma * 3.0:
        return FAULT_SPIKE, 0.85

    # ── Elevated noise ────────────────────────────────────────────────────────
    # std is much larger than the reference baseline but no large single step
    if std > sigma * 2.5 and step_to_std < 4.0:
        return FAULT_NOISY, 0.75

    # ── Calibration drift (direction from CUSUM) ──────────────────────────────
    if cusum_state == "drift_high":
        return FAULT_DRIFT_HIGH, 0.85
    if cusum_state == "drift_low":
        return FAULT_DRIFT_LOW, 0.85

    # ── Trend-based drift (CUSUM not yet triggered but slope is sustained) ────
    slope = features.get("trend_slope", 0.0)
    if slope > sigma * 0.05:
        return FAULT_DRIFT_HIGH, 0.55
    if slope < -sigma * 0.05:
        return FAULT_DRIFT_LOW, 0.55

    return FAULT_NONE, 0.90


# ---------------------------------------------------------------------------
# Tier 2 — Supervised Random Forest
# ---------------------------------------------------------------------------

def _model_path(tag: str) -> Path:
    from config import settings
    return settings.models_dir / f"fault_clf_{tag.replace('/', '-')}.pkl"


def _load_classifier(tag: str):
    """Load classifier from disk into cache.  Returns None if not found."""
    path = _model_path(tag)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            clf = pickle.load(f)
        _classifiers[tag] = clf
        logger.info("Fault classifier loaded for %s", tag)
        return clf
    except Exception as e:
        logger.warning("Could not load fault classifier for %s: %s", tag, e)
        return None


def _save_classifier(tag: str, clf) -> None:
    path = _model_path(tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(clf, f)
    logger.info("Fault classifier saved → %s", path)


def is_fault_classifier_trained(tag: str) -> bool:
    if tag in _classifiers:
        return True
    return _model_path(tag).exists()


def classify_supervised(tag: str, X) -> tuple[Optional[str], float]:
    """Return (fault_type, confidence) using the supervised model, or (None, 0)."""
    clf = _classifiers.get(tag) or _load_classifier(tag)
    if clf is None:
        return None, 0.0
    try:
        proba = clf.predict_proba(X)[0]
        import numpy as np
        idx   = int(np.argmax(proba))
        return str(clf.classes_[idx]), float(proba[idx])
    except Exception as e:
        logger.warning("Supervised fault classify error for %s: %s", tag, e)
        return None, 0.0


# ---------------------------------------------------------------------------
# Combined classifier
# ---------------------------------------------------------------------------

def classify(
    tag: str,
    features: dict[str, float],
    fault_features: dict[str, float],
    cusum_state: str,
    stuck: bool,
    cusum_sigma: float,
    feature_array,   # np.ndarray shape (1, 14)
) -> tuple[str, float, str]:
    """
    Return (fault_type, confidence, method).

    method is 'supervised' when the trained model is used and confident,
    otherwise 'rule'.
    """
    sup_label, sup_conf = classify_supervised(tag, feature_array)
    if sup_label is not None and sup_conf >= 0.70:
        return sup_label, sup_conf, "supervised"

    rule_label, rule_conf = classify_rule_based(
        features, fault_features, cusum_state, stuck, cusum_sigma
    )
    return rule_label, rule_conf, "rule"


# ---------------------------------------------------------------------------
# Training — generate synthetic labelled data and fit the classifier
# ---------------------------------------------------------------------------

_WINDOW_SIZE = 20   # readings per feature window (matches ~40s at 2s sample rate)
_WINDOWS_PER_CLASS = 300   # balanced: 300 windows × 6 classes = 1800 total


def _generate_training_data(
    rmin: float, rmax: float, nominal: float,
) -> tuple:
    """
    Generate balanced labelled feature windows using the simulator process model.
    For each fault class, the fault is forced on for the duration of each window
    so every window has a clean, unambiguous label.
    """
    import numpy as np
    from scripts.simulate_mqtt import (
        InstrumentState, step_instrument,
        FAULT_NONE, FAULT_STUCK, FAULT_SPIKE,
        FAULT_DRIFT_HIGH, FAULT_DRIFT_LOW, FAULT_NOISY,
    )
    from backend.services.feature_extraction import extract_features, extract_fault_features

    fault_classes = [
        FAULT_NONE, FAULT_STUCK, FAULT_SPIKE,
        FAULT_DRIFT_HIGH, FAULT_DRIFT_LOW, FAULT_NOISY,
    ]

    X_rows, y_rows = [], []

    for fault_label in fault_classes:
        state = InstrumentState(
            tag="train", rmin=rmin, rmax=rmax, nominal=nominal, unit=""
        )
        # Warm up — let the process settle
        for i in range(80):
            step_instrument(state, i)

        sample_idx = 80
        windows_collected = 0

        while windows_collected < _WINDOWS_PER_CLASS:
            buffer = []

            # Force fault on for the whole window
            if fault_label == FAULT_NONE:
                state.fault = FAULT_NONE
            elif fault_label == FAULT_STUCK:
                state.fault = FAULT_STUCK
                state.stuck_value = state.pv
                state.fault_timer = _WINDOW_SIZE + 5
            elif fault_label == FAULT_SPIKE:
                # Spike lasts 1 sample — inject it mid-window
                state.fault = FAULT_NONE
            elif fault_label == FAULT_DRIFT_HIGH:
                state.fault = FAULT_DRIFT_HIGH
                state.fault_timer = _WINDOW_SIZE + 5
            elif fault_label == FAULT_DRIFT_LOW:
                state.fault = FAULT_DRIFT_LOW
                state.fault_timer = _WINDOW_SIZE + 5
            elif fault_label == FAULT_NOISY:
                state.fault = FAULT_NOISY
                state.fault_timer = _WINDOW_SIZE + 5

            for j in range(_WINDOW_SIZE):
                # For spike, inject on the middle sample
                if fault_label == FAULT_SPIKE and j == _WINDOW_SIZE // 2:
                    state.fault = FAULT_SPIKE
                    state.fault_timer = 1
                value = step_instrument(state, sample_idx)
                ts = datetime(2024, 1, 1) + timedelta(seconds=sample_idx * 2)
                buffer.append((ts, value))
                sample_idx += 1

            feats = extract_features(buffer, nominal, rmin, rmax)
            ffeats = extract_fault_features(buffer)
            if feats and ffeats:
                row = list(feats.values()) + list(ffeats.values())
                X_rows.append(row)
                y_rows.append(fault_label)
                windows_collected += 1

    return np.array(X_rows, dtype=float), y_rows


def train_fault_classifier(
    tag: str,
    rmin: float,
    rmax: float,
    nominal: float,
) -> dict:
    """
    Generate synthetic training data and fit a RandomForestClassifier.
    Returns a summary dict: {tag, classes, n_samples, feature_importances}.
    """
    from sklearn.ensemble import RandomForestClassifier
    import numpy as np

    logger.info("Generating fault training data for %s …", tag)
    X, y = _generate_training_data(rmin, rmax, nominal)

    clf = RandomForestClassifier(
        n_estimators=150,
        max_depth=12,
        class_weight="balanced",   # handles any residual class imbalance
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X, y)
    _classifiers[tag] = clf
    _save_classifier(tag, clf)

    importances = dict(zip(
        [
            "mean", "std", "min", "max", "rate_of_change",
            "deviation_from_nominal", "trend_slope", "autocorrelation_lag1",
            "coeff_of_variation", "range_utilisation",
            "max_adjacent_step", "mean_adjacent_step",
            "max_run_identical", "step_to_std_ratio",
        ],
        [round(float(v), 4) for v in clf.feature_importances_],
    ))

    classes = list(clf.classes_)
    logger.info("Fault classifier trained for %s — classes: %s", tag, classes)

    return {
        "tag":                 tag,
        "classes":             classes,
        "n_training_samples":  len(X),
        "feature_importances": importances,
    }
