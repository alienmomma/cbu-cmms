"""Simulate instrument readings and publish to MQTT for testing CIMMS without hardware."""
import json
import random
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paho.mqtt.client as mqtt
from config import settings

# Default fallback tags when no DB instruments are provided
# tag -> (range_min, range_max, nominal_value, unit)
DEFAULT_TAGS = {
    "PT-101": (0, 10, 7.5, "bar"),
    "TT-201": (-20, 120, 80, "°C"),
    "FT-301": (0, 100, 50, "m³/h"),
    "LT-401": (0, 100, 60, "%"),
}


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        print("Connection failed:", reason_code)
    else:
        print("Connected. Publishing to", settings.mqtt_topic_instruments.replace("/+", "/{tag}"))


def run_simulator(stop_event=None, tags=None):
    """Run the simulator loop. If stop_event is provided, loop until it is set.

    Args:
        stop_event: threading.Event to signal shutdown.
        tags: dict mapping tag_number -> (range_min, range_max, nominal_value, unit).
              Falls back to DEFAULT_TAGS if None or empty.
    """
    active_tags = tags if tags else DEFAULT_TAGS
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cimms-simulator")
    client.on_connect = on_connect
    try:
        client.connect(settings.mqtt_broker, settings.mqtt_port, 60)
    except (ConnectionRefusedError, OSError) as e:
        print(f"Simulator: MQTT broker not available ({e}). Skipping simulator.")
        return
    client.loop_start()
    tag_list = list(active_tags.keys())
    print(f"Simulator: publishing for {len(tag_list)} tags: {', '.join(tag_list)}")
    try:
        while stop_event is None or not stop_event.is_set():
            for tag, (rmin, rmax, nominal, unit) in active_tags.items():
                span = rmax - rmin
                noise_sigma = max(span * 0.02, 0.001)  # 2% span noise
                drift = random.gauss(0, noise_sigma)
                value = max(rmin, min(rmax, nominal + drift))
                payload = json.dumps({
                    "value": round(value, 4),
                    "unit": unit,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
                topic = f"cimms/instruments/{tag}/reading"
                client.publish(topic, payload, qos=0)
            time.sleep(2)
    finally:
        client.loop_stop()


def main():
    run_simulator()


if __name__ == "__main__":
    main()
