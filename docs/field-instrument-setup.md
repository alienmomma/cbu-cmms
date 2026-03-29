# Field Instrument Setup Guide
## Connecting Process Instruments to CBU CMMS via ESP32 + MQTT

---

## Part 1 — What You Need

**Hardware per instrument channel:**
- ESP32 dev board (ESP32-WROOM-32 or equivalent)
- ADS1115 16-bit ADC module (I²C, up to 4 channels per board)
- 250 Ω precision resistor (1% tolerance) — converts 4–20 mA to 1–5 V
- 24 VDC loop power supply (for 4–20 mA transmitters)
- Terminal blocks, DIN rail, enclosure
- Shielded twisted-pair cable for signal runs

**Software:**
- Arduino IDE or PlatformIO
- Libraries: `Adafruit_ADS1X15`, `PubSubClient` (MQTT), `ArduinoJson`, `WiFi.h`
- MQTT broker: Mosquitto on a local Raspberry Pi, or HiveMQ Cloud (free tier)

---

## Part 2 — Wiring a 4–20 mA Instrument

4–20 mA is the standard signal for all process transmitters (pressure, temperature, flow, level).

```
24VDC (+) ──────────────────── Transmitter (+)
                                Transmitter (−) ──── 250Ω resistor ──── ADS1115 AIN0
                                                                    └─── ADS1115 GND
24VDC (−) ──────────────────────────────────────────────────────────── ADS1115 GND
```

- The 250 Ω resistor converts current to voltage: 4 mA → 1.0 V, 20 mA → 5.0 V
- ADS1115 measures 1.0–5.0 V across the resistor
- Use the ±6.144 V gain setting on the ADS1115 (`GAIN_TWOTHIRDS`)
- **Shield the cable** and connect the shield to GND at one end only (the panel end)

**Multiple instruments:** each gets its own loop (+24V → transmitter → 250 Ω → one ADS1115 channel → GND). One ADS1115 handles 4 channels. Stack multiple ADS1115 boards by setting different I²C addresses using the ADDR pin:

| ADDR pin connected to | I²C Address |
|---|---|
| GND | 0x48 |
| VCC | 0x49 |
| SDA | 0x4A |
| SCL | 0x4B |

---

## Part 3 — ADS1115 to ESP32 Wiring

```
ADS1115        ESP32
─────────────────────────────
VDD    ──────  3.3V
GND    ──────  GND
SCL    ──────  GPIO 22  (I²C clock)
SDA    ──────  GPIO 21  (I²C data)
ADDR   ──────  GND      (address 0x48)
AIN0   ──────  signal from 250 Ω resistor (instrument 1)
AIN1   ──────  signal from 250 Ω resistor (instrument 2)
AIN2   ──────  signal from 250 Ω resistor (instrument 3)
AIN3   ──────  signal from 250 Ω resistor (instrument 4)
```

---

## Part 4 — ESP32 Firmware

Create a new Arduino sketch and paste the code below. Configure the top section for your network and instruments.

