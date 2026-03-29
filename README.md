# The Copperbelt University CMMS

A **Computerised Maintenance Management System** for industrial process instruments — pressure, temperature, flow, and level — combining real-time data acquisition, rule-based monitoring, and machine learning-based anomaly detection. Designed for small-to-medium plant deployments where cost, reliability, and traceability matter.

---

## What This System Does

The system manages the full lifecycle of a process instrument: from commissioning and real-time monitoring, through calibration and maintenance tracking, to condition-based maintenance decisions driven by ML. It is intended to replace spreadsheet-based calibration records and reactive maintenance workflows with a single, auditable platform.

---

## Architecture

```
Field instruments / simulator
        │  MQTT (tag, timestamp, value, unit)
        ▼
  MQTT Ingestion & Validation
  ├── Signal range check (4–20 mA equivalent)
  ├── Rate-of-change validation
  └── Data gap detection
        │
        ▼
  Reading Store (DB + in-memory buffer)
        │
        ├──► Rule Engine          — immediate range/threshold check
        ├──► CUSUM Detector       — cumulative drift and bias detection
        ├──► Stuck Sensor Check   — frozen/dead sensor detection
        └──► Isolation Forest     — statistical pattern anomaly detection
                    │
                    ▼
         Alert Aggregator
         (deduplication, deadband, acknowledgement lifecycle)
                    │
                    ▼
        REST API  /  Web Dashboard
```

---

## Features

### Instrument Register
- Tag number, type (pressure/temperature/flow/level), measured variable, unit, range, nominal value
- Location, associated equipment, accuracy class, criticality (low/medium/high/critical)
- Process unit / area grouping
- Calibration interval (days) — overridable by condition-based prediction

### Calibration Management
- **As-found / as-left data recording** — value before and after adjustment, reference standard used, error percentage
- Next due date calculation from last calibration
- Overdue status flagging with automatic alert and work order generation
- Condition-based interval adjustment driven by measured drift rate (CUSUM output)
- Full calibration history per instrument

### Maintenance Management
- Corrective maintenance records: action type, technician, description, parts used, duration
- Planned preventive maintenance scheduling
- Work order linkage — closing a calibration WO links directly to the calibration record

### Work Order System
- Manual creation or automatic generation from alerts
- Priority levels: low / medium / high / urgent
- Full lifecycle: open → in progress → completed / cancelled
- **Mandatory completion notes** when closing a work order
- Actual vs estimated duration tracking
- Parts/materials used field
- SLA breach flagging (overdue work orders)
- Assigned-to field linked to user accounts (with in-app notification on assignment)

### Machine Learning — Anomaly Detection

#### Isolation Forest (pattern anomalies)
Detects unusual statistical patterns in time-windowed readings that deviate from the trained baseline.

Feature set (10 features, up from 6):
| Feature | Purpose |
|---|---|
| `mean` | Window average |
| `std` | Variability |
| `min` / `max` | Extreme values in window |
| `rate_of_change` | First-to-last delta over time |
| `deviation_from_nominal` | Offset from expected operating point |
| `trend_slope` | Linear regression slope — directional drift |
| `autocorrelation_lag1` | Detects oscillation and hunting |
| `coeff_of_variation` | Normalised noise (std / mean) |
| `range_utilisation` | Position within operating range |

Training uses a **reference baseline** (clean data explicitly marked as normal), not a rolling window of all historical data.

#### CUSUM Detector (drift and bias) — new
Cumulative Sum control chart running continuously per instrument. Detects slow upward or downward drift that Isolation Forest cannot catch — the most common failure mode for process transmitters.

- Configurable slack parameter `k` (default 0.5σ) and decision threshold `h` (default 5σ)
- State reset after a calibration record is posted
- Drift rate at calibration time feeds back into calibration interval prediction
- Raises a `drift` alert type, separate from `anomaly`

#### Stuck Sensor Detection — new
Flags an `instrument_fault` alert when a reading has not changed beyond a configured dead-band for a configurable time window. Catches thermocouple burnout, plugged impulse lines, and transmitter power failure.

#### Signal Range Validation — new
Validates readings against the physical signal range (configurable 4–20 mA equivalent bounds). A reading outside this range raises an `instrument_fault` alert — a hardware problem, not a process anomaly. These are kept separate in the alert type classification.

#### ML Model Versioning — new
Each trained model is stored with metadata: trained by, trained at, number of windows used, contamination parameter, feature set version, active flag. Models can be rolled back. Previous versions are retained.

### Alert System
- Alert types: `calibration_overdue`, `rule_violation`, `drift`, `anomaly`, `instrument_fault`, `data_gap`
- Severity: `advisory` / `warning` / `critical`
- **Alarm deadband and time delay** per instrument — prevents alarm flooding
- **Alert lifecycle**: raised → acknowledged → resolved (with notes)
- Deduplication — the same condition does not re-raise an open alert
- Suppression window after maintenance completion

