"""Digest accumulator and daily scheduler.

:class:`DigestAccumulator` collects :class:`SyncCompleteEvent` objects across
sync cycles.  :func:`run_digest_scheduler` is a long-running ``asyncio.Task``
that sleeps until the daily configured time, flushes the accumulator, and
calls :meth:`~src.notifications.smtp.SmtpNotifier.send_digest`.

The accumulator is intentionally in-memory only — stats reset on container
restart.  This is documented in the configuration reference.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from src.notifications.base import SyncCompleteEvent
from src.utils.logging import get_logger

logger = get_logger(__name__)


class DigestAccumulator:
    """In-memory accumulator for sync cycle stats.

    Single-process asyncio — no locks required.
    """

    def __init__(self) -> None:
        self._events: list[SyncCompleteEvent] = []

    @property
    def pending_count(self) -> int:
        """Number of events accumulated since the last flush."""
        return len(self._events)

    def add(self, event: SyncCompleteEvent) -> None:
        """Append a sync cycle event."""
        self._events.append(event)

    def flush(self) -> list[SyncCompleteEvent]:
        """Return all accumulated events and reset."""
        events, self._events = self._events, []
        return events


def seconds_until_next_send(schedule_hhmm: str, tz_name: str) -> float:
    """Compute seconds until the next scheduled send time.

    Args:
        schedule_hhmm: Target time as ``"HH:MM"`` (e.g. ``"08:00"``).
        tz_name: IANA timezone name.  Falls back to UTC if invalid.

    Returns:
        Seconds (float) until next occurrence.  Always > 0 and <= 86 400.
    """
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        logger.warning("Invalid digest timezone — falling back to UTC", tz=tz_name)
        import zoneinfo
        tz = zoneinfo.ZoneInfo("UTC")

    hour, minute = (int(p) for p in schedule_hhmm.split(":"))
    now = datetime.now(tz=tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_digest_scheduler(
    accumulator: DigestAccumulator,
    smtp_notifier: object,   # SmtpNotifier — untyped to avoid circular import
    schedule_hhmm: str,
    tz_name: str,
) -> None:
    """Long-running asyncio task that sends the daily digest email.

    Args:
        accumulator: Shared :class:`DigestAccumulator`.
        smtp_notifier: A :class:`~src.notifications.smtp.SmtpNotifier` instance.
        schedule_hhmm: Time string ``"HH:MM"``.
        tz_name: Timezone name string.
    """
    logger.info("Digest scheduler started", schedule=schedule_hhmm, timezone=tz_name)
    try:
        while True:
            wait = seconds_until_next_send(schedule_hhmm, tz_name)
            logger.info("Next digest in", wait_seconds=round(wait))
            await asyncio.sleep(wait)
            events = accumulator.flush()
            logger.info("Sending daily digest", cycle_count=len(events))
            await smtp_notifier.send_digest(events)
    except asyncio.CancelledError:
        logger.info("Digest scheduler cancelled")
        raise