```cpp
#include <WiFi.h>
#include <PubSubClient.h>
#include <Adafruit_ADS1X15.h>
#include <ArduinoJson.h>

// ── Configure these ────────────────────────────────────────────────────
const char* WIFI_SSID     = "YourWiFiSSID";
const char* WIFI_PASSWORD = "YourWiFiPassword";
const char* MQTT_BROKER   = "192.168.1.100";  // broker IP or hostname
const int   MQTT_PORT     = 1883;
const char* MQTT_CLIENT   = "esp32-field-01"; // unique per ESP32

// One entry per instrument channel.
// tag must exactly match the Tag Number registered in the CMMS.
struct Channel {
    const char* tag;
    const char* unit;
    float       range_min;
    float       range_max;
    uint8_t     ads_channel; // 0–3
};

Channel channels[] = {
    { "PT-101", "bar",  0.0,  10.0, 0 },
    { "TT-201", "degC", 0.0, 200.0, 1 },
    { "FT-301", "m3/h", 0.0,  50.0, 2 },
};
const int NUM_CHANNELS = sizeof(channels) / sizeof(channels[0]);

const int PUBLISH_INTERVAL_MS = 5000; // publish every 5 seconds
// ───────────────────────────────────────────────────────────────────────

Adafruit_ADS1115 ads;
WiFiClient       wifiClient;
PubSubClient     mqtt(wifiClient);

// Convert raw ADS1115 reading to engineering units via 4–20 mA loop.
// ADS1115 at GAIN_TWOTHIRDS: 1 LSB = 0.1875 mV
float rawToEngineering(int16_t raw, float rmin, float rmax) {
    float volts = raw * 0.0001875f;
    float mA    = (volts - 1.0f) * (16.0f / 4.0f) + 4.0f; // 1V=4mA, 5V=20mA
    float pct   = (mA - 4.0f) / 16.0f;                     // 0.0 – 1.0
    return rmin + pct * (rmax - rmin);
}

void connectWiFi() {
    Serial.print("Connecting to WiFi");
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println(" connected. IP: " + WiFi.localIP().toString());
}

void connectMQTT() {
    while (!mqtt.connected()) {
        Serial.print("Connecting to MQTT...");
        if (mqtt.connect(MQTT_CLIENT)) {
            Serial.println(" connected.");
        } else {
            Serial.print(" failed (rc=");
            Serial.print(mqtt.state());
            Serial.println("). Retrying in 2s.");
            delay(2000);
        }
    }
}

void setup() {
    Serial.begin(115200);
    connectWiFi();
    mqtt.setServer(MQTT_BROKER, MQTT_PORT);
    ads.setGain(GAIN_TWOTHIRDS); // ±6.144V range — safe for 0–5V signals
    ads.begin();
    Serial.println("ADS1115 ready.");
}

void loop() {
    if (!mqtt.connected()) connectMQTT();
    mqtt.loop();

    for (int i = 0; i < NUM_CHANNELS; i++) {
        int16_t raw   = ads.readADC_SingleEnded(channels[i].ads_channel);
        float   value = rawToEngineering(raw, channels[i].range_min,
                                               channels[i].range_max);

        // MQTT topic format expected by the CMMS:
        // cimms/instruments/<tag>/reading
        char topic[64];
        snprintf(topic, sizeof(topic),
                 "cimms/instruments/%s/reading", channels[i].tag);

        StaticJsonDocument<128> doc;
        doc["tag"]   = channels[i].tag;
        doc["value"] = value;
        doc["unit"]  = channels[i].unit;

        char payload[128];
        serializeJson(doc, payload);

        if (mqtt.publish(topic, payload)) {
            Serial.printf("[%s] %.3f %s\n",
                          channels[i].tag, value, channels[i].unit);
        } else {
            Serial.printf("[%s] publish failed\n", channels[i].tag);
        }
    }

    delay(PUBLISH_INTERVAL_MS);
}
```

### Installing the required libraries (Arduino IDE)

Go to **Sketch → Include Library → Manage Libraries** and install:

| Library | Search term |
|---|---|
| Adafruit ADS1X15 | `Adafruit ADS1X15` |
| PubSubClient | `PubSubClient` |
| ArduinoJson | `ArduinoJson` |

---

## Part 5 — MQTT Broker Setup

### Option A — Mosquitto on a Raspberry Pi or local PC (LAN use)

```bash
# Install
sudo apt install mosquitto mosquitto-clients

# Allow anonymous connections on LAN (suitable for lab / campus network)
sudo tee /etc/mosquitto/conf.d/local.conf <<EOF
listener 1883
allow_anonymous true
EOF

sudo systemctl enable mosquitto
sudo systemctl restart mosquitto

# Verify — you should see JSON arriving from the ESP32
mosquitto_sub -h localhost -t "cimms/instruments/+/reading"
```

### Option B — HiveMQ Cloud (free tier, for cloud/Render deployment)