### Audit Log — new
Immutable log of every action taken in the system: who, what, when, old value, new value. Records are never deleted — soft-deleted entries are flagged. Required for ISO 9001 compliance and regulatory inspections.

### Authentication & Roles — new
- Login with **employee number + password** (bcrypt)
- Email stored optionally for notifications and password reset
- Two roles:
  - **Admin** — full access: instrument CRUD, ML training, user management, all reports, system configuration
  - **Field Technician** — operational access: view all instruments and alerts, update work order status, record calibrations and maintenance, generate reports
- JWT tokens with 8-hour expiry (shift-length sessions)
- Admin can deactivate accounts without deleting them

### In-App Notifications — new
- Bell icon in the navigation bar with unread count
- Technicians receive a notification when a work order is assigned to them
- Polled every 30 seconds; clicking a notification navigates to the work order
- Optional email delivery if an SMTP server is configured

### Reports
Available to both admins and technicians:
- Calibration status and history (with as-found/as-left data)
- Maintenance history
- Instrument health status
- Anomaly and drift detection history
- Compliance summary (calibration compliance %, overdue count)
- Work order history and SLA performance
- Export: PDF and CSV

### Dashboard (UI)
- KPI tiles: total instruments, calibration overdue, active alerts, open work orders
- Per-instrument health cards with live reading, health score bar (0–100), rule/ML status badges
- Click any instrument card to jump to its trend view
- Alert count badge on navigation
- Auto-refresh every 30 seconds with timestamp
- Trend viewer with Chart.js: proper time axis, hover tooltips, range/nominal threshold lines, 1h–7d time range selector, summary statistics
- Search and filter on instrument register
- Inline calibration recording, maintenance recording, and ML training per instrument
- Modals for calibration and maintenance entry (no page navigation required)

---

## Changes from Original System

### Machine Learning
| | Before | After |
|---|---|---|
| Drift detection | None | CUSUM control chart per instrument |
| Stuck sensor | None | Dead-band + time window check |
| Signal validation | None | Hardware fault detection separate from process anomalies |
| Feature set | 6 features | 10 features (added slope, autocorrelation, CoV, range utilisation) |
| Training data | Last 7 days rolling | Reference baseline (clean, admin-confirmed data) |
| Model storage | Single pickle per tag | Versioned with metadata, rollback supported |
| Alarm quality | Immediate on threshold cross | Deadband + time delay per instrument |
| Alert types | calibration_overdue, rule, anomaly | + drift, instrument_fault, data_gap |
| Alert lifecycle | Raised / unresolved | Raised → acknowledged → resolved |

### Calibration
| | Before | After |
|---|---|---|
| Data recorded | pass/fail, notes, technician | + as-found value, as-left value, reference standard, error % |
| Interval basis | Fixed days | Fixed days, overridable by drift-rate prediction |
| Failed calibration | Manual follow-up | Automatic work order generated |

### Work Orders
| | Before | After |
|---|---|---|
| Completion | Status change only | Mandatory notes, actual duration, parts used |
| Calibration link | None | Linked FK to calibration record when WO type = calibration |
| Assignment | Free-text name | Linked to user account with notification |
| SLA | None | Due date with breach flagging |

### Data Model (new tables)
| Table | Purpose |
|---|---|
| `users` | Employee accounts with role and bcrypt password |
| `notifications` | In-app work order assignment notices |
| `audit_log` | Immutable change history for all entities |
| `ml_model_versions` | Model metadata and version history |
| `process_units` | Logical instrument grouping by plant area |
| `background_jobs` | Async job status for ML training and report generation |

### Data Integrity
| | Before | After |
|---|---|---|
| Reading loss on restart | In-memory queue lost | MQTT QoS 1 + persistent session |
| Data retention | Unbounded growth | Tiered downsampling (7d full → 90d 1-min avg → 2yr hourly avg → forever daily avg) |
| Data gaps | Not detected | Alert raised when no reading arrives within 2× expected publish interval |

### Backend
| | Before | After |
|---|---|---|
| Authentication | None | JWT with employee number + bcrypt password |
| Audit trail | None | Immutable audit log on all mutations |
| Health endpoint | None | `GET /health` — DB, MQTT, ingestion status, instruments with data gaps |
| Background tasks | Blocking API call | Async job queue with status tracking |
| Rule alarm | Instant on threshold | Deadband + configurable time delay |

