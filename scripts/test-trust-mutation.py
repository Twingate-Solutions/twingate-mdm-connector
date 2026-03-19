"""
test-trust-mutation.py — Smoke-test the TwingateClient.trust_device() mutation.

Looks up a device by serial number and calls deviceUpdate(isTrusted: true).
Makes a real mutation — use only in test environments.

Usage:
    python scripts/test-trust-mutation.py --tenant mycompany --api-key $KEY --serial ABC123
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.twingate.client import TwingateClient
from src.utils.logging import configure_logging


async def run(tenant: str, api_key: str, serial: str) -> None:
    configure_logging("INFO")
    target = serial.strip().upper()

    async with TwingateClient(tenant=tenant, api_key=api_key) as client:
        devices = await client.list_untrusted_devices()

    match = next(
        (d for d in devices if (d.serial_number or "").upper() == target),
        None,
    )

    if match is None:
        print(f"FAIL: No untrusted active device found with serial '{target}'.")
        print(f"  Scanned {len(devices)} untrusted active device(s).")
        sys.exit(1)

    print(f"Found: {match.name!r} (id={match.id}, serial={match.serial_number})")
    print("Calling trust_device mutation...")

    async with TwingateClient(tenant=tenant, api_key=api_key) as client:
        result = await client.trust_device(match.id)

    if result.ok:
        name = result.entity.name if result.entity else match.name
        trusted = result.entity.is_trusted if result.entity else "?"
        print(f"PASS: '{name}' is now isTrusted={trusted}")
    else:
        print(f"FAIL: mutation returned ok=false — {result.error}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the trust_device mutation.")
    parser.add_argument("--tenant", default=os.environ.get("TWINGATE_TENANT"))
    parser.add_argument("--api-key", default=os.environ.get("TWINGATE_API_KEY"))
    parser.add_argument("--serial", required=True, help="Serial number of the device to trust.")
    args = parser.parse_args()

    if not args.tenant:
        parser.error("--tenant is required (or set TWINGATE_TENANT)")
    if not args.api_key:
        parser.error("--api-key is required (or set TWINGATE_API_KEY)")

    asyncio.run(run(args.tenant, args.api_key, args.serial))


if __name__ == "__main__":
    main()
