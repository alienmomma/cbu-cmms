"""The Copperbelt University CMMS — FastAPI application and MQTT integration."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import settings
from backend.database import init_db, get_db, AsyncSessionLocal
from backend.services.mqtt_ingestion import start_mqtt_client, get_reading_queue
from backend.services.reading_store import append_reading
from backend.services.instrument_service import get_instrument_by_tag
from api import router as api_router
from api.auth_routes import router as auth_router


async def on_reading(tag_number: str, timestamp: datetime, value: float, unit: str | None):
    """Handle one MQTT reading: persist, update anomaly, CUSUM, stuck sensor."""
    async with AsyncSessionLocal() as session:
        try:
            await append_reading(session, tag_number, timestamp, value, unit)
            await session.commit()
        except Exception as e:
            await session.rollback()
            print(f"Append reading error: {e}")

    async with AsyncSessionLocal() as session:
        inst = await get_instrument_by_tag(session, tag_number)

    if inst:
        # Isolation Forest anomaly detection (lazy import — numpy/sklearn are heavy)
        from backend.services.anomaly_detection import update_anomaly_from_buffer
        update_anomaly_from_buffer(
            tag_number,
            inst.nominal_value,
            range_min=inst.range_min,
            range_max=inst.range_max,
        )
        # CUSUM drift detection
        from backend.services.cusum_detector import update as cusum_update
        cusum_update(
            tag_number,
            value,
            range_min=inst.range_min,
            range_max=inst.range_max,
        )
        # Stuck sensor detection
        from backend.services.stuck_sensor import check as stuck_check
        span = inst.range_max - inst.range_min
        stuck_check(tag_number, value, range_span=span)


async def drain_mqtt_queue():
    """Background task: drain reading queue and process each reading."""
    q = get_reading_queue()
    loop = asyncio.get_event_loop()
    while True:
        try:
            item = await loop.run_in_executor(None, q.get)
            await on_reading(*item)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Queue drain error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        start_mqtt_client()
    except (ConnectionRefusedError, OSError) as e:
        print(f"MQTT broker not available ({e}). Running without live data ingestion.")
    task = asyncio.create_task(drain_mqtt_queue())
    if settings.simulator_enabled:
        from backend.services.simulator import start as start_simulator
        if start_simulator():
            print("Simulator started (SIMULATOR_ENABLED=true). Publishing to MQTT.")
    yield
    # Clean shutdown: cancel drain task and wait for it without propagating CancelledError
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass  # ignore any other error during shutdown
    from backend.services.cusum_detector import save_all as cusum_save_all
    cusum_save_all()


app = FastAPI(
    title=settings.app_name,
    description="The Copperbelt University CMMS — Computerised Maintenance Management System for industrial process instruments.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])


# Serve frontend (single-page app)
import os
frontend_path = os.path.join(os.path.dirname(__file__), "frontend", "dist")
_index_headers = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}

if os.path.isdir(frontend_path):
    assets_dir = os.path.join(frontend_path, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(frontend_path, "index.html"), headers=_index_headers)

    @app.get("/{path:path}")
    def catch_all(path: str):
        if not path.startswith("api"):
            return FileResponse(os.path.join(frontend_path, "index.html"), headers=_index_headers)
        from fastapi import HTTPException
        raise HTTPException(404)
