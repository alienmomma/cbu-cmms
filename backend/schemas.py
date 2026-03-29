"""Pydantic schemas for API request/response."""
from datetime import datetime
from pydantic import BaseModel
from backend.models import InstrumentType, CriticalityLevel, WorkOrderStatus, WorkOrderPriority


class InstrumentCreate(BaseModel):
    tag_number: str
    instrument_type: InstrumentType
    measured_variable: str
    unit: str
    range_min: float
    range_max: float
    calibration_interval_days: int
    criticality: CriticalityLevel
    accuracy_class: str | None = None
    location: str | None = None
    associated_equipment: str | None = None
    nominal_value: float | None = None


class InstrumentResponse(BaseModel):
    id: int
    tag_number: str
    instrument_type: InstrumentType
    measured_variable: str
    unit: str
    range_min: float
    range_max: float
    accuracy_class: str | None
    location: str | None
    associated_equipment: str | None
    calibration_interval_days: int
    criticality: CriticalityLevel
    nominal_value: float | None
    next_calibration_due: datetime | None
    calibration_overdue: bool
    created_at: datetime
    ml_trained: bool

    class Config:
        from_attributes = True


class CalibrationRecordCreate(BaseModel):
    instrument_id:   int
    performed_at:    datetime
    passed:          bool
    performed_by:    str | None = None
    notes:           str | None = None
    as_found_value:  float | None = None
    as_left_value:   float | None = None
    reference_value: float | None = None


class MaintenanceRecordCreate(BaseModel):
    instrument_id: int
    action_type: str
    description: str | None = None
    technician: str | None = None
    trigger_source: str | None = None
    notes: str | None = None


class ReadingResponse(BaseModel):
    tag_number: str
    timestamp: datetime
    value: float
    unit: str | None


class InstrumentStatusResponse(BaseModel):
    tag_number:        str
    rule_based_status: str
    anomaly_state:     str
    anomaly_score:     float | None
    cusum_state:       str           # "normal" | "drift_high" | "drift_low"
    cusum_c_pos:       float         # upper accumulator value (diagnostic)
    cusum_c_neg:       float         # lower accumulator value (diagnostic)
    stuck_sensor:      bool
    last_value:        float | None
    last_timestamp:    datetime | None
    health_index:      float | None
    health_label:      str | None
    recommendation:    str | None


class AlertSummary(BaseModel):
    id:         int | None = None
    tag_number: str
    state:      str
    source:     str   # "rule" | "ml" | "drift" | "stuck" | "fault"
    alert_type: str   # "rule_violation" | "anomaly" | "drift" | "instrument_fault" | "calibration_overdue"
    message:    str
    timestamp:  datetime


# ============================================================================
# REPORT SCHEMAS
# ============================================================================


class CalibrationHistoryItem(BaseModel):
    performed_at: datetime
    due_next_at: datetime
    passed: bool
    performed_by: str | None
    notes: str | None


class CalibrationReportInstrument(BaseModel):
    tag_number: str
    instrument_type: str
    location: str | None
    calibration_interval_days: int
    criticality: str
    status: str  # OVERDUE, DUE_IN_7_DAYS, DUE_IN_30_DAYS, UP_TO_DATE, NEVER_CALIBRATED
    last_calibration: datetime | None
    next_due: datetime | None
    days_until_due: int | None
    calibration_history: list[CalibrationHistoryItem] | None = None


class CalibrationReportSummary(BaseModel):
    total_instruments: int
    due_in_7_days: int
    due_in_30_days: int
    overdue: int
    up_to_date: int


class CalibrationReport(BaseModel):
    report_type: str
    generated_at: datetime
    instruments: list[CalibrationReportInstrument]
    summary: CalibrationReportSummary


class MaintenanceRecordDetail(BaseModel):
    created_at: datetime
    instrument_tag: str | None
    action_type: str
    description: str | None
    technician: str | None
    resolved_at: datetime | None
    trigger_source: str | None


class MaintenanceReportSummary(BaseModel):
    total_records: int
    resolved: int
    pending: int
    avg_resolution_hours: float | None
    action_type_breakdown: dict[str, int]
    trigger_source_breakdown: dict[str, int]


class MaintenanceReport(BaseModel):
    report_type: str
    generated_at: datetime
    period_days: int
    summary: MaintenanceReportSummary
    records: list[MaintenanceRecordDetail]


class RecentAlert(BaseModel):
    timestamp: datetime
    alert_type: str
    severity: str
    message: str


class HealthStatusInstrument(BaseModel):
    tag_number: str
    instrument_type: str
    location: str | None
    nominal_value: float | None
    recent_alerts: list[RecentAlert]


class HealthStatusReport(BaseModel):
    report_type: str
    generated_at: datetime
    instruments: list[HealthStatusInstrument]
    summary: dict


class AnomalyAlert(BaseModel):
    timestamp: datetime
    instrument_tag: str | None
    severity: str
    message: str


class AnomalyReportSummary(BaseModel):
    total_anomalies_detected: int
    severity_breakdown: dict[str, int]
    affected_instruments: int


class AnomalyReport(BaseModel):
    report_type: str
    generated_at: datetime
    period_days: int
    summary: AnomalyReportSummary
    alerts: list[AnomalyAlert]


class ComplianceReportSummary(BaseModel):
    total_instruments: int
    calibration_compliance_percentage: float
    overdue_calibrations: int
    overdue_by_criticality: dict[str, int]
    active_alerts: int
    instruments_with_active_alerts: int
    overall_risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL


class ComplianceReport(BaseModel):
    report_type: str
    generated_at: datetime
    summary: ComplianceReportSummary
    recommendations: list[str]


class WorkOrderCreate(BaseModel):
    instrument_id: int | None = None
    title: str
    description: str | None = None
    work_type: str = "corrective"
    priority: WorkOrderPriority = WorkOrderPriority.MEDIUM
    assigned_to: str | None = None
    due_date: datetime | None = None


class WorkOrderFromAlertCreate(BaseModel):
    tag_number: str
    message: str
    alert_source: str
    state: str


class WorkOrderPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    status: WorkOrderStatus | None = None
    priority: WorkOrderPriority | None = None
    assigned_to: str | None = None
    due_date: datetime | None = None


class AlertAcknowledge(BaseModel):
    acknowledged_by: str
    notes:           str | None = None


class AlertResolve(BaseModel):
    resolved_by: str
    notes:        str | None = None


# ============================================================================
# AUTH & USER SCHEMAS
# ============================================================================

class LoginResponse(BaseModel):
    access_token:    str
    token_type:      str
    role:            str
    full_name:       str
    employee_number: str


class UserResponse(BaseModel):
    id:              int
    employee_number: str
    full_name:       str
    email:           str | None
    role:            str
    is_active:       bool
    last_login:      datetime | None

    class Config:
        from_attributes = True


class NotificationResponse(BaseModel):
    id:        int
    created_at: datetime
    type:      str
    title:     str
    message:   str | None
    entity_id: int | None
    is_read:   bool

    class Config:
        from_attributes = True


class WorkOrderResponse(BaseModel):
    id: int
    work_order_number: str
    instrument_id: int | None
    instrument_tag: str | None = None
    title: str
    description: str | None
    work_type: str
    priority: WorkOrderPriority
    status: WorkOrderStatus
    assigned_to: str | None
    due_date: datetime | None
    source: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    class Config:
        from_attributes = True
