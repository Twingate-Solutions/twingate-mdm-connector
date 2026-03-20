"""Tests for the Notifier protocol, event dataclasses, and NullNotifier."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.notifications.base import (
    NullNotifier,
    ProviderErrorEvent,
    SyncCompleteEvent,
    TrustEvent,
)
from src.notifications.factory import CompositeNotifier, build_notifier
from src.config import AppConfig


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@pytest.fixture
def trust_event() -> TrustEvent:
    return TrustEvent(
        device_id="dev-123",
        device_name="CORP-LAPTOP-01",
        serial_number="ABC123456",
        os_name="Windows",
        user_email="user@example.com",
        providers=("ninjaone",),
        timestamp=_now(),
        dry_run=False,
    )


@pytest.fixture
def provider_error_event() -> ProviderErrorEvent:
    return ProviderErrorEvent(
        provider_name="sophos",
        error_message="Connection refused",
        timestamp=_now(),
    )


@pytest.fixture
def sync_complete_event() -> SyncCompleteEvent:
    return SyncCompleteEvent(
        total_untrusted=10,
        total_trusted=3,
        total_skipped=2,
        total_no_match=5,
        total_errors=0,
        provider_names=("ninjaone",),
        cycle_number=1,
        timestamp=_now(),
    )


class TestTrustEventMaskedSerial:
    def test_long_serial_shows_last_four(self, trust_event: TrustEvent) -> None:
        assert trust_event.masked_serial == "****3456"

    def test_short_serial_fully_masked(self) -> None:
        e = TrustEvent(
            device_id="x", device_name="x", serial_number="AB",
            os_name=None, user_email=None, providers=(), timestamp=_now(), dry_run=False,
        )
        assert e.masked_serial == "****"

    def test_exactly_four_chars_fully_masked(self) -> None:
        e = TrustEvent(
            device_id="x", device_name="x", serial_number="1234",
            os_name=None, user_email=None, providers=(), timestamp=_now(), dry_run=False,
        )
        assert e.masked_serial == "****"

    def test_five_chars_shows_last_four(self) -> None:
        e = TrustEvent(
            device_id="x", device_name="x", serial_number="A1234",
            os_name=None, user_email=None, providers=(), timestamp=_now(), dry_run=False,
        )
        assert e.masked_serial == "****1234"


class TestNullNotifier:
    @pytest.mark.asyncio
    async def test_on_device_trusted_is_noop(self, trust_event: TrustEvent) -> None:
        await NullNotifier().on_device_trusted(trust_event)  # must not raise

    @pytest.mark.asyncio
    async def test_on_provider_error_is_noop(
        self, provider_error_event: ProviderErrorEvent
    ) -> None:
        await NullNotifier().on_provider_error(provider_error_event)

    @pytest.mark.asyncio
    async def test_on_sync_complete_is_noop(
        self, sync_complete_event: SyncCompleteEvent
    ) -> None:
        await NullNotifier().on_sync_complete(sync_complete_event)

    def test_has_all_required_methods(self) -> None:
        n = NullNotifier()
        assert callable(getattr(n, "on_device_trusted", None))
        assert callable(getattr(n, "on_provider_error", None))
        assert callable(getattr(n, "on_sync_complete", None))

    def test_null_notifier_satisfies_notifier_protocol(self) -> None:
        from src.notifications.base import Notifier

        assert isinstance(NullNotifier(), Notifier)


class TestCompositeNotifier:
    @pytest.mark.asyncio
    async def test_dispatches_on_device_trusted_to_all_children(
        self, trust_event: TrustEvent
    ) -> None:
        child_a = AsyncMock()
        child_b = AsyncMock()
        composite = CompositeNotifier([child_a, child_b])
        await composite.on_device_trusted(trust_event)
        child_a.on_device_trusted.assert_awaited_once_with(trust_event)
        child_b.on_device_trusted.assert_awaited_once_with(trust_event)

    @pytest.mark.asyncio
    async def test_dispatches_on_provider_error_to_all_children(
        self, provider_error_event: ProviderErrorEvent
    ) -> None:
        child_a = AsyncMock()
        child_b = AsyncMock()
        composite = CompositeNotifier([child_a, child_b])
        await composite.on_provider_error(provider_error_event)
        child_a.on_provider_error.assert_awaited_once_with(provider_error_event)
        child_b.on_provider_error.assert_awaited_once_with(provider_error_event)

    @pytest.mark.asyncio
    async def test_dispatches_on_sync_complete_to_all_children(
        self, sync_complete_event: SyncCompleteEvent
    ) -> None:
        child_a = AsyncMock()
        child_b = AsyncMock()
        composite = CompositeNotifier([child_a, child_b])
        await composite.on_sync_complete(sync_complete_event)
        child_a.on_sync_complete.assert_awaited_once_with(sync_complete_event)
        child_b.on_sync_complete.assert_awaited_once_with(sync_complete_event)

    def test_composite_satisfies_notifier_protocol(self) -> None:
        from src.notifications.base import Notifier
        composite = CompositeNotifier([])
        assert isinstance(composite, Notifier)

    @pytest.mark.asyncio
    async def test_child_failure_does_not_block_sibling(
        self, trust_event: TrustEvent
    ) -> None:
        """A failing child must not prevent subsequent children from receiving the event."""
        from unittest.mock import AsyncMock
        failing = AsyncMock()
        failing.on_device_trusted.side_effect = RuntimeError("boom")
        healthy = AsyncMock()
        composite = CompositeNotifier([failing, healthy])
        await composite.on_device_trusted(trust_event)  # must not raise
        healthy.on_device_trusted.assert_awaited_once_with(trust_event)


class TestBuildNotifier:
    def _minimal_app_config(self, notifications=None) -> AppConfig:
        """Build a minimal valid AppConfig."""
        return AppConfig.model_validate({
            "twingate": {"tenant": "example", "api_key": "test-key"},
            "notifications": notifications,
        })

    def _smtp_block(self) -> dict:
        return {
            "host": "smtp.example.com",
            "username": "u",
            "password": "p",
            "from": "bridge@example.com",
            "to": ["admin@example.com"],
        }

    def test_returns_null_notifier_when_notifications_none(self) -> None:
        from src.notifications.base import NullNotifier
        cfg = self._minimal_app_config(notifications=None)
        result = build_notifier(cfg)
        assert isinstance(result, NullNotifier)

    def test_returns_null_notifier_when_no_channels_configured(self) -> None:
        from src.notifications.base import NullNotifier
        cfg = self._minimal_app_config(notifications={"smtp": None, "webhooks": []})
        result = build_notifier(cfg)
        assert isinstance(result, NullNotifier)

    def test_returns_composite_with_smtp_channel(self) -> None:
        cfg = self._minimal_app_config(notifications={"smtp": self._smtp_block()})
        result = build_notifier(cfg)
        assert isinstance(result, CompositeNotifier)

    def test_returns_composite_with_webhook_channel(self) -> None:
        cfg = self._minimal_app_config(notifications={"webhooks": [{"url": "https://hooks.example.com/"}]})
        result = build_notifier(cfg)
        assert isinstance(result, CompositeNotifier)

    def test_smtp_digest_enabled_creates_accumulator(self) -> None:
        smtp_block = self._smtp_block()
        smtp_block["digest"] = {"enabled": True, "schedule": "08:00", "timezone": "UTC"}
        cfg = self._minimal_app_config(notifications={"smtp": smtp_block})
        from src.notifications.digest import DigestAccumulator
        acc = DigestAccumulator()
        result = build_notifier(cfg, accumulator=acc)
        assert isinstance(result, CompositeNotifier)
