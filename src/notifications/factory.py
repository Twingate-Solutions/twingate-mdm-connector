"""Composite notifier and factory function.

:class:`CompositeNotifier` dispatches all notification events to a list of
child notifiers.  :func:`build_notifier` constructs the correct notifier
(or a no-op :class:`~src.notifications.base.NullNotifier`) from the
application config.
"""

from __future__ import annotations

from src.config import AppConfig
from src.notifications.base import (
    Notifier,
    NullNotifier,
    ProviderErrorEvent,
    SyncCompleteEvent,
    TrustEvent,
)
from src.notifications.digest import DigestAccumulator
from src.utils.logging import get_logger

logger = get_logger(__name__)


class CompositeNotifier:
    """Dispatches notification events to all registered child notifiers."""

    def __init__(self, children: list[Notifier]) -> None:
        self._children = children

    async def on_device_trusted(self, event: TrustEvent) -> None:
        """Dispatch to all children."""
        for child in self._children:
            try:
                await child.on_device_trusted(event)
            except Exception:
                logger.error(
                    "Child notifier raised — continuing",
                    method="on_device_trusted",
                    exc_info=True,
                )

    async def on_provider_error(self, event: ProviderErrorEvent) -> None:
        """Dispatch to all children."""
        for child in self._children:
            try:
                await child.on_provider_error(event)
            except Exception:
                logger.error(
                    "Child notifier raised — continuing",
                    method="on_provider_error",
                    exc_info=True,
                )

    async def on_sync_complete(self, event: SyncCompleteEvent) -> None:
        """Dispatch to all children."""
        for child in self._children:
            try:
                await child.on_sync_complete(event)
            except Exception:
                logger.error(
                    "Child notifier raised — continuing",
                    method="on_sync_complete",
                    exc_info=True,
                )


class _AccumulatorAdapter:
    """Bridges on_sync_complete events to a DigestAccumulator.

    This is an internal child notifier that silently accumulates
    SyncCompleteEvent objects so they can be flushed by the digest scheduler.
    """

    def __init__(self, accumulator: DigestAccumulator) -> None:
        self._accumulator = accumulator

    async def on_device_trusted(self, event: TrustEvent) -> None:
        """No-op."""

    async def on_provider_error(self, event: ProviderErrorEvent) -> None:
        """No-op."""

    async def on_sync_complete(self, event: SyncCompleteEvent) -> None:
        """Add sync event to the digest accumulator."""
        self._accumulator.add(event)


def build_notifier(
    config: AppConfig,
    accumulator: DigestAccumulator | None = None,
) -> Notifier:
    """Build the appropriate notifier from application config.

    Returns a :class:`~src.notifications.base.NullNotifier` when:
    - The ``notifications`` config block is absent
    - Both channels (smtp and webhooks) are empty/absent

    Otherwise returns a :class:`CompositeNotifier` with each enabled channel
    as a child notifier.

    Args:
        config: The root application config.
        accumulator: Optional :class:`DigestAccumulator` shared with the
            digest scheduler.  Required when ``smtp.digest.enabled`` is
            ``True``.

    Returns:
        A :class:`~src.notifications.base.Notifier` implementation
        (:class:`~src.notifications.base.NullNotifier` or
        :class:`CompositeNotifier`).
    """
    notif = config.notifications
    if notif is None:
        return NullNotifier()

    children: list[Notifier] = []

    if notif.smtp is not None:
        from src.notifications.smtp import SmtpNotifier
        children.append(SmtpNotifier(notif.smtp, display_timezone=config.logging.timezone))
        if notif.smtp.digest.enabled and accumulator is not None:
            children.append(_AccumulatorAdapter(accumulator))
            logger.info("Digest accumulator wired into notifier")

    for wh_cfg in notif.webhooks:
        from src.notifications.webhook import WebhookNotifier
        children.append(WebhookNotifier(wh_cfg))

    if not children:
        return NullNotifier()

    logger.info("Composite notifier built", channel_count=len(children))
    return CompositeNotifier(children)
