"""
Realistic MQTT instrument simulator for CBU CMMS.

Each instrument runs an independent process model with:
  - Correlated (low-pass filtered) noise  — adjacent readings are smooth
  - Slow calibration drift                — random walk over hours
  - Scheduled process setpoint changes    — step and ramp load changes
  - Fault injection                       — stuck sensor, spike, span error
  - Per-type process behaviour            — pressure cycles, temperature lag, etc.

This gives the ML and CUSUM detectors something genuinely challenging to
train on and validate against, matching real plant conditions.
"""
from __future__ import annotations

import json
import math
import random
import time
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paho.mqtt.client as mqtt
from config import settings

# ---------------------------------------------------------------------------
# Default instrument definitions (used when no DB tags are supplied)
# tag -> (range_min, range_max, nominal_value, unit)
# ---------------------------------------------------------------------------
DEFAULT_TAGS = {
    "PT-101": (0.0,  10.0,  7.5,  "bar"),
    "TT-201": (-20.0, 120.0, 80.0, "degC"),
    "FT-301": (0.0,  100.0, 50.0, "m3/h"),
    "LT-401": (0.0,  100.0, 60.0, "%"),
}

# ---------------------------------------------------------------------------
# Fault modes
# ---------------------------------------------------------------------------
FAULT_NONE        = "none"
FAULT_STUCK       = "stuck"       # reading freezes
FAULT_SPIKE       = "spike"       # single-sample outlier, then recovers
FAULT_DRIFT_HIGH  = "drift_high"  # accelerated upward span drift
FAULT_DRIFT_LOW   = "drift_low"   # accelerated downward span drift
FAULT_NOISY       = "noisy"       # elevated noise (e.g. impulse line blockage)


@dataclass
class InstrumentState:
    tag:          str
    rmin:         float
    rmax:         float
    nominal:      float
    unit:         str

    # Process state
    setpoint:     float = 0.0     # current target process value
    pv:           float = 0.0     # current process variable (before noise)
    noise_state:  float = 0.0     # exponential filter state for correlated noise

    # Slow calibration drift (random walk, resets on calibration)
    drift:        float = 0.0     # accumulated span drift in engineering units

    # Fault injection
    fault:        str   = FAULT_NONE
    fault_timer:  int   = 0       # countdown in samples until fault clears
    stuck_value:  float = 0.0

    # Setpoint change schedule
    next_step_in: int   = 0       # samples until next setpoint step
    ramp_active:  bool  = False
    ramp_target:  float = 0.0
    ramp_rate:    float = 0.0     # units per sample

    def __post_init__(self):
        span = self.rmax - self.rmin
        # Start at a random position within ±20% of nominal
        self.setpoint = self.nominal + random.gauss(0, span * 0.05)
        self.pv       = self.setpoint
        self.noise_state = self.setpoint
        self.next_step_in = random.randint(60, 300)   # first step in 2–10 min


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _next_fault(state: InstrumentState) -> None:
    """Randomly inject a fault with low probability."""
    r = random.random()
    span = state.rmax - state.rmin
    if r < 0.0002:                          # ~0.02% per sample = rare
        state.fault = FAULT_STUCK
        state.stuck_value = state.pv
        state.fault_timer = random.randint(30, 180)   # stuck for 1–6 min
    elif r < 0.0005:
        state.fault = FAULT_SPIKE
        state.fault_timer = 1                          # one-sample spike
    elif r < 0.0008:
        state.fault = FAULT_DRIFT_HIGH
        state.fault_timer = random.randint(120, 600)  # accelerated drift 4–20 min
    elif r < 0.0011:
        state.fault = FAULT_DRIFT_LOW
        state.fault_timer = random.randint(120, 600)
    elif r < 0.0014:
        state.fault = FAULT_NOISY
        state.fault_timer = random.randint(60, 300)


