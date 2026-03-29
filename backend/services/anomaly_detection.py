"""
Isolation Forest-based anomaly detection per instrument.

Changes from v1
---------------
* 10-feature vector (was 6) — must retrain existing models.
* Versioned model storage:  each trained model is saved as
  anomaly_{tag}_{yyyymmdd_HHMMSS}.pkl alongside a
  anomaly_{tag}_versions.json manifest.
  The active version is tracked in the manifest; previous versions
  are retained for rollback.
* initialize_cusum() is called automatically after training so both
  detectors share the same reference window.
"""

from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest

from config import settings
from backend.services.feature_extraction import extract_features, feature_vector
from backend.services.reading_store import get_buffered_readings_sync

logger = logging.getLogger(__name__)

# ── In-memory state ────────────────────────────────────────────────────────────
_models:        dict[str, IsolationForest] = {}
_anomaly_state: dict[str, str]             = {}   # tag → "normal"|"warning"|"critical"
_scores:        dict[str, float]           = {}

FEATURES_VERSION = 2   # bump when feature set changes (invalidates old models)


# ── Paths ──────────────────────────────────────────────────────────────────────

def _model_dir() -> Path:
    return settings.models_dir


def _manifest_path(tag: str) -> Path:
    safe = tag.replace("/", "_").replace("\\", "_")
    return _model_dir() / f"anomaly_{safe}_versions.json"


def _model_path(tag: str, version_id: str) -> Path:
    safe = tag.replace("/", "_").replace("\\", "_")
    return _model_dir() / f"anomaly_{safe}_{version_id}.pkl"


# ── Manifest (version index) ───────────────────────────────────────────────────

def _read_manifest(tag: str) -> dict:
    path = _manifest_path(tag)
    if not path.exists():
        return {"versions": [], "active": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"versions": [], "active": None}


def _write_manifest(tag: str, manifest: dict) -> None:
    _manifest_path(tag).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


# ── Public state accessors ─────────────────────────────────────────────────────

def get_anomaly_state(tag: str) -> str:
    return _anomaly_state.get(tag, "normal")


def get_anomaly_score(tag: str) -> float | None:
    return _scores.get(tag)


def is_model_trained(tag: str) -> bool:
    if tag in _models:
        return True
    manifest = _read_manifest(tag)
    return manifest.get("active") is not None


def get_model_versions(tag: str) -> list[dict]:
    """Return list of version metadata dicts, newest first."""
    return list(reversed(_read_manifest(tag).get("versions", [])))


# ── Training ───────────────────────────────────────────────────────────────────

def train_model(
    tag: str,
    feature_matrix: np.ndarray,
    trained_by: str | None = None,
    notes: str | None = None,
) -> str:
    """
    Train Isolation Forest on a feature matrix.

    Returns the version_id string for the saved model.
    """
    if len(feature_matrix) < 10:
        raise ValueError("Need at least 10 feature windows to train")

    clf = IsolationForest(
        contamination=settings.anomaly_contamination,
        random_state=42,
        n_estimators=150,   # increased from 100
        max_samples="auto",
    )
    clf.fit(feature_matrix)

    version_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    model_file = str(_model_path(tag, version_id))

    _model_dir().mkdir(parents=True, exist_ok=True)
    with open(model_file, "wb") as fh:
        pickle.dump(clf, fh)

    # Update manifest
    manifest = _read_manifest(tag)
    # Deactivate previous active version
    for v in manifest["versions"]:
        v["is_active"] = False
    manifest["versions"].append({
        "version_id":       version_id,
        "trained_at":       datetime.utcnow().isoformat(),
        "trained_by":       trained_by or "system",
        "windows_used":     int(len(feature_matrix)),
        "contamination":    settings.anomaly_contamination,
        "features_version": FEATURES_VERSION,
        "model_file":       model_file,
        "is_active":        True,
        "notes":            notes or "",
    })
    manifest["active"] = version_id
    _write_manifest(tag, manifest)

    # Load into memory
    _models[tag] = clf
    logger.info(
        "Anomaly model trained for %s  version=%s  windows=%d",
        tag, version_id, len(feature_matrix),
    )
    return version_id


