"""Tests for SmtpNotifier — SMTP transport mocked throughout."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.config import SmtpAlertsConfig, SmtpConfig, SmtpDigestConfig
from src.notifications.base import ProviderErrorEvent, SyncCompleteEvent, TrustEvent
from src.notifications.smtp import SmtpNotifier, load_template


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _smtp_cfg(**overrides) -> SmtpConfig:
    defaults = dict(
        host="smtp.example.com", port=587,
        username="user", password="pass",
        **{"from": "bridge@example.com"},
        to=["admin@example.com"],
        tls_mode="starttls",
        alerts=SmtpAlertsConfig(enabled=True, events=["provider_error", "mutation_error"]),
        digest=SmtpDigestConfig(enabled=True, schedule="08:00", timezone="UTC"),
    )
    defaults.update(overrides)
    return SmtpConfig.model_validate(defaults)


@pytest.fixture
def trust_event() -> TrustEvent:
    return TrustEvent(
        device_id="dev-1", device_name="CORP-01", serial_number="ABCD1234",
        os_name="Windows", user_email="user@example.com",
        providers=("ninjaone",), timestamp=_now(), dry_run=False,
    )


@pytest.fixture
def provider_error_event() -> ProviderErrorEvent:
    return ProviderErrorEvent(
        provider_name="sophos", error_message="Connection refused", timestamp=_now()
    )


@pytest.fixture
def sync_event() -> SyncCompleteEvent:
    return SyncCompleteEvent(
        total_untrusted=10, total_trusted=3, total_skipped=2,
        total_no_match=5, total_errors=0,
        provider_names=("ninjaone",), cycle_number=1, timestamp=_now(),
    )


class TestLoadTemplate:
    def test_loads_bundled_template(self) -> None:
        body = load_template("alert_provider_error.txt", templates_dir=None,
                             provider_name="sophos", error_message="err",
                             timestamp="2026-01-01T00:00:00Z")
        assert "sophos" in body
        assert "err" in body

    def test_custom_templates_dir_overrides_bundled(self, tmp_path: Path) -> None:
        custom = tmp_path / "alert_provider_error.txt"
        custom.write_text("Custom: $provider_name failed at $timestamp with $error_message")
        body = load_template("alert_provider_error.txt", templates_dir=str(tmp_path),
                             provider_name="ninjaone", error_message="timeout",
                             timestamp="2026-01-01T00:00:00Z")
        assert body == "Custom: ninjaone failed at 2026-01-01T00:00:00Z with timeout"

    def test_missing_custom_file_falls_back_to_bundled(self, tmp_path: Path) -> None:
        """If user's templates_dir doesn't have the file, use the bundled default."""
        body = load_template("alert_provider_error.txt", templates_dir=str(tmp_path),
                             provider_name="x", error_message="y",
                             timestamp="z")
        assert "x" in body


class TestSmtpAlerts:
    @pytest.mark.asyncio
    async def test_provider_error_sends_email(
        self, provider_error_event: ProviderErrorEvent
    ) -> None:
        notifier = SmtpNotifier(_smtp_cfg())
        with patch("src.notifications.smtp.aiosmtplib.send", new_callable=AsyncMock) as mock:
            await notifier.on_provider_error(provider_error_event)
        mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provider_error_suppressed_when_not_in_events(
        self, provider_error_event: ProviderErrorEvent
    ) -> None:
        cfg = _smtp_cfg(alerts=SmtpAlertsConfig(enabled=True, events=["startup_failure"]))
        notifier = SmtpNotifier(cfg)
        with patch("src.notifications.smtp.aiosmtplib.send", new_callable=AsyncMock) as mock:
            await notifier.on_provider_error(provider_error_event)
        mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_alerts_disabled_suppresses_all(
        self, provider_error_event: ProviderErrorEvent
    ) -> None:
        cfg = _smtp_cfg(alerts=SmtpAlertsConfig(enabled=False, events=["provider_error"]))
        notifier = SmtpNotifier(cfg)
        with patch("src.notifications.smtp.aiosmtplib.send", new_callable=AsyncMock) as mock:
            await notifier.on_provider_error(provider_error_event)
        mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_device_trusted_is_noop(self, trust_event: TrustEvent) -> None:
        """SMTP does not send per-device emails (that's the digest's job)."""
        notifier = SmtpNotifier(_smtp_cfg())
        with patch("src.notifications.smtp.aiosmtplib.send", new_callable=AsyncMock) as mock:
            await notifier.on_device_trusted(trust_event)
        mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_sync_complete_is_noop(self, sync_event: SyncCompleteEvent) -> None:
        """Sync stats are accumulated by DigestAccumulator, not sent directly."""
        notifier = SmtpNotifier(_smtp_cfg())
        with patch("src.notifications.smtp.aiosmtplib.send", new_callable=AsyncMock) as mock:
            await notifier.on_sync_complete(sync_event)
        mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_smtp_failure_is_non_fatal(
        self, provider_error_event: ProviderErrorEvent
    ) -> None:
        notifier = SmtpNotifier(_smtp_cfg())
        with patch("src.notifications.smtp.aiosmtplib.send",
                   new_callable=AsyncMock, side_effect=Exception("SMTP down")):
            await notifier.on_provider_error(provider_error_event)  # must not raise

    @pytest.mark.asyncio
    async def test_email_subject_contains_provider_name(
        self, provider_error_event: ProviderErrorEvent
    ) -> None:
        notifier = SmtpNotifier(_smtp_cfg())
        captured = []
        async def capture(msg, **kwargs):
            captured.append(msg)
        with patch("src.notifications.smtp.aiosmtplib.send", side_effect=capture):
            await notifier.on_provider_error(provider_error_event)
        assert "sophos" in captured[0]["Subject"]


class TestSmtpDigest:
    @pytest.mark.asyncio
    async def test_send_digest_sends_email(self, sync_event: SyncCompleteEvent) -> None:
        notifier = SmtpNotifier(_smtp_cfg())
        with patch("src.notifications.smtp.aiosmtplib.send", new_callable=AsyncMock) as mock:
            await notifier.send_digest([sync_event])
        mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_digest_empty_list_sends_nothing(self) -> None:
        notifier = SmtpNotifier(_smtp_cfg())
        with patch("src.notifications.smtp.aiosmtplib.send", new_callable=AsyncMock) as mock:
            await notifier.send_digest([])
        mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_digest_body_contains_stats(
        self, sync_event: SyncCompleteEvent
    ) -> None:
        notifier = SmtpNotifier(_smtp_cfg())
        captured = []
        async def capture(msg, **kwargs):
            captured.append(msg)
        with patch("src.notifications.smtp.aiosmtplib.send", side_effect=capture):
            await notifier.send_digest([sync_event])
        assert "3" in captured[0].as_string()   # total_trusted
