"""
test-twingate-client.py — Smoke-test the TwingateClient against the real API.

Fetches all untrusted devices and pretty-prints them. Makes no mutations.

Usage:
    python scripts/test-twingate-client.py --tenant mycompany --api-key $KEY
    # or via env vars:
    TWINGATE_TENANT=mycompany TWINGATE_API_KEY=... python scripts/test-twingate-client.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# Allow running from the repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.twingate.client import TwingateClient
from src.utils.logging import configure_logging


async def run(tenant: str, api_key: str) -> None:
    configure_logging("DEBUG")

    print(f"\nConnecting to https://{tenant}.twingate.com/api/graphql/\n")

    async with TwingateClient(tenant=tenant, api_key=api_key) as client:
        devices = await client.list_untrusted_devices()

    print(f"\n--- {len(devices)} untrusted device(s) ---\n")
    for d in devices:
        print(json.dumps(d.model_dump(mode="json"), indent=2, default=str))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the TwingateClient.")
    parser.add_argument("--tenant", default=os.environ.get("TWINGATE_TENANT"))
    parser.add_argument("--api-key", default=os.environ.get("TWINGATE_API_KEY"))
    args = parser.parse_args()

    if not args.tenant:
        parser.error("--tenant is required (or set TWINGATE_TENANT)")
    if not args.api_key:
        parser.error("--api-key is required (or set TWINGATE_API_KEY)")

    asyncio.run(run(args.tenant, args.api_key))


if __name__ == "__main__":
    main()
