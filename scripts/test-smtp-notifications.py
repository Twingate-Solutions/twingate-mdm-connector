"""
test-smtp-notifications.py — Send test emails for every configured SMTP alert type.

Loads config.yaml (or the file set by CONFIG_FILE), constructs a real SmtpNotifier,
and fires one test email per alert event type that is enabled. Also sends a test
digest if smtp.digest.enabled is true.

Usage:
    python scripts/test-smtp-notifications.py
    CONFIG_FILE=my-config.yaml python scripts/test-smtp-notifications.py

No events need to be happening — this bypasses the sync engine entirely.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

# Allow running from the repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.notifications.base import ProviderErrorEvent, SyncCompleteEvent, TrustEvent
from src.notifications.smtp import SmtpNotifier


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def run() -> None:
    config_path = os.environ.get("CONFIG_FILE", "config.yaml")
    print(f"Loading config from: {config_path}")

    config = load_config(config_path)

    if config.notifications is None or config.notifications.smtp is None:
        print("\nNo SMTP configuration found in config.yaml.")
        print("Add a 'notifications.smtp' block to test email notifications.")
        sys.exit(1)

    smtp_cfg = config.notifications.smtp
    notifier = SmtpNotifier(smtp_cfg, display_timezone=config.logging.timezone)

    print(f"\nSMTP host:    {smtp_cfg.host}:{smtp_cfg.port}")
    print(f"From:         {smtp_cfg.from_address}")
    print(f"To:           {', '.join(smtp_cfg.to)}")
    print(f"TLS mode:     {smtp_cfg.tls_mode}")
    print(f"Alert events: {smtp_cfg.alerts.events}")
    print(f"Digest:       {'enabled' if smtp_cfg.digest.enabled else 'disabled'}")
    print()

    sent = 0
    skipped = 0

    # --- provider_error alert ---
    if "provider_error" in smtp_cfg.alerts.events and smtp_cfg.alerts.enabled:
        print("Sending provider_error alert...")
        await notifier.on_provider_error(ProviderErrorEvent(
            provider_name="test-provider",
            error_message="This is a test alert — no real error occurred.",
            timestamp=_now(),
        ))
        print("  Done.")
        sent += 1
    else:
        print("Skipping provider_error alert (not in alerts.events or alerts disabled).")
        skipped += 1

    # --- mutation_error alert (sent directly via _send, bypassing event filter) ---
    if "mutation_error" in smtp_cfg.alerts.events and smtp_cfg.alerts.enabled:
        print("Sending mutation_error alert...")
        from src.notifications.smtp import load_template
        subject = "[twingate-mdm-connector] Error: failed to trust device (TEST)"
        body = load_template(
            "alert_mutation_error.txt",
            smtp_cfg.templates_dir,
            device_id="DEV-TEST-001",
            device_name="TEST-LAPTOP",
            serial_masked="****1234",
            error_message="This is a test alert — no real error occurred.",
            timestamp=_now().astimezone(notifier._tz).isoformat(),
            timezone=str(notifier._tz),
        )
        await notifier._send(subject, body)
        print("  Done.")
        sent += 1
    else:
        print("Skipping mutation_error alert (not in alerts.events or alerts disabled).")
        skipped += 1

    # --- startup_failure alert (sent directly) ---
    if "startup_failure" in smtp_cfg.alerts.events and smtp_cfg.alerts.enabled:
        print("Sending startup_failure alert...")
        from src.notifications.smtp import load_template
        subject = "[twingate-mdm-connector] Startup failure (TEST)"
        body = load_template(
            "alert_startup_failure.txt",
            smtp_cfg.templates_dir,
            error_message="This is a test alert — no real error occurred.",
            timestamp=_now().astimezone(notifier._tz).isoformat(),
            timezone=str(notifier._tz),
        )
        await notifier._send(subject, body)
        print("  Done.")
        sent += 1
    else:
        print("Skipping startup_failure alert (not in alerts.events or alerts disabled).")
        skipped += 1

    # --- digest ---
    if smtp_cfg.digest.enabled:
        print("Sending digest email...")
        await notifier.send_digest([
            SyncCompleteEvent(
                total_untrusted=42,
                total_trusted=7,
                total_skipped=3,
                total_no_match=32,
                total_errors=0,
                provider_names=("ninjaone", "sophos"),
                cycle_number=0,
                timestamp=_now(),
            ),
            SyncCompleteEvent(
                total_untrusted=38,
                total_trusted=5,
                total_skipped=2,
                total_no_match=31,
                total_errors=1,
                provider_names=("ninjaone", "sophos"),
                cycle_number=0,
                timestamp=_now(),
            ),
        ])
        print("  Done.")
        sent += 1
    else:
        print("Skipping digest (smtp.digest.enabled is false).")
        skipped += 1

    print(f"\nDone. {sent} email(s) sent, {skipped} skipped.")
    if sent > 0:
        print(f"Check your inbox at: {', '.join(smtp_cfg.to)}")


if __name__ == "__main__":
    asyncio.run(run())
