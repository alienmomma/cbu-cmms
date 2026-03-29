"""Report generation service for CIMMS - compliance, health, maintenance analytics."""
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Instrument,
    CalibrationRecord,
    MaintenanceRecord,
    RawReading,
    AlertRecord,
    WorkOrder,
    WorkOrderStatus,
)


class ReportGenerator:
    """Generate operational and compliance reports."""

    @staticmethod
    async def calibration_report(
        session: AsyncSession,
        tag_number: Optional[str] = None,
        include_history: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate calibration report.
        
        Args:
            session: DB session
            tag_number: Optional - filter to single instrument
            include_history: Include past calibration records
            
        Returns:
            Report dict with compliance metrics and history
        """
        if tag_number:
            # Single instrument
            stmt = select(Instrument).where(Instrument.tag_number == tag_number)
            result = await session.execute(stmt)
            instruments = [result.scalars().first()]
        else:
            # All instruments
            stmt = select(Instrument)
            result = await session.execute(stmt)
            instruments = result.scalars().all()

        if not instruments or instruments[0] is None:
            return {"error": "No instruments found"}

        report_data = {
            "report_type": "Calibration Report",
            "generated_at": datetime.utcnow().isoformat(),
            "instruments": [],
            "summary": {
                "total_instruments": len(instruments),
                "due_in_7_days": 0,
                "due_in_30_days": 0,
                "overdue": 0,
                "up_to_date": 0,
            },
        }

        now = datetime.utcnow()
        seven_days = now + timedelta(days=7)
        thirty_days = now + timedelta(days=30)

        for inst in instruments:
            # Get latest calibration
            stmt = (
                select(CalibrationRecord)
                .where(CalibrationRecord.instrument_id == inst.id)
                .order_by(desc(CalibrationRecord.performed_at))
                .limit(1)
            )
            result = await session.execute(stmt)
            latest_cal = result.scalars().first()

            # Get all calibrations if requested
            cal_history = []
            if include_history:
                stmt = (
                    select(CalibrationRecord)
                    .where(CalibrationRecord.instrument_id == inst.id)
                    .order_by(desc(CalibrationRecord.performed_at))
                )
                result = await session.execute(stmt)
                cals = result.scalars().all()
                cal_history = [
                    {
                        "performed_at": cal.performed_at.isoformat(),
                        "due_next_at": cal.due_next_at.isoformat(),
                        "passed": cal.passed,
                        "performed_by": cal.performed_by,
                        "notes": cal.notes,
                    }
                    for cal in cals
                ]

            # Determine status
            if latest_cal and latest_cal.due_next_at:
                due_next = latest_cal.due_next_at
                if due_next < now:
                    status = "OVERDUE"
                    report_data["summary"]["overdue"] += 1
                elif due_next < seven_days:
                    status = "DUE_IN_7_DAYS"
                    report_data["summary"]["due_in_7_days"] += 1
                elif due_next < thirty_days:
                    status = "DUE_IN_30_DAYS"
                    report_data["summary"]["due_in_30_days"] += 1
                else:
                    status = "UP_TO_DATE"
                    report_data["summary"]["up_to_date"] += 1
            else:
                status = "NEVER_CALIBRATED"
                report_data["summary"]["overdue"] += 1

            inst_report = {
                "tag_number": inst.tag_number,
                "instrument_type": inst.instrument_type,
                "location": inst.location,
                "calibration_interval_days": inst.calibration_interval_days,
                "criticality": inst.criticality,
                "status": status,
                "last_calibration": (
                    latest_cal.performed_at.isoformat() if latest_cal else None
                ),
                "next_due": (
                    latest_cal.due_next_at.isoformat() if latest_cal else None
                ),
                "days_until_due": (
                    (latest_cal.due_next_at - now).days
                    if latest_cal and latest_cal.due_next_at
                    else None
                ),
            }

            if include_history:
                inst_report["calibration_history"] = cal_history

            report_data["instruments"].append(inst_report)

        return report_data

    @staticmethod
    async def maintenance_report(
        session: AsyncSession,
        tag_number: Optional[str] = None,
        days_back: int = 90,
    ) -> Dict[str, Any]:
        """
        Generate maintenance activity report.
        
        Args:
            session: DB session
            tag_number: Optional - filter to single instrument
            days_back: Historical range (days)
            
        Returns:
            Report with maintenance records and analytics
        """
        cutoff = datetime.utcnow() - timedelta(days=days_back)

        query = select(MaintenanceRecord).where(
            MaintenanceRecord.created_at >= cutoff
        )

        if tag_number:
            stmt = select(Instrument).where(Instrument.tag_number == tag_number)
            result = await session.execute(stmt)
            inst = result.scalars().first()
            if not inst:
                return {"error": "Instrument not found"}
            query = query.where(MaintenanceRecord.instrument_id == inst.id)

        result = await session.execute(query.order_by(desc(MaintenanceRecord.created_at)))
        records = result.scalars().all()

        # Analyze
        action_type_counts = {}
        trigger_source_counts = {}
        resolved_count = 0
        avg_resolution_time = None

        resolution_times = []
        for rec in records:
            action_type_counts[rec.action_type] = (
                action_type_counts.get(rec.action_type, 0) + 1
            )
            trigger_source_counts[rec.trigger_source] = (
                trigger_source_counts.get(rec.trigger_source, 0) + 1
            )

            if rec.resolved_at:
                resolved_count += 1
                res_time = (rec.resolved_at - rec.created_at).total_seconds() / 3600
                resolution_times.append(res_time)

        if resolution_times:
            avg_resolution_time = sum(resolution_times) / len(resolution_times)

        report_data = {
            "report_type": "Maintenance Report",
            "generated_at": datetime.utcnow().isoformat(),
            "period_days": days_back,
            "summary": {
                "total_records": len(records),
                "resolved": resolved_count,
                "pending": len(records) - resolved_count,
                "avg_resolution_hours": round(avg_resolution_time, 2)
                if avg_resolution_time
                else None,
                "action_type_breakdown": action_type_counts,
                "trigger_source_breakdown": trigger_source_counts,
            },
            "records": [],
        }

        # Build records list with instrument tags
        for rec in records:
            inst = await session.get(Instrument, rec.instrument_id)
            report_data["records"].append({
                "created_at": rec.created_at.isoformat(),
                "instrument_tag": inst.tag_number if inst else None,
                "action_type": rec.action_type,
                "description": rec.description,
                "technician": rec.technician,
                "resolved_at": rec.resolved_at.isoformat()
                if rec.resolved_at
                else None,
                "trigger_source": rec.trigger_source,
            })

        return report_data

    @staticmethod
    async def health_status_report(
        session: AsyncSession,
        tag_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate current and historical health status report.
        
        Args:
            session: DB session
            tag_number: Optional - filter to single instrument
            
        Returns:
            Report with health metrics and trends
        """
        if tag_number:
            stmt = select(Instrument).where(Instrument.tag_number == tag_number)
            result = await session.execute(stmt)
            instruments = [result.scalars().first()]
        else:
            stmt = select(Instrument)
            result = await session.execute(stmt)
            instruments = result.scalars().all()

        if not instruments or instruments[0] is None:
            return {"error": "No instruments found"}

        report_data = {
            "report_type": "Health Status Report",
            "generated_at": datetime.utcnow().isoformat(),
            "instruments": [],
            "summary": {
                "excellent": 0,
                "good": 0,
                "fair": 0,
                "attention": 0,
                "poor": 0,
                "critical": 0,
            },
        }

        for inst in instruments:
            # Get alerts for this instrument
            stmt = (
                select(AlertRecord)
                .where(AlertRecord.instrument_id == inst.id)
                .order_by(desc(AlertRecord.created_at))
                .limit(10)
            )
            result = await session.execute(stmt)
            recent_alerts = result.scalars().all()

            inst_report = {
                "tag_number": inst.tag_number,
                "instrument_type": inst.instrument_type,
                "location": inst.location,
                "nominal_value": inst.nominal_value,
                "recent_alerts": [
                    {
                        "timestamp": alert.created_at.isoformat(),
                        "alert_type": alert.alert_type,
                        "severity": alert.severity,
                        "message": alert.message,
                    }
                    for alert in recent_alerts
                ],
            }

            report_data["instruments"].append(inst_report)

        return report_data

    @staticmethod
    async def anomaly_report(
        session: AsyncSession,
        tag_number: Optional[str] = None,
        days_back: int = 7,
        severity_threshold: str = "warning",
    ) -> Dict[str, Any]:
        """
        Generate ML anomaly detection activity report.
        
        Args:
            session: DB session
            tag_number: Optional - filter to single instrument
            days_back: Historical range (days)
            severity_threshold: 'warning' or 'critical' minimum
            
        Returns:
            Report with anomaly trends and statistics
        """
        cutoff = datetime.utcnow() - timedelta(days=days_back)

        query = select(AlertRecord).where(
            (AlertRecord.created_at >= cutoff)
            & (AlertRecord.alert_type == "anomaly")
        )

        if tag_number:
            stmt = select(Instrument).where(Instrument.tag_number == tag_number)
            result = await session.execute(stmt)
            inst = result.scalars().first()
            if not inst:
                return {"error": "Instrument not found"}
            query = query.where(AlertRecord.instrument_id == inst.id)

        if severity_threshold == "critical":
            query = query.where(AlertRecord.severity == "critical")
        else:
            query = query.where(AlertRecord.severity.in_(["warning", "critical"]))

        result = await session.execute(
            query.order_by(desc(AlertRecord.created_at))
        )
        alerts = result.scalars().all()

        severity_breakdown = {}
        for alert in alerts:
            severity_breakdown[alert.severity] = (
                severity_breakdown.get(alert.severity, 0) + 1
            )

        report_data = {
            "report_type": "Anomaly Detection Report",
            "generated_at": datetime.utcnow().isoformat(),
            "period_days": days_back,
            "summary": {
                "total_anomalies_detected": len(alerts),
                "severity_breakdown": severity_breakdown,
                "affected_instruments": len(set(a.instrument_id for a in alerts)),
            },
            "alerts": [],
        }

        # Build alerts list with instrument tags
        for alert in alerts:
            inst = await session.get(Instrument, alert.instrument_id)
            report_data["alerts"].append({
                "timestamp": alert.created_at.isoformat(),
                "instrument_tag": inst.tag_number if inst else None,
                "severity": alert.severity,
                "message": alert.message,
            })

        return report_data

    @staticmethod
    async def compliance_report(session: AsyncSession) -> Dict[str, Any]:
        """
        Generate operational compliance and risk assessment report.
        
        Args:
            session: DB session
            
        Returns:
            Report with compliance metrics and risk assessment
        """
        # Get all instruments
        stmt = select(Instrument)
        result = await session.execute(stmt)
        all_instruments = result.scalars().all()

        total = len(all_instruments)
        overdue_cals = 0
        overdue_by_criticality = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        active_alerts = 0
        instruments_with_alerts = set()

        now = datetime.utcnow()

        for inst in all_instruments:
            # Check calibration status
            stmt = (
                select(CalibrationRecord)
                .where(CalibrationRecord.instrument_id == inst.id)
                .order_by(desc(CalibrationRecord.performed_at))
                .limit(1)
            )
            result = await session.execute(stmt)
            latest_cal = result.scalars().first()

            if not latest_cal or latest_cal.due_next_at < now:
                overdue_cals += 1
                overdue_by_criticality[inst.criticality] += 1

            # Check active alerts
            stmt = (
                select(func.count(AlertRecord.id)).where(
                    (AlertRecord.instrument_id == inst.id)
                    & (AlertRecord.severity.in_(["warning", "critical"]))
                )
            )
            result = await session.execute(stmt)
            alert_count = result.scalar()
            if alert_count > 0:
                active_alerts += alert_count
                instruments_with_alerts.add(inst.id)

        compliance_percentage = (
            ((total - overdue_cals) / total * 100) if total > 0 else 0
        )
        risk_level = (
            "CRITICAL"
            if overdue_by_criticality["critical"] > 0
            or overdue_by_criticality["high"] > 2
            else "HIGH" if overdue_cals > total * 0.2
            else "MEDIUM" if overdue_cals > 0
            else "LOW"
        )

        report_data = {
            "report_type": "Compliance & Risk Report",
            "generated_at": datetime.utcnow().isoformat(),
            "summary": {
                "total_instruments": total,
                "calibration_compliance_percentage": round(compliance_percentage, 2),
                "overdue_calibrations": overdue_cals,
                "overdue_by_criticality": overdue_by_criticality,
                "active_alerts": active_alerts,
                "instruments_with_active_alerts": len(instruments_with_alerts),
                "overall_risk_level": risk_level,
            },
            "recommendations": _generate_recommendations(
                overdue_cals, total, overdue_by_criticality, active_alerts
            ),
        }

        return report_data

    @staticmethod
    async def work_orders_report(session: AsyncSession, days_back: int = 365) -> Dict[str, Any]:
        """Work order backlog and throughput summary (CMMS)."""
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        result = await session.execute(select(WorkOrder))
        all_rows = list(result.scalars().all())

        by_status: Dict[str, int] = {s.value: 0 for s in WorkOrderStatus}
        by_priority: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for wo in all_rows:
            by_status[wo.status.value] = by_status.get(wo.status.value, 0) + 1
            by_priority[wo.priority.value] = by_priority.get(wo.priority.value, 0) + 1
            by_type[wo.work_type] = by_type.get(wo.work_type, 0) + 1

        created_in_period = [w for w in all_rows if w.created_at >= cutoff]
        completed_in_period = sum(
            1
            for w in all_rows
            if w.completed_at is not None and w.completed_at >= cutoff
        )
        open_count = sum(
            1
            for wo in all_rows
            if wo.status in (WorkOrderStatus.OPEN, WorkOrderStatus.IN_PROGRESS)
        )

        recent = sorted(all_rows, key=lambda w: w.created_at, reverse=True)[:50]
        tag_map: Dict[int, str] = {}
        if recent:
            inst_ids = {w.instrument_id for w in recent if w.instrument_id}
            if inst_ids:
                ir = await session.execute(select(Instrument).where(Instrument.id.in_(inst_ids)))
                for inst in ir.scalars().all():
                    tag_map[inst.id] = inst.tag_number

        return {
            "report_type": "Work Orders Report",
            "generated_at": datetime.utcnow().isoformat(),
            "period_days": days_back,
            "summary": {
                "total_all_time": len(all_rows),
                "created_in_period": len(created_in_period),
                "open_and_in_progress": open_count,
                "completed_in_period": completed_in_period,
                "by_status": by_status,
                "by_priority": by_priority,
                "by_work_type": by_type,
            },
            "work_orders": [
                {
                    "work_order_number": w.work_order_number,
                    "title": w.title,
                    "instrument_tag": tag_map.get(w.instrument_id) if w.instrument_id else None,
                    "work_type": w.work_type,
                    "priority": w.priority.value,
                    "status": w.status.value,
                    "source": w.source,
                    "assigned_to": w.assigned_to,
                    "due_date": w.due_date.isoformat() if w.due_date else None,
                    "created_at": w.created_at.isoformat(),
                    "completed_at": w.completed_at.isoformat() if w.completed_at else None,
                }
                for w in recent
            ],
        }


def _generate_recommendations(
    overdue_count: int, total: int, by_criticality: Dict, active_alerts: int
) -> List[str]:
    """Generate actionable recommendations based on compliance metrics."""
    recs = []

    if by_criticality.get("critical", 0) > 0:
        recs.append(
            f"URGENT: {by_criticality['critical']} CRITICAL instruments have overdue calibrations"
        )

    if by_criticality.get("high", 0) > 0:
        recs.append(
            f"Schedule calibrations for {by_criticality['high']} HIGH priority instruments within 7 days"
        )

    if overdue_count > total * 0.2:
        recs.append(
            f"Calibration compliance below 80% ({round((total-overdue_count)/total*100, 1)}%). "
            "Implement preventive maintenance plan."
        )

    if active_alerts > 50:
        recs.append(
            f"High anomaly alert volume ({active_alerts}). Review ML model thresholds and sensor health."
        )

    if not recs:
        recs.append("All systems operating within compliance parameters.")

    return recs
