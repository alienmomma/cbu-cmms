"""Seed sample instruments and optionally initial calibration. Run once after DB init."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import AsyncSessionLocal, init_db
from backend.models import InstrumentType, CriticalityLevel
from backend.services.instrument_service import create_instrument, add_calibration_record
from datetime import datetime, timedelta


async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        instruments = [
            ("PT-101", InstrumentType.PRESSURE, "Pressure", "bar", 0, 10, 90, CriticalityLevel.HIGH, 7.5),
            ("TT-201", InstrumentType.TEMPERATURE, "Temperature", "°C", -20, 120, 180, CriticalityLevel.HIGH, 80),
            ("FT-301", InstrumentType.FLOW, "Flow", "m³/h", 0, 100, 90, CriticalityLevel.MEDIUM, 50),
            ("LT-401", InstrumentType.LEVEL, "Level", "%", 0, 100, 180, CriticalityLevel.CRITICAL, 60),
        ]
        for tag, itype, var, unit, rmin, rmax, cal_days, crit, nominal in instruments:
            try:
                inst = await create_instrument(
                    session,
                    tag_number=tag,
                    instrument_type=itype,
                    measured_variable=var,
                    unit=unit,
                    range_min=rmin,
                    range_max=rmax,
                    calibration_interval_days=cal_days,
                    criticality=crit,
                    nominal_value=nominal,
                    location="Process area",
                )
                # One calibration in the past so next_due is set
                performed = datetime.utcnow() - timedelta(days=30)
                await add_calibration_record(session, inst.id, performed, True, performed_by="seed")
            except Exception as e:
                print(f"Skip {tag}: {e}")
        await session.commit()
    print("Seeded instruments and calibration records.")


if __name__ == "__main__":
    asyncio.run(main())
