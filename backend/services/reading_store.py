"""Store and aggregate raw readings; buffer for time-window feature extraction."""
from datetime import datetime, timedelta
from collections import defaultdict
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import RawReading
from config import settings


# In-memory buffer: tag -> list of (timestamp, value) for current window aggregation
_reading_buffer: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
_buffer_lock = asyncio.Lock()


async def append_reading(session: AsyncSession, tag_number: str, timestamp: datetime, value: float, unit: str | None = None) -> None:
    """Persist one raw reading and add to in-memory buffer for aggregation."""
    r = RawReading(tag_number=tag_number, timestamp=timestamp, value=value, unit=unit)
    session.add(r)
    async with _buffer_lock:
        _reading_buffer[tag_number].append((timestamp, value))
    # Prune buffer to last 2 windows to avoid unbounded growth
    window = timedelta(seconds=settings.aggregation_window_seconds)
    cutoff = datetime.utcnow() - 2 * window
    async with _buffer_lock:
        _reading_buffer[tag_number] = [(t, v) for t, v in _reading_buffer[tag_number] if t >= cutoff]


def get_buffered_readings_sync(tag_number: str, since: datetime) -> list[tuple[datetime, float]]:
    """Sync access to buffer (e.g. from sync ML code). Not thread-safe with async writers."""
    return [(t, v) for t, v in _reading_buffer.get(tag_number, []) if t >= since]


async def clear_buffer_for_tag(tag_number: str) -> None:
    """Drop in-memory readings buffer for a tag (instrument removed)."""
    async with _buffer_lock:
        _reading_buffer.pop(tag_number, None)


async def get_buffered_readings(tag_number: str, since: datetime) -> list[tuple[datetime, float]]:
    """Return buffered readings for a tag since given time (thread-safe copy)."""
    async with _buffer_lock:
        return [(t, v) for t, v in _reading_buffer.get(tag_number, []) if t >= since]


async def get_readings_for_period(
    session: AsyncSession, tag_number: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    """Load raw readings from DB for a tag in [start, end]."""
    result = await session.execute(
        select(RawReading.timestamp, RawReading.value).where(
            RawReading.tag_number == tag_number,
            RawReading.timestamp >= start,
            RawReading.timestamp <= end,
        ).order_by(RawReading.timestamp)
    )
    return [(row[0], row[1]) for row in result.all()]