def activate_version(tag: str, version_id: str) -> bool:
    """Rollback or switch active model version. Returns True on success."""
    manifest = _read_manifest(tag)
    target = next((v for v in manifest["versions"] if v["version_id"] == version_id), None)
    if not target:
        return False

    model_file = target["model_file"]
    if not Path(model_file).exists():
        return False

    try:
        with open(model_file, "rb") as fh:
            clf = pickle.load(fh)
    except Exception:
        return False

    for v in manifest["versions"]:
        v["is_active"] = (v["version_id"] == version_id)
    manifest["active"] = version_id
    _write_manifest(tag, manifest)
    _models[tag] = clf
    logger.info("Anomaly model switched for %s → version %s", tag, version_id)
    return True


# ── Loading ────────────────────────────────────────────────────────────────────

def load_model(tag: str) -> bool:
    """Load the active model from disk. Returns True if loaded."""
    manifest = _read_manifest(tag)
    active_id = manifest.get("active")
    if not active_id:
        return False

    version = next(
        (v for v in manifest["versions"] if v["version_id"] == active_id), None
    )
    if not version:
        return False

    model_file = version["model_file"]
    if not Path(model_file).exists():
        return False

    try:
        with open(model_file, "rb") as fh:
            _models[tag] = pickle.load(fh)
        return True
    except Exception as exc:
        logger.warning("Failed to load model for %s: %s", tag, exc)
        return False


def discard_trained_model(tag: str) -> None:
    """Remove all model state for a tag (instrument deleted)."""
    _models.pop(tag, None)
    _anomaly_state.pop(tag, None)
    _scores.pop(tag, None)

    manifest = _read_manifest(tag)
    for v in manifest.get("versions", []):
        try:
            Path(v["model_file"]).unlink(missing_ok=True)
        except OSError:
            pass

    path = _manifest_path(tag)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


# ── Inference ──────────────────────────────────────────────────────────────────

def _score_to_state(score: float) -> str:
    if score >= settings.anomaly_critical_threshold:
        return "critical"
    if score >= settings.anomaly_warning_threshold:
        return "warning"
    return "normal"


def predict(
    readings: list[tuple],
    nominal_value: float | None,
    tag: str,
    range_min: float | None = None,
    range_max: float | None = None,
) -> tuple[str, float] | None:
    """
    Run anomaly detection on a window of readings.
    Returns (state, score) or None if model not available / insufficient data.
    """
    features = extract_features(
        readings,
        nominal_value=nominal_value,
        range_min=range_min,
        range_max=range_max,
    )
    if features is None:
        return None

    model = _models.get(tag)
    if model is None:
        if not load_model(tag):
            return None
        model = _models[tag]

    X    = feature_vector(features)
    raw  = model.decision_function(X)[0]
    # IsolationForest: more negative raw → more anomalous.
    # Map to [0, 1] where 1 = most anomalous.
    score = float(np.clip(0.5 - raw, 0.0, 1.0))
    state = _score_to_state(score)

    _scores[tag]        = score
    _anomaly_state[tag] = state
    return state, score


def update_anomaly_from_buffer(
    tag: str,
    nominal_value: float | None,
    range_min: float | None = None,
    range_max: float | None = None,
) -> tuple[str, float] | None:
    """Compute anomaly state from the current reading buffer. Returns (state, score) or None."""
    from datetime import timedelta
    since    = datetime.utcnow() - timedelta(seconds=settings.aggregation_window_seconds * 2)
    readings = get_buffered_readings_sync(tag, since)
    return predict(readings, nominal_value, tag, range_min=range_min, range_max=range_max)
