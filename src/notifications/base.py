"""Notifier protocol, event dataclasses, and NullNotifier.

## Extensibility guide

To add a new notification event type (e.g., for device untrust or local trust):

1. Add a frozen ``@dataclass`` here with all event fields.
2. Add a method to the ``Notifier`` Protocol (e.g., ``on_device_untrusted``).
3. Add the same method as a no-op to ``NullNotifier``.
4. Add the method to ``CompositeNotifier`` in ``factory.py``.
5. Add a template file to ``src/notifications/templates/``.
6. Add handling in ``SmtpNotifier`` and ``WebhookNotifier``.
7. The config ``events`` lists in both channels automatically support the new
   event name string — admins opt-in by adding it to their ``config.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Event dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustEvent:
    """Emitted when a device is trusted (or would be in dry-run)."""

    device_id: str
    device_name: str | None
    serial_number: str
    os_name: str | None
    user_email: str | None
    providers: tuple[str, ...]   # provider names that approved the device
    timestamp: datetime
    dry_run: bool

    @property
    def masked_serial(self) -> str:
        """Return the serial number with all but the last 4 chars replaced by ****."""
        if len(self.serial_number) <= 4:
            return "****"
        return "****" + self.serial_number[-4:]


@dataclass(frozen=True)
class ProviderErrorEvent:
    """Emitted when a provider fails during a sync cycle."""

    provider_name: str
    error_message: str
    timestamp: datetime


@dataclass(frozen=True)
class SyncCompleteEvent:
    """Emitted at the end of every sync cycle with aggregate stats."""

    total_untrusted: int
    total_trusted: int
    total_skipped: int
    total_no_match: int
    total_errors: int
    provider_names: tuple[str, ...]
    cycle_number: int
    timestamp: datetime


# ---------------------------------------------------------------------------
# Notifier Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Notifier(Protocol):
    """Protocol satisfied by all notification channel implementations.

    New event types are added here as new methods.  Both ``NullNotifier``
    and ``CompositeNotifier`` must be updated in parallel.
    """

    async def on_device_trusted(self, event: TrustEvent) -> None:
        """Called when a device is trusted (or would be in dry-run)."""
        ...

    async def on_provider_error(self, event: ProviderErrorEvent) -> None:
        """Called when a provider fails during a sync cycle."""
        ...

    async def on_sync_complete(self, event: SyncCompleteEvent) -> None:
        """Called at the end of each sync cycle with aggregate stats."""
        ...


# ---------------------------------------------------------------------------
# Null implementation
# ---------------------------------------------------------------------------


class NullNotifier:
    """No-op notifier — used when the ``notifications`` config block is absent."""

    async def on_device_trusted(self, event: TrustEvent) -> None:
        """No-op."""

    async def on_provider_error(self, event: ProviderErrorEvent) -> None:
        """No-op."""

    async def on_sync_complete(self, event: SyncCompleteEvent) -> None:
        """No-op."""
