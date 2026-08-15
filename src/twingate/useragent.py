"""User-Agent construction for outbound Twingate API calls.

The connector stamps a descriptive ``User-Agent`` on every Twingate request so
that usage can be analysed downstream (e.g. in ELK) — primarily to see which
MDM/EDR providers are configured across deployments.

Format::

    twingate-mdm-connector/<version> (<key=value; ...>) python-httpx/<version>

The parenthesised comment is a ``; ``-separated list of stable ``key=value``
pairs, ordered deterministically so log parsers can rely on it:

    providers   comma-separated sorted provider slugs (or "none")
    mode        trust evaluation mode ("any" | "all")
    dry_run     "true" | "false"
    op          the operation issuing the call ("list" | "trust")
    via         (trust only) the provider(s) that voted to trust this device
"""

import httpx

from src import __version__

_PRODUCT = "twingate-mdm-connector"


def build_user_agent(
    *,
    providers: list[str],
    mode: str,
    dry_run: bool,
    op: str,
    via: list[str] | None = None,
) -> str:
    """Build a ``User-Agent`` header value for a Twingate API call.

    Args:
        providers: Enabled provider slugs (e.g. ``["fleetdm", "jumpcloud"]``).
            Sorted and de-duplicated internally for a stable token.
        mode: Trust evaluation mode — ``"any"`` or ``"all"``.
        dry_run: Whether the connector is running in dry-run mode.
        op: Operation issuing the call — ``"list"`` or ``"trust"``.
        via: For trust mutations, the provider slug(s) that contributed the
            positive trust decision for this device. Omitted for other ops.

    Returns:
        A single-line ``User-Agent`` string.
    """
    provider_token = ",".join(sorted(set(providers))) if providers else "none"
    parts = [
        f"providers={provider_token}",
        f"mode={mode}",
        f"dry_run={str(dry_run).lower()}",
        f"op={op}",
    ]
    if via:
        parts.append(f"via={','.join(sorted(set(via)))}")

    comment = "; ".join(parts)
    return f"{_PRODUCT}/{__version__} ({comment}) python-httpx/{httpx.__version__}"
