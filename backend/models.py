"""SQLAlchemy models for instruments, maintenance, and readings."""
from datetime import datetime
import enum
from sqlalchemy import String, Float, Integer, DateTime, Text, ForeignKey, Boolean, Enum as SQLEnum, Column, JSON
from sqlalchemy.orm import relationship

from backend.database import Base


class InstrumentType(str, enum.Enum):
    PRESSURE = "pressure"
    TEMPERATURE = "temperature"
    FLOW = "flow"
    LEVEL = "level"


class CriticalityLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WorkOrderStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class WorkOrderPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Instrument(Base):
    """Instrument register: one row per physical instrument."""
    __tablename__ = "instruments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tag_number = Column(String(64), unique=True, nullable=False, index=True)
    instrument_type = Column(SQLEnum(InstrumentType), nullable=False)
    measured_variable = Column(String(128), nullable=False)
    unit = Column(String(32), nullable=False)
    range_min = Column(Float, nullable=False)
    range_max = Column(Float, nullable=False)
    accuracy_class = Column(String(32), nullable=True)
    location = Column(String(256), nullable=True)
    associated_equipment = Column(String(256), nullable=True)
    calibration_interval_days = Column(Integer, nullable=False)
    criticality = Column(SQLEnum(CriticalityLevel), nullable=False)
    nominal_value = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    calibrations = relationship("CalibrationRecord", back_populates="instrument", order_by="CalibrationRecord.performed_at.desc()")
    maintenance_records = relationship("MaintenanceRecord", back_populates="instrument", order_by="MaintenanceRecord.created_at.desc()")
    work_orders = relationship("WorkOrder", back_populates="instrument", order_by="WorkOrder.created_at.desc()")


class CalibrationRecord(Base):
    """Record of a calibration event (preventive)."""
    __tablename__ = "calibration_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False)
    performed_at = Column(DateTime, nullable=False)
    due_next_at = Column(DateTime, nullable=False)
    passed = Column(Boolean, nullable=False)
    notes = Column(Text, nullable=True)
    performed_by = Column(String(128), nullable=True)
    as_found_value = Column(Float, nullable=True)
    as_left_value  = Column(Float, nullable=True)
    reference_value = Column(Float, nullable=True)
    error_found_pct = Column(Float, nullable=True)
    error_left_pct  = Column(Float, nullable=True)
    # Five-point ISA-51.1 calibration data — list of dicts:
    # [{pct, ref_val, as_found, as_left, err_found_pct, err_left_pct}, ...]
    calibration_points = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    instrument = relationship("Instrument", back_populates="calibrations")


class MaintenanceRecord(Base):
    """Corrective maintenance or inspection triggered by anomaly/failure."""
    __tablename__ = "maintenance_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    action_type = Column(String(64), nullable=False)
    description = Column(Text, nullable=True)
    technician = Column(String(128), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    trigger_source = Column(String(64), nullable=True)

    instrument = relationship("Instrument", back_populates="maintenance_records")


class WorkOrder(Base):
    """CMMS work order: generated manually, from alerts, or scheduled."""
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    work_order_number = Column(String(32), unique=True, nullable=False, index=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=True, index=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    work_type = Column(String(64), nullable=False)
    priority = Column(SQLEnum(WorkOrderPriority), nullable=False, default=WorkOrderPriority.MEDIUM)
    status = Column(SQLEnum(WorkOrderStatus), nullable=False, default=WorkOrderStatus.OPEN)
    assigned_to = Column(String(128), nullable=True)
    due_date = Column(DateTime, nullable=True)
    source = Column(String(64), nullable=True)
    source_detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    instrument = relationship("Instrument", back_populates="work_orders")


class RawReading(Base):
    """Raw instrument readings (time-series)."""
    __tablename__ = "raw_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tag_number = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    value = Column(Float, nullable=False)
    unit = Column(String(32), nullable=True)


class AlertRecord(Base):
    """Alert record: tracks calibration overdue, rule violations, and ML anomalies."""
    __tablename__ = "alert_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    alert_type = Column(String(64), nullable=False)  # "calibration_overdue", "rule_violation", "anomaly"
    severity = Column(String(32), nullable=False)  # "warning", "critical"
    message = Column(Text, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    acknowledged_at    = Column(DateTime, nullable=True)
    acknowledged_by    = Column(String(128), nullable=True)
    resolution_notes   = Column(Text, nullable=True)
    suppressed_until   = Column(DateTime, nullable=True)
    is_active          = Column(Boolean, default=True, nullable=False)

    instrument = relationship("Instrument")


class MLModelVersion(Base):
    """Versioned record of each trained anomaly detection model."""
    __tablename__ = "ml_model_versions"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    tag_number     = Column(String(64), nullable=False, index=True)
    trained_at     = Column(DateTime, default=datetime.utcnow)
    trained_by     = Column(String(128), nullable=True)
    windows_used   = Column(Integer, nullable=False)
    contamination  = Column(Float, nullable=False)
    features_version = Column(Integer, nullable=False, default=2)
    model_file     = Column(String(256), nullable=False)
    is_active      = Column(Boolean, default=True, nullable=False)
    notes          = Column(Text, nullable=True)


class UserRole(str, enum.Enum):
    admin = "admin"
    technician = "technician"


class User(Base):
    """System user — admin or field technician."""
    __tablename__ = "users"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    employee_number = Column(String(32), unique=True, nullable=False, index=True)
    full_name       = Column(String(128), nullable=False)
    email           = Column(String(256), nullable=True)
    role            = Column(SQLEnum(UserRole), nullable=False)
    hashed_password = Column(String(256), nullable=False)
    is_active       = Column(Boolean, default=True, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)
    last_login      = Column(DateTime, nullable=True)

    notifications = relationship("Notification", back_populates="user")


class Notification(Base):
    """In-app notification (e.g. work order assigned to technician)."""
    __tablename__ = "notifications"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    type       = Column(String(64), nullable=False)   # "work_order_assigned"
    title      = Column(String(256), nullable=False)
    message    = Column(Text, nullable=True)
    entity_id  = Column(Integer, nullable=True)       # work order id
    is_read    = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="notifications")
