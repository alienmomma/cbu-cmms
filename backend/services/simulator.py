"""In-process MQTT simulator: start/stop from API or env."""
import threading
from scripts.simulate_mqtt import run_simulator as _run_simulator

_stop_event: threading.Event | None = None
_thread: threading.Thread | None = None
_current_tags: dict | None = None  # tag -> (rmin, rmax, nominal, unit)


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def get_tags() -> dict | None:
    """Return the tags dict currently being simulated, or None if not running."""
    return _current_tags if is_running() else None


def start(tags: dict | None = None) -> bool:
    """Start simulator in background. Returns True if started.

    Args:
        tags: dict mapping tag_number -> (range_min, range_max, nominal_value, unit).
              If None, the simulator falls back to its hardcoded defaults.
    """
    global _stop_event, _thread, _current_tags
    if is_running():
        return False
    _stop_event = threading.Event()
    _current_tags = tags
    _thread = threading.Thread(
        target=_run_simulator,
        args=(_stop_event, tags),
        daemon=True,
    )
    _thread.start()
    return True


def stop() -> bool:
    """Stop simulator. Returns True if was running."""
    global _stop_event, _thread, _current_tags
    if not is_running():
        return False
    _stop_event.set()
    _thread.join(timeout=5)
    _thread = None
    _stop_event = None
    _current_tags = None
    return True