### UI
| | Before | After |
|---|---|---|
| Dashboard | 3 KPI numbers + mini text list | 4 KPIs + health cards with live value and score bar |
| Trends | Raw SVG polyline, no axes | Chart.js with time axis, tooltips, threshold lines, time range selector |
| Instruments | Table with delete only | + search, inline Cal/Maint/ML-train buttons |
| Calibration entry | API-only | Modal dialog from instrument row |
| Maintenance entry | API-only | Modal dialog from instrument row |
| Reports | Raw JSON dump | Formatted summary stats + HTML tables; JSON under disclosure |
| Notifications | None | Bell icon with unread count in nav bar |
| Project name | CIMMS | The Copperbelt University CMMS |

---

## Setup

### 1. Python environment

```bash
cd cmms
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS
pip install -r requirements.txt
```

### 2. MQTT broker

A broker is required for live data. The simplest option:

```bash
# Docker
docker run -p 1883:1883 eclipse-mosquitto

# Linux
sudo apt install mosquitto && sudo systemctl start mosquitto

# Windows: install Mosquitto from https://mosquitto.org/download/
```

The backend starts without a broker; MQTT ingestion connects automatically when the broker becomes available.

### 3. Database initialisation

```bash
python -m scripts.seed_instruments
```

Creates the SQLite database, runs all table migrations, and seeds sample instruments (PT-101, TT-201, FT-301, LT-401) with calibration history.

### 4. Start the application

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** — the web dashboard loads immediately.

### 5. Simulate data (optional)

**From the dashboard** — expand the Data Simulator card and click Start. Requires a broker on localhost.

**Auto-start with the app** — add `SIMULATOR_ENABLED=true` to a `.env` file.

**Separate terminal:**
```bash
python -m scripts.simulate_mqtt
```

### 6. Train ML models

After at least 10 minutes of data for a tag, train its anomaly model:

```bash
POST /api/ml/train/PT-101
```

The CUSUM detector starts automatically with the first reading — no training required.

---

## Configuration

All settings read from environment variables or a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./cimms.db` | Database connection string |
| `MQTT_BROKER` | `localhost` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `AGGREGATION_WINDOW_SECONDS` | `60` | Feature extraction window size |
| `ANOMALY_CONTAMINATION` | `0.05` | Isolation Forest contamination parameter |
| `ANOMALY_WARNING_THRESHOLD` | `0.5` | Score ≥ this → warning |
| `ANOMALY_CRITICAL_THRESHOLD` | `0.7` | Score ≥ this → critical |
| `CUSUM_K` | `0.5` | CUSUM slack (allowable deviation, in σ units) |
| `CUSUM_H` | `5.0` | CUSUM decision threshold (in σ units) |
| `STUCK_SENSOR_MINUTES` | `15` | Minutes without change before stuck-sensor alert |
| `SIMULATOR_ENABLED` | `false` | Auto-start MQTT simulator with app |
| `JWT_SECRET` | *(required in prod)* | Secret key for JWT signing |
| `JWT_EXPIRY_HOURS` | `8` | Token lifetime (one shift) |
| `SMTP_HOST` | *(optional)* | SMTP host for email notifications |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | *(optional)* | SMTP username |
| `SMTP_PASSWORD` | *(optional)* | SMTP password |

---

## API Overview

### Authentication
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Employee number + password → JWT |
| `GET` | `/api/auth/me` | Current user info |

### Instruments
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/instruments` | List all instruments |
| `POST` | `/api/instruments` | Register new instrument *(admin)* |
| `GET` | `/api/instruments/{id}` | Instrument detail |
| `DELETE` | `/api/instruments/{id}` | Remove instrument *(admin)* |
| `GET` | `/api/instruments/{id}/status` | Live rule, CUSUM, ML status and health score |

### Calibration & Maintenance
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/calibrations` | Record calibration (with as-found/as-left) |
| `POST` | `/api/maintenance` | Record maintenance event |

### ML
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/ml/train/{tag}` | Train Isolation Forest for tag *(admin)* |
| `GET` | `/api/ml/models/{tag}` | Model version history for tag |

### Alerts
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/alerts` | All active alerts |
| `POST` | `/api/alerts/{id}/acknowledge` | Acknowledge alert |
| `POST` | `/api/alerts/{id}/resolve` | Resolve alert with notes |

### Work Orders
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/work-orders` | List (filterable by status, tag, assigned_to) |
| `POST` | `/api/work-orders` | Create work order *(admin)* |
| `POST` | `/api/work-orders/from-alert` | Auto-create from alert *(admin)* |
| `PATCH` | `/api/work-orders/{id}` | Update status / completion fields |

### Notifications
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/notifications` | Unread notifications for current user |
| `POST` | `/api/notifications/{id}/read` | Mark notification read |

### Reports
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/reports/calibration` | Calibration status and as-found/as-left history |
| `GET` | `/api/reports/maintenance` | Maintenance history |
| `GET` | `/api/reports/health-status` | Instrument health scores |
| `GET` | `/api/reports/anomalies` | Anomaly and drift detection history |
| `GET` | `/api/reports/compliance` | Calibration compliance summary |
| `GET` | `/api/reports/work-orders` | Work order history and SLA performance |

