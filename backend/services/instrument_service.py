"""Instrument register and calibration/maintenance logic."""
from datetime import datetime, timedelta
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import (
    AlertRecord,
    CalibrationRecord,
    Instrument,
    InstrumentType,
    CriticalityLevel,
    MaintenanceRecord,
    RawReading,
    WorkOrder,
)


async def get_instrument_by_tag(session: AsyncSession, tag_number: str) -> Instrument | None:
    """Get instrument by tag number."""
    result = await session.execute(
        select(Instrument).options(selectinload(Instrument.calibrations)).where(Instrument.tag_number == tag_number)
    )
    return result.scalar_one_or_none()


async def get_instrument_by_id(session: AsyncSession, instrument_id: int) -> Instrument | None:
    """Get instrument by id."""
    result = await session.execute(
        select(Instrument).options(selectinload(Instrument.calibrations)).where(Instrument.id == instrument_id)
    )
    return result.scalar_one_or_none()


async def list_instruments(session: AsyncSession) -> list[Instrument]:
    """List all instruments (calibrations eager-loaded for async-safe due dates)."""
    result = await session.execute(
        select(Instrument)
        .options(selectinload(Instrument.calibrations))
        .order_by(Instrument.tag_number)
    )
    return list(result.scalars().all())


async def create_instrument(
    session: AsyncSession,
    tag_number: str,
    instrument_type: InstrumentType,
    measured_variable: str,
    unit: str,
    range_min: float,
    range_max: float,
    calibration_interval_days: int,
    criticality: CriticalityLevel,
    accuracy_class: str | None = None,
    location: str | None = None,
    associated_equipment: str | None = None,
    nominal_value: float | None = None,
) -> Instrument:
    """Create a new instrument in the register."""
    inst = Instrument(
        tag_number=tag_number,
        instrument_type=instrument_type,
        measured_variable=measured_variable,
        unit=unit,
        range_min=range_min,
        range_max=range_max,
        accuracy_class=accuracy_class,
        location=location,
        associated_equipment=associated_equipment,
        calibration_interval_days=calibration_interval_days,
        criticality=criticality,
        nominal_value=nominal_value,
    )
    session.add(inst)
    await session.flush()
    return inst


def next_calibration_due(inst: Instrument) -> datetime | None:
    """Compute next calibration due date from latest calibration record."""
    if not inst.calibrations:
        return None
    latest = inst.calibrations[0]
    return latest.due_next_at


def is_calibration_overdue(inst: Instrument) -> bool:
    """True if next calibration due date is in the past."""
    due = next_calibration_due(inst)
    return due is not None and due < datetime.utcnow()


async def add_calibration_record(
    db: AsyncSession,
    *,
    instrument_id: int,
    performed_at: datetime,
    passed: bool,
    performed_by: str | None = None,
    notes: str | None = None,
    as_found_value: float | None = None,
    as_left_value: float | None = None,
    reference_value: float | None = None,
    calibration_points: list | None = None,
) -> CalibrationRecord:
    """Add a calibration record and set due_next_at from instrument interval.

    If calibration_points (5-point ISA-51.1 list) is supplied, errors are computed
    for each point and the scalar as_found/as_left fields are derived from the 50 %
    row so that legacy code continues to work.
    """
    inst = await get_instrument_by_id(db, instrument_id)
    if not inst:
        raise ValueError("Instrument not found")
    due_next = performed_at + timedelta(days=inst.calibration_interval_days)
    span = inst.range_max - inst.range_min

    # ── Enrich and normalise 5-point data ──────────────────────────────────────
    stored_points = None
    if calibration_points:
        stored_points = []
        for pt in calibration_points:
            pt_dict = pt if isinstance(pt, dict) else pt.model_dump()
            ref = pt_dict.get("ref_val") or (inst.range_min + span * pt_dict["pct"] / 100.0)
            af  = pt_dict.get("as_found")
            al  = pt_dict.get("as_left")
            efe = round((af - ref) / span * 100.0, 4) if (af is not None and span > 0) else None
            ele = round((al - ref) / span * 100.0, 4) if (al is not None and span > 0) else None
            stored_points.append({
                "pct":          pt_dict["pct"],
                "ref_val":      round(ref, 6),
                "as_found":     af,
                "as_left":      al,
                "err_found_pct": efe,
                "err_left_pct":  ele,
            })
        # Derive scalar fields from the 50 % test point for backward compat
        mid = next((p for p in stored_points if p["pct"] == 50.0), None)
        if mid:
            as_found_value  = mid["as_found"]
            as_left_value   = mid["as_left"]
            reference_value = mid["ref_val"]

    # ── Scalar errors (used when no 5-point data, or as summary) ───────────────
    error_found = None
    error_left  = None
    if reference_value is not None and span and span > 0:
        if as_found_value is not None:
            error_found = round((as_found_value - reference_value) / span * 100.0, 4)
        if as_left_value is not None:
            error_left = round((as_left_value - reference_value) / span * 100.0, 4)

    rec = CalibrationRecord(
        instrument_id=instrument_id,
        performed_at=performed_at,
        due_next_at=due_next,
        passed=passed,
        performed_by=performed_by,
        notes=notes,
        as_found_value=as_found_value,
        as_left_value=as_left_value,
        reference_value=reference_value,
        error_found_pct=error_found,
        error_left_pct=error_left,
        calibration_points=stored_points,
    )
    db.add(rec)
    await db.flush()
    return rec


async def delete_instrument_by_id(session: AsyncSession, instrument_id: int) -> str | None:
    """
    Remove instrument: delete calibrations, maintenance, alerts, raw readings for tag;
    unlink work orders; return tag_number if removed, else None.
    """
    inst = await session.get(Instrument, instrument_id)
    if not inst:
        return None
    tag = inst.tag_number
    await session.execute(delete(RawReading).where(RawReading.tag_number == tag))
    await session.execute(delete(AlertRecord).where(AlertRecord.instrument_id == instrument_id))
    await session.execute(delete(CalibrationRecord).where(CalibrationRecord.instrument_id == instrument_id))
    await session.execute(delete(MaintenanceRecord).where(MaintenanceRecord.instrument_id == instrument_id))
    await session.execute(
        update(WorkOrder)
        .where(WorkOrder.instrument_id == instrument_id)
        .values(instrument_id=None)
    )
    session.delete(inst)
    await session.flush()
    return tag


async def add_maintenance_record(
    session: AsyncSession,
    instrument_id: int,
    action_type: str,
    description: str | None = None,
    technician: str | None = None,
    trigger_source: str | None = None,
    notes: str | None = None,
) -> MaintenanceRecord:
    """Create a corrective maintenance record."""
    rec = MaintenanceRecord(
        instrument_id=instrument_id,
        action_type=action_type,
        description=description,
        technician=technician,
        trigger_source=trigger_source,
        notes=notes,
    )
    session.add(rec)
    await session.flush()
    return rec