1. Go to [hivemq.com/mqtt-cloud-broker](https://www.hivemq.com/mqtt-cloud-broker/) and create a free cluster
2. Note the **hostname** (e.g. `abc123.s2.eu.hivemq.cloud`), **port** `8883` (TLS), **username** and **password**
3. Update the ESP32 firmware to use `WiFiClientSecure` and the TLS port
4. In the Render dashboard set `MQTT_BROKER` to the HiveMQ hostname and `MQTT_PORT` to `8883`

---

## Part 6 — Register the Instrument in the CMMS

In the CMMS web UI → **Instruments panel** → **Add Instrument**, fill in:

| Field | Example | Notes |
|---|---|---|
| Tag Number | `PT-101` | **Must exactly match** the tag in the ESP32 firmware |
| Instrument Type | Pressure | |
| Measured Variable | Process pressure | |
| Unit | bar | Must match the `unit` field in the firmware |
| Range Min | 0.0 | Must match the firmware `range_min` |
| Range Max | 10.0 | Must match the firmware `range_max` |
| Calibration Interval | 180 | Days between calibrations |
| Criticality | High | |
| Nominal Value | 5.0 | Expected normal operating value — used as ML baseline |
| Location | Pump house A | Optional but recommended |

Once registered, the instrument appears on the dashboard and begins receiving live readings within one publish cycle.

---

## Part 7 — Verify End-to-End

1. Power the 24 V loop and the ESP32
2. Open a terminal on the broker machine and subscribe:
   ```bash
   mosquitto_sub -h <broker-ip> -t "cimms/instruments/+/reading" -v
   ```
   You should see JSON payloads arriving every 5 seconds.
3. Start the CMMS server:
   ```bash
   .venv/Scripts/python -m uvicorn main:app --host 0.0.0.0 --port 8000
   ```
4. Open the dashboard — the instrument tile should show a live reading within seconds
5. Navigate to **Instruments** — the status badge should change from `no data` to `normal`
6. Leave it running for **1–2 hours** to accumulate enough data, then click **Train ML** on the instrument row to establish the anomaly detection baseline

---

## Part 8 — Running Multiple ESP32 Nodes

Each ESP32 must have a **unique `MQTT_CLIENT` ID**. Use the same broker for all nodes.

```
ESP32 node 1 (MQTT_CLIENT="esp32-field-01")  →  PT-101, TT-201, FT-301, LT-401
ESP32 node 2 (MQTT_CLIENT="esp32-field-02")  →  PT-102, TT-202, FT-302, LT-402
ESP32 node 3 (MQTT_CLIENT="esp32-field-03")  →  PT-103, TT-203
```

All nodes publish to the same broker. The CMMS subscribes to the wildcard topic `cimms/instruments/+/reading` and routes each message to the correct instrument by tag number.

---

## Common Issues

| Symptom | Likely Cause | Fix |
|---|---|---|
| Reading stuck at minimum | No 4 mA signal reaching ADS1115 | Check 24 V supply, loop wiring polarity, 250 Ω resistor connection |
| Reading pegged at maximum | >20 mA or wrong ADC gain | Verify transmitter range, confirm `GAIN_TWOTHIRDS` is set |
| Serial shows NaN or -32768 | ADS1115 not communicating | Check SDA/SCL wiring, confirm I²C address (0x48 default) |
| MQTT not connecting | Wrong broker IP or firewall | Confirm broker IP, check port 1883 is open on the network |
| Tag not appearing in CMMS | Tag name mismatch | The firmware `tag` field must exactly match the CMMS Tag Number |
| Noisy / jumping readings | Unshielded cable or ground loop | Use shielded cable, connect shield to GND at panel end only |
| Dashboard shows old value | CMMS not receiving MQTT | Check broker is running, MQTT_BROKER env var is correct |

---

## Instrument Signal Type Reference

| Instrument type | Typical output | Wiring method |
|---|---|---|
| Smart transmitter (pressure, temp, level) | 4–20 mA (2-wire) | 250 Ω → ADS1115 (this guide) |
| Thermocouple | mV (type K: 0–52 mV for 0–1250 °C) | MAX31855 or MAX6675 SPI module |
| RTD (PT100 / PT1000) | Resistance | MAX31865 SPI module |
| Ultrasonic / radar level | 4–20 mA or RS-485 Modbus | 250 Ω → ADS1115 or ESP32 UART |
| Coriolis / magnetic flow | 4–20 mA or HART | 250 Ω → ADS1115 (or HART modem) |
| Voltage output (0–5V, 0–10V) | Voltage | Direct to ADS1115 (check voltage divider for 0–10V) |
