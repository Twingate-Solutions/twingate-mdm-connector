"""HTTP webhook notification channel.

Sends JSON POST requests on device trust, provider error, and sync complete
events.  Optionally signs payloads with HMAC-SHA256.  Failures are non-fatal.

The ``format`` config field selects the payload shape:

- ``raw`` (default) — the original structured JSON built in code.
- Any other value (e.g. ``slack``, ``teams``, ``discord``, ``pagerduty``,
  ``opsgenie``) — loads a bundled ``{format}_{event_type}.json`` template
  and renders it with ``string.Template`` substitution.

Custom templates can override bundled ones via ``templates_dir``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import string
from pathlib import Path

from src.config import WebhookConfig
from src.notifications.base import ProviderErrorEvent, SyncCompleteEvent, TrustEvent
from src.utils.http import build_client, request_with_retry
from src.utils.logging import get_logger

logger = get_logger(__name__)

_WEBHOOK_MAX_RETRIES = 2   # 3 total attempts
_BUNDLED_WEBHOOK_TEMPLATES_DIR = Path(__file__).parent / "webhook_templates"


def load_webhook_template(
    format: str, event_type: str, templates_dir: str | None
) -> str:
    """Load a JSON webhook template for the given format and event type.

    Searches *templates_dir* first (if set), then the bundled templates
    shipped with the package.

    Filename pattern: ``{format}_{event_type}.json``

    Args:
        format: Template format name (e.g. ``slack``, ``teams``).
        event_type: Event name (e.g. ``device_trusted``).
        templates_dir: Optional user-provided template directory that
            takes priority over the bundled templates.

    Returns:
        The raw template text (containing ``$variable`` placeholders).

    Raises:
        FileNotFoundError: If no matching template file exists in either
            the custom or bundled directories.
    """
    filename = f"{format}_{event_type}.json"

    if templates_dir is not None:
        custom_path = Path(templates_dir) / filename
        if custom_path.exists():
            return custom_path.read_text(encoding="utf-8")

    bundled_path = _BUNDLED_WEBHOOK_TEMPLATES_DIR / filename
    if not bundled_path.exists():
        raise FileNotFoundError(
            f"No webhook template found for format '{format}', event '{event_type}'. "
            f"Expected file: {bundled_path}"
        )
    return bundled_path.read_text(encoding="utf-8")


class WebhookNotifier:
    """Delivers webhook payloads via signed HTTP POST.

    Each event fires only if its name appears in ``config.events``.  This
    list-based filter means future event types (e.g. ``device_untrusted``,
    ``local_trust_applied``) are automatically supported — admins just add
    the event name to their ``config.yaml`` without any code changes.
    """

    def __init__(self, config: WebhookConfig) -> None:
        self._cfg = config

    async def on_device_trusted(self, event: TrustEvent) -> None:
        """Fire ``device_trusted`` webhook if configured."""
        if "device_trusted" not in self._cfg.events:
            return

        raw_payload = {
            "event": "device_trusted",
            "timestamp": event.timestamp.isoformat(),
            "device": {
                "hostname": event.device_name,
                "serial_masked": event.masked_serial,
                "os": event.os_name,
                "user_email": event.user_email,
            },
            "result": {
                "trusted": True,
                "providers_matched": list(event.providers),
                "dry_run": event.dry_run,
            },
        }

        template_variables = {
            "event_type": "device_trusted",
            "timestamp": event.timestamp.isoformat(),
            "device_hostname": event.device_name or "",
            "device_serial_masked": event.masked_serial,
            "device_os": event.os_name or "",
            "device_user_email": event.user_email or "",
            "providers_matched": ", ".join(event.providers),
            "dry_run": str(event.dry_run).lower(),
        }

        body = self._build_body("device_trusted", raw_payload, template_variables)
        await self._post("device_trusted", body)

    async def on_provider_error(self, event: ProviderErrorEvent) -> None:
        """Fire ``provider_error`` webhook if configured."""
        if "provider_error" not in self._cfg.events:
            return

        raw_payload = {
            "event": "provider_error",
            "timestamp": event.timestamp.isoformat(),
            "provider": event.provider_name,
            "error": event.error_message,
        }

        template_variables = {
            "event_type": "provider_error",
            "timestamp": event.timestamp.isoformat(),
            "provider_name": event.provider_name,
            "error_message": event.error_message,
        }

        body = self._build_body("provider_error", raw_payload, template_variables)
        await self._post("provider_error", body)

    async def on_sync_complete(self, event: SyncCompleteEvent) -> None:
        """Fire ``sync_complete`` webhook if configured."""
        if "sync_complete" not in self._cfg.events:
            return

        raw_payload = {
            "event": "sync_complete",
            "timestamp": event.timestamp.isoformat(),
            "cycle": event.cycle_number,
            "stats": {
                "total_untrusted": event.total_untrusted,
                "total_trusted": event.total_trusted,
                "total_skipped": event.total_skipped,
                "total_no_match": event.total_no_match,
                "total_errors": event.total_errors,
            },
            "providers": list(event.provider_names),
        }

        template_variables = {
            "event_type": "sync_complete",
            "timestamp": event.timestamp.isoformat(),
            "total_untrusted": str(event.total_untrusted),
            "total_trusted": str(event.total_trusted),
            "total_skipped": str(event.total_skipped),
            "total_no_match": str(event.total_no_match),
            "total_errors": str(event.total_errors),
            "provider_names": ", ".join(event.provider_names),
            "cycle_number": str(event.cycle_number),
            "num_cycles": "1",
        }

        body = self._build_body("sync_complete", raw_payload, template_variables)
        await self._post("sync_complete", body)

    def _build_body(
        self,
        event_type: str,
        raw_payload: dict,
        template_variables: dict[str, str],
    ) -> bytes:
        """Build the serialised POST body.

        For ``raw`` format the existing dict is JSON-encoded directly.
        For any other format a ``{format}_{event_type}.json`` template is
        loaded and rendered with ``string.Template.safe_substitute``.
        """
        if self._cfg.format == "raw":
            return json.dumps(raw_payload, default=str).encode()

        template_text = load_webhook_template(
            self._cfg.format, event_type, self._cfg.templates_dir
        )
        return string.Template(template_text).safe_substitute(template_variables).encode()

    async def _post(self, event_type: str, body: bytes) -> None:
        """Sign and POST the payload.  Failures are non-fatal."""
        headers: dict[str, str] = {"Content-Type": "application/json"}

        if self._cfg.headers:
            headers.update(self._cfg.headers)

        if self._cfg.secret:
            sig = "sha256=" + hmac.new(
                self._cfg.secret.encode(), body, hashlib.sha256
            ).hexdigest()
            headers["X-Hub-Signature-256"] = sig

        try:
            async with build_client(
                read_timeout=float(self._cfg.timeout_seconds),
            ) as client:
                await request_with_retry(
                    client, "POST", self._cfg.url,
                    headers=headers,
                    content=body,
                    max_retries=_WEBHOOK_MAX_RETRIES,
                )
            logger.info(
                "Webhook delivered",
                event_type=event_type,
                url=self._cfg.url,
                format=self._cfg.format,
            )
        except Exception as exc:
            logger.error(
                "Webhook delivery failed — continuing",
                event_type=event_type,
                url=self._cfg.url,
                error=str(exc),
            )