All report endpoints accept `?export=csv` or `?export=pdf`.

### System
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | DB, MQTT, ingestion status, instruments with data gaps |
| `GET` | `/api/dashboard/summary` | KPI counts for dashboard |
| `GET` | `/api/readings/trends` | Time-series readings for a tag |
| `GET/POST` | `/api/simulator/*` | Simulator control *(admin)* |

---

## Project Structure

```
cmms/
├── main.py                        # FastAPI app, MQTT wiring, startup, static files
├── config.py                      # Settings (env / .env)
├── requirements.txt
│
├── api/
│   ├── routes.py                  # REST endpoints
│   ├── auth.py                    # Login, JWT issue/verify, role dependencies
│   └── __init__.py
│
├── backend/
│   ├── database.py                # Async SQLAlchemy engine and session factory
│   ├── models.py                  # ORM models (see Data Model section)
│   ├── schemas.py                 # Pydantic request/response schemas
│   └── services/
│       ├── instrument_service.py  # Instrument register, calibration, maintenance
│       ├── mqtt_ingestion.py      # MQTT subscriber, validation, queue
│       ├── reading_store.py       # Reading persistence, buffer, downsampling
│       ├── feature_extraction.py  # 10-feature extraction per time window
│       ├── anomaly_detection.py   # Isolation Forest train/predict, versioning
│       ├── cusum_detector.py      # CUSUM drift detector (per-instrument state)
│       ├── stuck_sensor.py        # Stuck/frozen sensor detection
│       ├── rules.py               # Threshold rules with deadband and time delay
│       ├── health_index.py        # Aggregate health score (0–100)
│       ├── alert_service.py       # Alert lifecycle, deduplication, acknowledgement
│       ├── work_order_service.py  # Work order CRUD and lifecycle
│       ├── notification_service.py# In-app and email notifications
│       ├── audit_log.py           # Immutable audit trail writer
│       ├── data_retention.py      # Tiered downsampling and archival
│       ├── report_generation.py   # Report builders
│       └── report_export.py       # PDF and CSV export
│
├── frontend/dist/
│   └── index.html                 # Single-page dashboard (Chart.js, vanilla JS)
│
├── scripts/
│   ├── seed_instruments.py        # DB init and sample data
│   └── simulate_mqtt.py           # Standalone MQTT data publisher
│
├── models/                        # Persisted ML model files (versioned)
└── data/                          # Reserved for future time-series storage
```

### Data Model

```
users                   — employee_number, full_name, role, email, password_hash, is_active
instruments             — tag, type, range, criticality, calibration_interval, process_unit_id
process_units           — name, description, plant_area
calibration_records     — instrument_id, performed_at, as_found, as_left, reference, error_pct, passed
maintenance_records     — instrument_id, action_type, technician, description, parts_used, duration
work_orders             — instrument_id, title, type, priority, status, assigned_to, due_date, completion_notes
raw_readings            — tag_number, timestamp, value, unit
alert_records           — instrument_id, alert_type, severity, message, acknowledged_by, resolved_at
notifications           — user_id, work_order_id, message, read_at
audit_log               — user_id, action, entity_type, entity_id, old_value, new_value, timestamp
ml_model_versions       — tag_number, trained_at, trained_by, windows_used, is_active, model_file
background_jobs         — job_type, status, started_at, completed_at, result_summary, error
```

---

## Typical Workflow

1. **Admin sets up** — creates user accounts, registers instruments with ranges and calibration intervals, assigns process units
2. **Data flows in** — MQTT readings arrive; rule engine and CUSUM run immediately on every reading
3. **Alerts surface** — overdue calibration, range violations, detected drift, stuck sensors appear in the alert panel with severity and source clearly labelled
4. **Admin raises work orders** — manually or from an alert; assigned technician receives an in-app notification
5. **Technician acts** — updates WO status in the field, records calibration as-found/as-left data and maintenance details on completion
6. **ML improves over time** — after enough clean baseline data, admin trains the Isolation Forest; CUSUM drift data refines calibration intervals from fixed-time to condition-based
7. **Reports for compliance** — calibration compliance, maintenance history, and anomaly history exported as PDF or CSV for audits

---

## Requirements

- **Python** 3.10+
- **OS** Windows, Linux, or macOS
- **MQTT broker** Mosquitto or any MQTT 3.1.1-compatible broker
- **Optional** SMTP server for email notifications

All Python dependencies are in `requirements.txt`.

---

## License

Specify your chosen license here (e.g. MIT, Apache 2.0) and include a `LICENSE` file at the project root.
