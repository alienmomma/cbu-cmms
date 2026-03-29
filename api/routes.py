"""API route handlers."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Instrument, User, Notification
from backend.auth import require_admin, require_any_role
from backend.schemas import (
    InstrumentCreate,
    InstrumentResponse,
    CalibrationRecordCreate,
    MaintenanceRecordCreate,
    InstrumentStatusResponse,
    AlertSummary,
    WorkOrderCreate,
    WorkOrderFromAlertCreate,
    WorkOrderPatch,
    WorkOrderResponse,
    AlertAcknowledge,
    AlertResolve,
    NotificationResponse,
)
from backend.services.instrument_service import (
    list_instruments,
    create_instrument,
    get_instrument_by_id,
    get_instrument_by_tag,
    next_calibration_due,
    is_calibration_overdue,
    add_calibration_record,
    add_maintenance_record,
    delete_instrument_by_id,
)
from backend.services.cusum_detector import (
    get_alert_state as cusum_alert_state,
    get_accumulators as cusum_accumulators,
    reset as cusum_reset,
)
from backend.services.stuck_sensor import get_state as stuck_state
from backend.services.reading_store import (
    get_readings_for_period,
    get_buffered_readings_sync,
    clear_buffer_for_tag,
)
from backend.services.rules import rule_based_status
from backend.services.report_generation import ReportGenerator
from backend.services.report_export import report_attachment
from backend.services.work_order_service import (
    list_work_orders,
    create_work_order,
    update_work_order,
    get_work_order,
    create_work_order_from_alert,
)
from backend.models import WorkOrder, WorkOrderStatus
from config import settings

router = APIRouter()


def _report_payload(data: dict, export: str):
    ex = (export or "json").lower().strip()
    if ex == "json":
        return data
    if ex not in ("csv", "pdf"):
        raise HTTPException(400, "export must be json, csv, or pdf")
    try:
        body, mediatype, filename = report_attachment(data, ex)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return Response(
        content=body,
        media_type=mediatype,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _work_order_to_response(wo: WorkOrder) -> WorkOrderResponse:
    tag = wo.instrument.tag_number if wo.instrument else None
    return WorkOrderResponse(
        id=wo.id,
        work_order_number=wo.work_order_number,
        instrument_id=wo.instrument_id,
        instrument_tag=tag,
        title=wo.title,
        description=wo.description,
        work_type=wo.work_type,
        priority=wo.priority,
        status=wo.status,
        assigned_to=wo.assigned_to,
        due_date=wo.due_date,
        source=wo.source,
        created_at=wo.created_at,
        updated_at=wo.updated_at,
        completed_at=wo.completed_at,
    )


def _instrument_to_response(inst: Instrument) -> InstrumentResponse:
    from backend.services.anomaly_detection import is_model_trained
    return InstrumentResponse(
        id=inst.id,
        tag_number=inst.tag_number,
        instrument_type=inst.instrument_type,
        measured_variable=inst.measured_variable,
        unit=inst.unit,
        range_min=inst.range_min,
        range_max=inst.range_max,
        accuracy_class=inst.accuracy_class,
        location=inst.location,
        associated_equipment=inst.associated_equipment,
        calibration_interval_days=inst.calibration_interval_days,
        criticality=inst.criticality,
        nominal_value=inst.nominal_value,
        next_calibration_due=next_calibration_due(inst),
        calibration_overdue=is_calibration_overdue(inst),
        created_at=inst.created_at,
        ml_trained=is_model_trained(inst.tag_number),
    )


@router.get("/instruments", response_model=list[InstrumentResponse])
async def api_list_instruments(db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    instruments = await list_instruments(db)
    return [_instrument_to_response(inst) for inst in instruments]


@router.get("/instruments/live-readings")
async def api_live_readings(db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    """Return the most recent buffered reading for every registered instrument.

    Used by the dashboard live-readings panel (polls every few seconds).
    Must be defined before /instruments/{instrument_id} to avoid int-cast conflict.
    """
    instruments = await list_instruments(db)
    since = datetime.utcnow() - timedelta(minutes=5)
    result = []
    for inst in instruments:
        buf = get_buffered_readings_sync(inst.tag_number, since)
        if buf:
            ts, val = buf[-1]
            result.append({
                "tag_number": inst.tag_number,
                "instrument_type": inst.instrument_type,
                "value": round(val, 4),
                "unit": inst.unit or "",
                "timestamp": ts.isoformat() + "Z",
                "range_min": inst.range_min,
                "range_max": inst.range_max,
            })
        else:
            result.append({
                "tag_number": inst.tag_number,
                "instrument_type": inst.instrument_type,
                "value": None,
                "unit": inst.unit or "",
                "timestamp": None,
                "range_min": inst.range_min,
                "range_max": inst.range_max,
            })
    return result


@router.post("/instruments", response_model=InstrumentResponse)
async def api_create_instrument(body: InstrumentCreate, db: AsyncSession = Depends(get_db), _user: User = Depends(require_admin)):
    inst = await create_instrument(
        db,
        tag_number=body.tag_number,
        instrument_type=body.instrument_type,
        measured_variable=body.measured_variable,
        unit=body.unit,
        range_min=body.range_min,
        range_max=body.range_max,
        calibration_interval_days=body.calibration_interval_days,
        criticality=body.criticality,
        accuracy_class=body.accuracy_class,
        location=body.location,
        associated_equipment=body.associated_equipment,
        nominal_value=body.nominal_value,
    )
    # Re-load with calibrations; lazy-loading .calibrations causes MissingGreenlet in async.
    inst_loaded = await get_instrument_by_id(db, inst.id)
    if not inst_loaded:
        raise HTTPException(500, "Instrument created but could not be reloaded")
    return _instrument_to_response(inst_loaded)


@router.get("/instruments/{instrument_id}", response_model=InstrumentResponse)
async def api_get_instrument(instrument_id: int, db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    inst = await get_instrument_by_id(db, instrument_id)
    if not inst:
        raise HTTPException(404, "Instrument not found")
    return _instrument_to_response(inst)


@router.delete("/instruments/{instrument_id}", status_code=204)
async def api_delete_instrument(instrument_id: int, db: AsyncSession = Depends(get_db), _user: User = Depends(require_admin)):
    """Remove instrument, its readings/history/alerts, unlink work orders; drop ML buffer/model for tag."""
    tag = await delete_instrument_by_id(db, instrument_id)
    if tag is None:
        raise HTTPException(404, "Instrument not found")
    from backend.services.anomaly_detection import discard_trained_model
    discard_trained_model(tag)
    await clear_buffer_for_tag(tag)
    return Response(status_code=204)


@router.post("/calibrations", status_code=201)
async def api_add_calibration(body: CalibrationRecordCreate, db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    rec = await add_calibration_record(
        db,
        instrument_id=body.instrument_id,
        performed_at=body.performed_at,
        passed=body.passed,
        performed_by=body.performed_by,
        notes=body.notes,
        as_found_value=body.as_found_value,
        as_left_value=body.as_left_value,
        reference_value=body.reference_value,
        calibration_points=[p.model_dump() for p in body.calibration_points] if body.calibration_points else None,
    )
    await db.commit()
    # Reset CUSUM — instrument has been verified
    inst = await get_instrument_by_id(db, body.instrument_id)
    if inst:
        cusum_reset(inst.tag_number)
    return {"ok": True, "id": rec.id}


@router.post("/maintenance", status_code=201)
async def api_add_maintenance(body: MaintenanceRecordCreate, db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    await add_maintenance_record(
        db,
        instrument_id=body.instrument_id,
        action_type=body.action_type,
        description=body.description,
        technician=body.technician,
        trigger_source=body.trigger_source,
        notes=body.notes,
    )
    return {"ok": True}


@router.get("/instruments/{instrument_id}/status", response_model=InstrumentStatusResponse)
async def api_instrument_status(instrument_id: int, db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    inst = await get_instrument_by_id(db, instrument_id)
    if not inst:
        raise HTTPException(404, "Instrument not found")
    since = datetime.utcnow() - timedelta(seconds=settings.aggregation_window_seconds * 2)
    readings = get_buffered_readings_sync(inst.tag_number, since)
    last_value = readings[-1][1] if readings else None
    last_ts = readings[-1][0] if readings else None
    from backend.services.anomaly_detection import get_anomaly_state, get_anomaly_score
    from backend.services.health_index import compute_health_index
    rule_status = rule_based_status(last_value, inst) if last_value is not None else "normal"
    anomaly_state = get_anomaly_state(inst.tag_number)
    anomaly_score = get_anomaly_score(inst.tag_number)
    next_due = next_calibration_due(inst)
    cusum_st   = cusum_alert_state(inst.tag_number)
    c_pos, c_neg = cusum_accumulators(inst.tag_number)
    is_stuck   = stuck_state(inst.tag_number)
    health_index, health_label, recommendation = compute_health_index(
        instrument=inst,
        last_value=last_value,
        anomaly_score=anomaly_score,
        next_cal_due=next_due,
        cusum_state=cusum_st,
        is_stuck=is_stuck,
    )
    return InstrumentStatusResponse(
        tag_number=inst.tag_number,
        rule_based_status=rule_status,
        anomaly_state=anomaly_state,
        anomaly_score=anomaly_score,
        cusum_state=cusum_st,
        cusum_c_pos=c_pos,
        cusum_c_neg=c_neg,
        stuck_sensor=is_stuck,
        last_value=last_value,
        last_timestamp=last_ts,
        health_index=health_index,
        health_label=health_label,
        recommendation=recommendation,
    )


@router.get("/alerts", response_model=list[AlertSummary])
async def api_alerts(db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    """Aggregate alerts: calibration overdue, rule-based critical/warning, ML warning/critical."""
    from backend.services.anomaly_detection import get_anomaly_state
    alerts = []
    instruments = await list_instruments(db)
    for inst in instruments:
        due = next_calibration_due(inst)
        if due and due < datetime.utcnow():
            alerts.append(AlertSummary(
                tag_number=inst.tag_number,
                state="critical",
                source="rule",
                alert_type="calibration_overdue",
                message="Calibration overdue",
                timestamp=datetime.utcnow(),
            ))
        since = datetime.utcnow() - timedelta(seconds=settings.aggregation_window_seconds * 2)
        readings = get_buffered_readings_sync(inst.tag_number, since)
        if readings:
            last_val = readings[-1][1]
            last_ts = readings[-1][0]
            rule_status = rule_based_status(last_val, inst)
            if rule_status != "normal":
                alerts.append(AlertSummary(
                    tag_number=inst.tag_number,
                    state=rule_status,
                    source="rule",
                    alert_type="rule_violation",
                    message=f"Value {last_val} outside acceptable range",
                    timestamp=last_ts,
                ))
        ml_state = get_anomaly_state(inst.tag_number)
        if ml_state in ("warning", "critical"):
            alerts.append(AlertSummary(
                tag_number=inst.tag_number,
                state=ml_state,
                source="ml",
                alert_type="anomaly",
                message="Anomaly detected",
                timestamp=datetime.utcnow(),
            ))

        # CUSUM drift alert
        drift_st = cusum_alert_state(inst.tag_number)
        if drift_st in ("drift_high", "drift_low"):
            direction = "upward" if drift_st == "drift_high" else "downward"
            alerts.append(AlertSummary(
                tag_number=inst.tag_number,
                state="warning",
                source="drift",
                alert_type="drift",
                message=f"Sustained {direction} drift detected — calibration check recommended",
                timestamp=datetime.utcnow(),
            ))

        # Stuck sensor alert
        if stuck_state(inst.tag_number):
            alerts.append(AlertSummary(
                tag_number=inst.tag_number,
                state="critical",
                source="stuck",
                alert_type="instrument_fault",
                message="Sensor output is not changing - possible hardware fault or signal loss",
                timestamp=datetime.utcnow(),
            ))
    return alerts


@router.get("/readings/trends")
async def api_trends(tag_number: str, hours: int = 24, db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    """Time-series readings for a tag (for trend plots)."""
    end = datetime.utcnow()
    start = end - timedelta(hours=hours)
    rows = await get_readings_for_period(db, tag_number, start, end)
    return {"tag_number": tag_number, "readings": [{"timestamp": r[0].isoformat(), "value": r[1]} for r in rows]}


@router.post("/ml/train/{tag_number}")
async def api_ml_train(tag_number: str, db: AsyncSession = Depends(get_db), _user: User = Depends(require_admin)):
    """Train anomaly detection model for a tag using recent historical readings."""
    inst = await get_instrument_by_tag(db, tag_number)
    if not inst:
        raise HTTPException(404, "Instrument not found")
    end   = datetime.utcnow()
    start = end - timedelta(days=7)
    rows  = await get_readings_for_period(db, tag_number, start, end)
    window_sec = settings.aggregation_window_seconds
    windows: dict[int, list] = {}
    for ts, val in rows:
        bucket = int(ts.timestamp()) // window_sec * window_sec
        windows.setdefault(bucket, []).append((ts, val))
    import numpy as np
    from backend.services.feature_extraction import extract_features
    from backend.services.anomaly_detection import train_model
    features_list = []
    for bucket_readings in windows.values():
        f = extract_features(
            bucket_readings,
            nominal_value=inst.nominal_value,
            range_min=inst.range_min,
            range_max=inst.range_max,
        )
        if f:
            features_list.append(f)
    if len(features_list) < 10:
        raise HTTPException(400, "Insufficient data: need at least 10 time windows")
    X = np.array([list(f.values()) for f in features_list])
    version_id = train_model(tag_number, X)

    # Initialize CUSUM from the same training data
    from backend.services.cusum_detector import initialize_from_data as cusum_init
    cusum_init(tag_number, rows, nominal_value=inst.nominal_value)

    return {"ok": True, "tag_number": tag_number, "windows_used": len(features_list), "version_id": version_id}


@router.get("/ml/models/{tag_number}")
async def api_ml_versions(tag_number: str, db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    """List all trained model versions for a tag, newest first."""
    inst = await get_instrument_by_tag(db, tag_number)
    if not inst:
        raise HTTPException(404, "Instrument not found")
    from backend.services.anomaly_detection import get_model_versions
    return {"tag_number": tag_number, "versions": get_model_versions(tag_number)}


@router.post("/ml/models/{tag_number}/activate/{version_id}")
async def api_ml_activate(tag_number: str, version_id: str, db: AsyncSession = Depends(get_db), _user: User = Depends(require_admin)):
    """Activate a specific model version (rollback support)."""
    inst = await get_instrument_by_tag(db, tag_number)
    if not inst:
        raise HTTPException(404, "Instrument not found")
    from backend.services.anomaly_detection import activate_version
    if not activate_version(tag_number, version_id):
        raise HTTPException(404, "Version not found or model file missing")
    return {"ok": True, "tag_number": tag_number, "active_version": version_id}


@router.get("/dashboard/summary")
async def api_dashboard_summary(db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    """Summary for dashboard: total instruments, overdue count, alert count."""
    from backend.services.anomaly_detection import get_anomaly_state
    instruments = await list_instruments(db)
    overdue = sum(1 for i in instruments if is_calibration_overdue(i))
    alert_count = 0
    for inst in instruments:
        if is_calibration_overdue(inst):
            alert_count += 1
        if get_anomaly_state(inst.tag_number) in ("warning", "critical"):
            alert_count += 1
        if cusum_alert_state(inst.tag_number) in ("drift_high", "drift_low"):
            alert_count += 1
        if stuck_state(inst.tag_number):
            alert_count += 1
        since = datetime.utcnow() - timedelta(seconds=settings.aggregation_window_seconds * 2)
        readings = get_buffered_readings_sync(inst.tag_number, since)
        if readings and rule_based_status(readings[-1][1], inst) != "normal":
            alert_count += 1
    return {
        "total_instruments": len(instruments),
        "calibration_overdue": overdue,
        "active_alerts": alert_count,
    }


@router.get("/simulator/status")
async def api_simulator_status(_user: User = Depends(require_any_role)):
    """Whether the built-in MQTT simulator is running, plus which tags are active."""
    from backend.services.simulator import is_running as simulator_is_running, get_tags as simulator_get_tags
    running = simulator_is_running()
    tags = simulator_get_tags()
    return {
        "running": running,
        "tags": list(tags.keys()) if tags else [],
    }


class SimulatorStartBody(BaseModel):
    tags: list[str] | None = None  # tag numbers to simulate; None = all registered


@router.post("/simulator/start")
async def api_simulator_start(body: SimulatorStartBody | None = None, db: AsyncSession = Depends(get_db), _user: User = Depends(require_admin)):
    """Start the built-in MQTT simulator.

    Builds the tag list from registered instruments in the database.
    Optionally restricted to a subset by providing tag_numbers in the request body.
    """
    from backend.services.simulator import is_running as simulator_is_running, start as simulator_start
    if simulator_is_running():
        return {"ok": False, "message": "Simulator already running"}

    instruments = await list_instruments(db)
    selected = body.tags if (body and body.tags) else None

    tags: dict[str, tuple] = {}
    for inst in instruments:
        if selected is not None and inst.tag_number not in selected:
            continue
        rmin = inst.range_min if inst.range_min is not None else 0.0
        rmax = inst.range_max if inst.range_max is not None else 100.0
        nominal = inst.nominal_value if inst.nominal_value is not None else (rmin + rmax) / 2
        unit = inst.unit or ""
        tags[inst.tag_number] = (rmin, rmax, nominal, unit)

    if not tags:
        return {"ok": False, "message": "No matching instruments found in database"}

    simulator_start(tags=tags)
    return {"ok": True, "message": f"Simulator started for {len(tags)} instruments", "tags": list(tags.keys())}


@router.post("/simulator/stop")
async def api_simulator_stop(_user: User = Depends(require_admin)):
    """Stop the built-in MQTT simulator."""
    from backend.services.simulator import stop as simulator_stop
    if simulator_stop():
        return {"ok": True, "message": "Simulator stopped"}
    return {"ok": False, "message": "Simulator was not running"}


# ============================================================================
# REPORT ENDPOINTS
# ============================================================================


@router.get("/reports/calibration")
async def api_calibration_report(
    tag_number: str | None = None,
    include_history: bool = True,
    export: str = Query("json", description="json | csv | pdf"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_any_role),
):
    """
    Generate calibration compliance report.
    
    - Optionally filter to single instrument by tag_number
    - Include full calibration history if include_history=true
    - Use export=csv or export=pdf to download a file
    """
    data = await ReportGenerator.calibration_report(db, tag_number, include_history)
    return _report_payload(data, export)


@router.get("/reports/maintenance")
async def api_maintenance_report(
    tag_number: str | None = None,
    days_back: int = 90,
    export: str = Query("json", description="json | csv | pdf"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_any_role),
):
    """
    Generate maintenance activity report.
    
    - Optionally filter to single instrument by tag_number
    - Configurable historical period (default 90 days)
    - Includes activity breakdown by action type and trigger source
    """
    data = await ReportGenerator.maintenance_report(db, tag_number, days_back)
    return _report_payload(data, export)


@router.get("/reports/health-status")
async def api_health_status_report(
    tag_number: str | None = None,
    export: str = Query("json", description="json | csv | pdf"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_any_role),
):
    """
    Generate current health status report.
    
    - Optionally filter to single instrument by tag_number
    - Shows current health metrics and recent alerts
    """
    data = await ReportGenerator.health_status_report(db, tag_number)
    return _report_payload(data, export)


@router.get("/reports/anomalies")
async def api_anomaly_report(
    tag_number: str | None = None,
    days_back: int = 7,
    severity_threshold: str = "warning",
    export: str = Query("json", description="json | csv | pdf"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_any_role),
):
    """
    Generate ML anomaly detection activity report.
    
    - Optionally filter to single instrument by tag_number
    - Configurable historical period (default 7 days)
    - severity_threshold: 'warning' (all warnings+critical) or 'critical' (only critical)
    - Includes detected anomalies, frequency, and affected instruments
    """
    if severity_threshold not in ["warning", "critical"]:
        raise HTTPException(400, "severity_threshold must be 'warning' or 'critical'")
    data = await ReportGenerator.anomaly_report(
        db, tag_number, days_back, severity_threshold
    )
    return _report_payload(data, export)


@router.get("/reports/compliance")
async def api_compliance_report(
    export: str = Query("json", description="json | csv | pdf"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_any_role),
):
    """
    Generate operational compliance and risk assessment report.
    
    - Overall fleet compliance metrics (calibration %, overdue by criticality)
    - Active alert summary
    - Overall risk level assessment (LOW, MEDIUM, HIGH, CRITICAL)
    - Actionable recommendations based on compliance status
    """
    data = await ReportGenerator.compliance_report(db)
    return _report_payload(data, export)


@router.get("/reports/work-orders")
async def api_work_orders_report(
    days_back: int = 365,
    export: str = Query("json", description="json | csv | pdf"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_any_role),
):
    """Backlog and activity report for work orders."""
    data = await ReportGenerator.work_orders_report(db, days_back)
    return _report_payload(data, export)


# --- Work orders (CMMS) ---


@router.get("/work-orders", response_model=list[WorkOrderResponse])
async def api_list_work_orders(status: str | None = None, db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    st: WorkOrderStatus | None = None
    if status:
        try:
            st = WorkOrderStatus(status)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")
    orders = await list_work_orders(db, status=st)
    return [_work_order_to_response(wo) for wo in orders]


async def _notify_assignee(db: AsyncSession, wo_id: int, wo_number: str, wo_title: str, assigned_to: str | None) -> None:
    """Create a notification for the assigned technician if they exist in the users table."""
    if not assigned_to:
        return
    result = await db.execute(select(User).where(User.employee_number == assigned_to))
    user = result.scalar_one_or_none()
    if user:
        notif = Notification(
            user_id=user.id,
            type="work_order_assigned",
            title=f"Work Order Assigned: {wo_number}",
            message=wo_title,
            entity_id=wo_id,
        )
        db.add(notif)
        await db.commit()


@router.post("/work-orders", response_model=WorkOrderResponse)
async def api_create_work_order(body: WorkOrderCreate, db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    try:
        wo = await create_work_order(
            db,
            instrument_id=body.instrument_id,
            title=body.title,
            description=body.description,
            work_type=body.work_type,
            priority=body.priority,
            assigned_to=body.assigned_to,
            due_date=body.due_date,
            source="manual",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    wo_loaded = await get_work_order(db, wo.id)
    if not wo_loaded:
        raise HTTPException(500, "Work order created but could not be loaded")
    await _notify_assignee(db, wo.id, wo.work_order_number, wo.title, body.assigned_to)
    return _work_order_to_response(wo_loaded)


@router.post("/work-orders/from-alert", response_model=WorkOrderResponse)
async def api_work_order_from_alert(body: WorkOrderFromAlertCreate, db: AsyncSession = Depends(get_db), _user: User = Depends(require_any_role)):
    try:
        wo = await create_work_order_from_alert(
            db,
            tag_number=body.tag_number,
            message=body.message,
            alert_source=body.alert_source,
            state=body.state,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    wo_loaded = await get_work_order(db, wo.id)
    if not wo_loaded:
        raise HTTPException(500, "Work order created but could not be loaded")
    return _work_order_to_response(wo_loaded)


@router.patch("/work-orders/{work_order_id}", response_model=WorkOrderResponse)
async def api_patch_work_order(
    work_order_id: int,
    body: WorkOrderPatch,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_any_role),
):
    wo = await update_work_order(
        db,
        work_order_id,
        title=body.title,
        description=body.description,
        status=body.status,
        priority=body.priority,
        assigned_to=body.assigned_to,
        due_date=body.due_date,
    )
    if not wo:
        raise HTTPException(404, "Work order not found")
    # Notify if assignment changed
    if body.assigned_to is not None:
        await _notify_assignee(db, work_order_id, wo.work_order_number, wo.title, body.assigned_to)
    wo = await get_work_order(db, work_order_id)
    assert wo is not None
    return _work_order_to_response(wo)


# ============================================================================
# NOTIFICATION ENDPOINTS
# ============================================================================


@router.get("/notifications/count")
async def api_notification_count(db: AsyncSession = Depends(get_db), current_user: User = Depends(require_any_role)):
    result = await db.execute(
        select(Notification).where(
            Notification.user_id == current_user.id,
            Notification.is_read == False,  # noqa: E712
        )
    )
    unread = len(result.scalars().all())
    return {"unread": unread}


@router.get("/notifications", response_model=list[NotificationResponse])
async def api_list_notifications(db: AsyncSession = Depends(get_db), current_user: User = Depends(require_any_role)):
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    )
    return result.scalars().all()


@router.post("/notifications/{notification_id}/read", status_code=200)
async def api_mark_notification_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_role),
):
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(404, "Notification not found")
    notif.is_read = True
    await db.commit()
    return {"ok": True}


# ============================================================================
# PRINT DOCUMENT ENDPOINTS
# ============================================================================

from fastapi.responses import HTMLResponse
from backend.services.print_documents import work_order_html, calibration_certificate_html, calibration_certificate_blank_html
from sqlalchemy.orm import selectinload as _sil


@router.get("/print/work-order/{work_order_id}", response_class=HTMLResponse)
async def api_print_work_order(
    work_order_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_any_role),
):
    """Return a self-contained, print-ready HTML work order document."""
    result = await db.execute(
        select(WorkOrder)
        .options(_sil(WorkOrder.instrument))
        .where(WorkOrder.id == work_order_id)
    )
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(404, "Work order not found")
    return HTMLResponse(content=work_order_html(wo))


@router.get("/print/calibration/{calibration_id}", response_class=HTMLResponse)
async def api_print_calibration_certificate(
    calibration_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_any_role),
):
    """Return a self-contained, print-ready ISO/IEC 17025 calibration certificate."""
    from backend.models import CalibrationRecord
    result = await db.execute(
        select(CalibrationRecord)
        .options(_sil(CalibrationRecord.instrument))
        .where(CalibrationRecord.id == calibration_id)
    )
    cal = result.scalar_one_or_none()
    if not cal:
        raise HTTPException(404, "Calibration record not found")
    return HTMLResponse(content=calibration_certificate_html(cal, cal.instrument))


@router.get("/print/calibration/instrument/{instrument_id}", response_class=HTMLResponse)
async def api_print_calibration_blank(
    instrument_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_any_role),
):
    """Return a blank calibration certificate pre-filled with instrument data (field use)."""
    from backend.models import CalibrationRecord
    inst = await db.get(Instrument, instrument_id)
    if not inst:
        raise HTTPException(404, "Instrument not found")
    # Load latest calibration record for schedule reference
    cal_result = await db.execute(
        select(CalibrationRecord)
        .where(CalibrationRecord.instrument_id == instrument_id)
        .order_by(CalibrationRecord.performed_at.desc())
        .limit(1)
    )
    latest_cal = cal_result.scalar_one_or_none()
    return HTMLResponse(content=calibration_certificate_blank_html(inst, latest_cal))
