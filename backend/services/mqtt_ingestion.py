"""MQTT client: subscribe to instrument readings and ingest into backend."""
import json
import queue
from datetime import datetime
import paho.mqtt.client as mqtt

from config import settings


# Thread-safe queue: MQTT thread puts (tag, timestamp, value, unit), async task drains it
_reading_queue = queue.Queue()


def get_reading_queue():
    """Return the queue so the main app can drain it from an async task."""
    return _reading_queue


def _on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        print(f"MQTT connection failed: {reason_code}")
        return
    topic = settings.mqtt_topic_instruments
    client.subscribe(topic)
    print(f"Subscribed to {topic}")


def _on_message(client, userdata, msg):
    """Parse payload and put reading on queue. Payload: JSON { timestamp?, value, unit? } or raw number."""
    try:
        topic = msg.topic
        parts = topic.split("/")
        tag_number = parts[2] if len(parts) >= 3 else "unknown"

        payload = msg.payload.decode("utf-8")
        try:
            data = json.loads(payload)
            value = float(data.get("value", data) if isinstance(data, dict) else data)
            ts = data.get("timestamp")
            if isinstance(ts, str):
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    timestamp = dt.replace(tzinfo=None) if dt.tzinfo else dt
                except ValueError:
                    timestamp = datetime.utcnow()
            else:
                timestamp = datetime.utcnow()
            unit = data.get("unit") if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError):
            value = float(payload)
            timestamp = datetime.utcnow()
            unit = None

        _reading_queue.put((tag_number, timestamp, value, unit))
    except Exception as e:
        print(f"MQTT message error: {e}")


def start_mqtt_client():
    """Start MQTT client in a background thread (blocking loop)."""
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.mqtt_client_id,
    )
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(settings.mqtt_broker, settings.mqtt_port, 60)
    client.loop_start()
    return client
