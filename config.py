"""The Copperbelt University CMMS — configuration."""
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    """Application settings loaded from environment or .env."""

    # App
    app_name: str = "The Copperbelt University CMMS"
    debug: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./cimms.db"

    # MQTT
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    simulator_enabled: bool = False  # Set SIMULATOR_ENABLED=true to run simulator with app

    mqtt_topic_instruments: str = "cimms/instruments/+/reading"
    mqtt_client_id: str = "cimms-backend"

    # ML / Anomaly detection
    aggregation_window_seconds: int = 60  # 1-minute windows
    anomaly_contamination: float = 0.05  # expected proportion of anomalies in training
    anomaly_warning_threshold: float = 0.5  # score above = warning
    anomaly_critical_threshold: float = 0.7  # score above = critical

    # CUSUM drift detection
    cusum_k: float = 0.5        # allowable slack (sigma units)
    cusum_h: float = 5.0        # decision threshold (sigma units)

    # Stuck sensor detection
    stuck_sensor_minutes: int = 15  # minutes without change before alert

    # Alarm management
    alarm_deadband_pct: float = 2.0      # % of span required outside range before alarming
    alarm_time_delay_seconds: int = 30   # seconds condition must persist before alarm fires

    # Authentication
    jwt_secret_key: str = "CHANGE-ME-IN-PRODUCTION-USE-A-LONG-RANDOM-SECRET"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 8

    # Paths
    data_dir: Path = Path("./data")
    models_dir: Path = Path("./models")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.models_dir.mkdir(parents=True, exist_ok=True)
