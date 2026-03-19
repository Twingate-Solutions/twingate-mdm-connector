"""SMTP email notification channel.

Email bodies are rendered from ``string.Template`` template files.  Bundled
defaults live in ``src/notifications/templates/``.  Override any template by
placing a file with the same name in the directory set by ``smtp.templates_dir``
in your config.

Failures are always non-fatal — logged and swallowed.
"""

from __future__ import annotations

import email.mime.multipart
import email.mime.text
import string
import zoneinfo
from datetime import datetime
from pathlib import Path

import aiosmtplib

from src.config import SmtpConfig
from src.notifications.base import ProviderErrorEvent, SyncCompleteEvent, TrustEvent
from src.utils.logging import get_logger

logger = get_logger(__name__)

_BUNDLED_TEMPLATES_DIR = Path(__file__).parent / "templates"


def load_template(
    filename: str,
    templates_dir: str | None,
    **variables: str,
) -> str:
    """Render a ``string.Template`` email template.

    Searches ``templates_dir`` first (if set), then falls back to the bundled
    templates in ``src/notifications/templates/``.

    Args:
        filename: Template filename (e.g. ``"alert_provider_error.txt"``).
        templates_dir: User-configured directory path, or ``None``.
        **variables: Template substitution variables.

    Returns:
        Rendered template string.
    """
    template_text: str | None = None

    if templates_dir is not None:
        custom_path = Path(templates_dir) / filename
        if custom_path.exists():
            template_text = custom_path.read_text(encoding="utf-8")

    if template_text is None:
        bundled_path = _BUNDLED_TEMPLATES_DIR / filename
        template_text = bundled_path.read_text(encoding="utf-8")

    return string.Template(template_text).safe_substitute(variables)


class SmtpNotifier:
    """Sends SMTP email alerts and daily digests.

    Alert emails are sent immediately via :meth:`on_provider_error` (and
    future alert events).  Per-device trust events do **not** trigger
    individual emails — they are accumulated by
    :class:`~src.notifications.digest.DigestAccumulator` and flushed daily
    via :meth:`send_digest`.
    """

    def __init__(self, config: SmtpConfig, display_timezone: str = "UTC") -> None:
        self._cfg = config
        try:
            self._tz = zoneinfo.ZoneInfo(display_timezone)
        except Exception:
            self._tz = zoneinfo.ZoneInfo("UTC")

    async def on_device_trusted(self, event: TrustEvent) -> None:
        """No-op — trust events appear in the daily digest, not individual emails."""

    async def on_provider_error(self, event: ProviderErrorEvent) -> None:
        """Send an alert email if ``provider_error`` is in ``alerts.events``."""
        if not self._cfg.alerts.enabled:
            return
        if "provider_error" not in self._cfg.alerts.events:
            return
        subject = (
            f"[twingate-mdm-connector] Error: provider '{event.provider_name}' failed"
        )
        body = load_template(
            "alert_provider_error.txt",
            self._cfg.templates_dir,
            provider_name=event.provider_name,
            error_message=event.error_message,
            timestamp=event.timestamp.astimezone(self._tz).isoformat(),
            timezone=str(self._tz),
        )
        await self._send(subject, body)

    async def on_sync_complete(self, event: SyncCompleteEvent) -> None:
        """No-op — stats are accumulated and sent in the daily digest."""

    async def send_digest(self, cycles: list[SyncCompleteEvent]) -> None:
        """Send a daily digest email summarising completed sync cycles.

        Args:
            cycles: All :class:`SyncCompleteEvent` objects since the last
                digest flush.  Sends nothing if the list is empty.
        """
        if not cycles:
            return

        total_trusted = sum(c.total_trusted for c in cycles)
        total_untrusted = sum(c.total_untrusted for c in cycles)
        total_skipped = sum(c.total_skipped for c in cycles)
        total_errors = sum(c.total_errors for c in cycles)
        provider_names = sorted({n for c in cycles for n in c.provider_names})

        subject = "[twingate-mdm-connector] Daily digest"
        body = load_template(
            "digest.txt",
            self._cfg.templates_dir,
            date=datetime.now(tz=self._tz).strftime("%Y-%m-%d"),
            num_cycles=str(len(cycles)),
            total_untrusted=str(total_untrusted),
            total_trusted=str(total_trusted),
            total_skipped=str(total_skipped),
            total_errors=str(total_errors),
            provider_names=", ".join(provider_names) if provider_names else "none",
        )
        await self._send(subject, body)

    async def _send(self, subject: str, body: str) -> None:
        """Build and transmit a plain-text MIME email. Failures are non-fatal."""
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = self._cfg.from_address
        msg["To"] = ", ".join(self._cfg.to)
        msg["Subject"] = subject
        msg.attach(email.mime.text.MIMEText(body, "plain"))

        try:
            await aiosmtplib.send(
                msg,
                hostname=self._cfg.host,
                port=self._cfg.port,
                username=self._cfg.username,
                password=self._cfg.password,
                use_tls=self._cfg.tls_mode == "tls",
                start_tls=self._cfg.tls_mode == "starttls",
            )
            logger.info("SMTP email sent", subject=subject, to=self._cfg.to)
        except Exception as exc:
            logger.error(
                "Failed to send SMTP email — continuing",
                subject=subject,
                error=str(exc),
            )