def step_instrument(state: InstrumentState, sample_idx: int) -> float:
    """Advance the process model by one sample and return the simulated reading."""
    span   = state.rmax - state.rmin
    tau    = 8.0     # first-order lag time constant (samples) — process inertia
    alpha  = 1.0 - math.exp(-1.0 / tau)   # smoothing factor

    # ── 1. Setpoint / process changes ────────────────────────────────────────
    state.next_step_in -= 1
    if state.next_step_in <= 0:
        if random.random() < 0.6:
            # Step change: ±5–25% of span from current setpoint
            delta = random.uniform(0.05, 0.25) * span * random.choice([-1, 1])
            state.setpoint = _clamp(state.setpoint + delta,
                                    state.rmin + span * 0.05,
                                    state.rmax - span * 0.05)
            state.ramp_active = False
        else:
            # Ramp change: slow linear move over 2–8 minutes
            target = state.nominal + random.gauss(0, span * 0.2)
            target = _clamp(target, state.rmin + span * 0.05, state.rmax - span * 0.05)
            duration = random.randint(60, 240)   # samples
            state.ramp_target  = target
            state.ramp_rate    = (target - state.setpoint) / duration
            state.ramp_active  = True
        state.next_step_in = random.randint(120, 600)  # next change in 4–20 min

    if state.ramp_active:
        state.setpoint += state.ramp_rate
        if abs(state.setpoint - state.ramp_target) < abs(state.ramp_rate):
            state.setpoint    = state.ramp_target
            state.ramp_active = False

    # ── 2. First-order process lag (instrument response) ─────────────────────
    state.pv += alpha * (state.setpoint - state.pv)

    # ── 3. Slow calibration drift (random walk, ~0.5% span/hour at 2s sample) ─
    drift_sigma = span * 0.00003  # per sample → ~0.5% span/hour at 0.5 Hz
    state.drift += random.gauss(0, drift_sigma)
    state.drift  = _clamp(state.drift, -span * 0.08, span * 0.08)  # cap at ±8% span

    # ── 4. Correlated (low-pass filtered) measurement noise ──────────────────
    noise_sigma = span * 0.005   # 0.5% span base noise — tighter than before
    raw_noise   = random.gauss(0, noise_sigma)
    alpha_n     = 0.3            # noise filter: higher = noisier, lower = smoother
    state.noise_state = alpha_n * raw_noise + (1 - alpha_n) * state.noise_state * 0.1

    # ── 5. Fault injection ───────────────────────────────────────────────────
    if state.fault == FAULT_NONE:
        _next_fault(state)

    reading = state.pv + state.drift + state.noise_state

    if state.fault == FAULT_STUCK:
        reading = state.stuck_value
    elif state.fault == FAULT_SPIKE:
        reading = state.pv + random.choice([-1, 1]) * span * random.uniform(0.3, 0.6)
    elif state.fault == FAULT_DRIFT_HIGH:
        state.drift += span * 0.001   # accelerated drift
        reading = state.pv + state.drift + state.noise_state
    elif state.fault == FAULT_DRIFT_LOW:
        state.drift -= span * 0.001
        reading = state.pv + state.drift + state.noise_state
    elif state.fault == FAULT_NOISY:
        reading += random.gauss(0, span * 0.04)   # 4× normal noise

    # Decrement fault timer and clear if expired
    if state.fault != FAULT_NONE:
        state.fault_timer -= 1
        if state.fault_timer <= 0:
            state.fault = FAULT_NONE
            if state.fault == FAULT_STUCK:
                pass  # pv continues from where it was

    # ── 6. Clamp to physical range (4 mA floor = rmin, 20 mA ceiling = rmax) ─
    return _clamp(reading, state.rmin, state.rmax)


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        print(f"Simulator: MQTT connection failed (rc={reason_code})")
    else:
        print(f"Simulator: connected → publishing to cimms/instruments/+/reading")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_simulator(stop_event=None, tags: dict | None = None):
    """
    Run the realistic simulator loop.

    Args:
        stop_event: threading.Event — loop exits when set.
        tags: dict mapping tag_number -> (range_min, range_max, nominal_value, unit).
              Falls back to DEFAULT_TAGS if None.
    """
    active_tags = tags if tags else DEFAULT_TAGS

    # Build per-instrument state objects
    states: dict[str, InstrumentState] = {}
    for tag, (rmin, rmax, nominal, unit) in active_tags.items():
        states[tag] = InstrumentState(
            tag=tag, rmin=rmin, rmax=rmax, nominal=nominal, unit=unit
        )

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cimms-simulator")
    client.on_connect = on_connect
    try:
        client.connect(settings.mqtt_broker, settings.mqtt_port, 60)
    except (ConnectionRefusedError, OSError) as e:
        print(f"Simulator: MQTT broker not available ({e}). Skipping.")
        return
    client.loop_start()

    tag_list = list(states.keys())
    print(f"Simulator: {len(tag_list)} instruments — {', '.join(tag_list)}")
    print("Simulator: realistic process model active (correlated noise, drift, faults)")

    sample_idx = 0
    try:
        while stop_event is None or not stop_event.is_set():
            for tag, state in states.items():
                value = step_instrument(state, sample_idx)

                # Log active faults to console for visibility during testing
                if state.fault != FAULT_NONE and sample_idx % 10 == 0:
                    print(f"  [{tag}] FAULT={state.fault}  value={value:.3f} {state.unit}")

                payload = json.dumps({
                    "value": round(value, 4),
                    "unit":  state.unit,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
                topic = f"cimms/instruments/{tag}/reading"
                client.publish(topic, payload, qos=0)

            sample_idx += 1
            time.sleep(2)   # 0.5 Hz publish rate — matches aggregation_window_seconds=60

    finally:
        client.loop_stop()
        print("Simulator: stopped.")


def main():
    run_simulator()


if __name__ == "__main__":
    main()
