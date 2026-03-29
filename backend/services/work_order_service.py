"""Work order creation and lifecycle."""
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import Instrument, WorkOrder, WorkOrderPriority, WorkOrderStatus


async def list_work_orders(
    session: AsyncSession,
    status: WorkOrderStatus | None = None,
) -> list[WorkOrder]:
    stmt = (
        select(WorkOrder)
        .options(selectinload(WorkOrder.instrument))
        .order_by(WorkOrder.created_at.desc())
    )
    if status is not None:
        stmt = stmt.where(WorkOrder.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_work_order(session: AsyncSession, work_order_id: int) -> WorkOrder | None:
    result = await session.execute(
        select(WorkOrder)
        .options(selectinload(WorkOrder.instrument))
        .where(WorkOrder.id == work_order_id)
    )
    return result.scalar_one_or_none()


async def create_work_order(
    session: AsyncSession,
    instrument_id: int | None,
    title: str,
    description: str | None,
    work_type: str,
    priority: WorkOrderPriority,
    assigned_to: str | None,
    due_date: datetime | None,
    source: str,
    source_detail: str | None = None,
) -> WorkOrder:
    if instrument_id is not None:
        r = await session.execute(select(Instrument).where(Instrument.id == instrument_id))
        if r.scalar_one_or_none() is None:
            raise ValueError("Instrument not found")
    wo = WorkOrder(
        work_order_number=f"PENDING-{secrets.token_hex(6)}",
        instrument_id=instrument_id,
        title=title,
        description=description,
        work_type=work_type,
        priority=priority,
        status=WorkOrderStatus.OPEN,
        assigned_to=assigned_to,
        due_date=due_date,
        source=source,
        source_detail=source_detail,
    )
    session.add(wo)
    await session.flush()
    wo.work_order_number = f"WO-{wo.id:06d}"
    await session.flush()
    return wo


async def update_work_order(
    session: AsyncSession,
    work_order_id: int,
    *,
    title: str | None = None,
    description: str | None = None,
    status: WorkOrderStatus | None = None,
    priority: WorkOrderPriority | None = None,
    assigned_to: str | None = None,
    due_date: datetime | None = None,
) -> WorkOrder | None:
    wo = await get_work_order(session, work_order_id)
    if not wo:
        return None
    if title is not None:
        wo.title = title
    if description is not None:
        wo.description = description
    if status is not None:
        wo.status = status
        if status == WorkOrderStatus.COMPLETED:
            wo.completed_at = datetime.utcnow()
        elif wo.completed_at is not None and status != WorkOrderStatus.COMPLETED:
            wo.completed_at = None
    if priority is not None:
        wo.priority = priority
    if assigned_to is not None:
        wo.assigned_to = assigned_to
    if due_date is not None:
        wo.due_date = due_date
    wo.updated_at = datetime.utcnow()
    await session.flush()
    return wo


def _infer_work_order_from_alert(message: str, state: str) -> tuple[str, WorkOrderPriority, str]:
    low = message.lower()
    priority = WorkOrderPriority.HIGH if state == "critical" else WorkOrderPriority.MEDIUM
    if "calibration" in low and "overdue" in low:
        return "calibration", WorkOrderPriority.URGENT if state == "critical" else WorkOrderPriority.HIGH, "Calibration due / overdue"
    if "anomaly" in low:
        return "corrective", WorkOrderPriority.HIGH if state == "critical" else WorkOrderPriority.MEDIUM, "ML anomaly response"
    if "outside" in low or "range" in low:
        return "corrective", priority, "Process / range deviation"
    return "corrective", priority, "Alert follow-up"


async def create_work_order_from_alert(
    session: AsyncSession,
    tag_number: str,
    message: str,
    alert_source: str,
    state: str,
) -> WorkOrder:
    inst = await session.execute(select(Instrument).where(Instrument.tag_number == tag_number))
    instrument = inst.scalar_one_or_none()
    if not instrument:
        raise ValueError("Instrument not found for tag")
    work_type, priority, title_prefix = _infer_work_order_from_alert(message, state)
    title = f"{title_prefix}: {tag_number}"
    detail = f"source={alert_source}; state={state}\n{message}"
    return await create_work_order(
        session,
        instrument_id=instrument.id,
        title=title,
        description=message,
        work_type=work_type,
        priority=priority,
        assigned_to=None,
        due_date=None,
        source="alert",
        source_detail=detail,
    )
