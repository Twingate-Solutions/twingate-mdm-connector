"""Twingate GraphQL API client.

Provides two operations:
- :meth:`TwingateClient.list_untrusted_devices` — exhaustively paginates through
  all devices where ``isTrusted == false`` and ``activeState == ACTIVE``.
- :meth:`TwingateClient.trust_device` — calls the ``deviceUpdate`` mutation to
  mark a device as trusted.

Auth: ``X-API-KEY`` header with an admin-generated API token.
"""

from typing import Any

import httpx

from src.twingate.models import (
    TrustMutationResult,
    TwingateDevice,
    TwingateDeviceConnection,
)
from src.utils.http import build_client, request_with_retry
from src.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Pagination safety limit
# ---------------------------------------------------------------------------

_MAX_PAGES = 500

# ---------------------------------------------------------------------------
# GraphQL query and mutation strings
# ---------------------------------------------------------------------------

_QUERY_UNTRUSTED_DEVICES = """
query GetUntrustedDevices($after: String, $first: Int) {
  devices(
    filter: { isTrusted: { eq: false }, activeState: { in: [ACTIVE] } }
    after: $after
    first: $first
  ) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        name
        serialNumber
        osName
        osVersion
        hostname
        username
        isTrusted
        activeState
        lastConnectedAt
        user {
          email
        }
      }
    }
  }
}
"""

_MUTATION_TRUST_DEVICE = """
mutation TrustDevice($id: ID!) {
  deviceUpdate(id: $id, isTrusted: true) {
    ok
    error
    entity {
      id
      name
      isTrusted
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class TwingateClient:
    """Client for the Twingate GraphQL API.

    Args:
        tenant: Twingate tenant subdomain (e.g. ``"mycompany"``).
        api_key: Admin API token generated in Twingate Settings → API.
        batch_size: Number of devices to fetch per pagination page.
    """

    def __init__(self, tenant: str, api_key: str, batch_size: int = 50) -> None:
        self._tenant = tenant
        self._api_key = api_key
        self._batch_size = batch_size
        self._endpoint = f"https://{tenant}.twingate.com/api/graphql/"
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "TwingateClient":
        self._client = build_client(
            headers={
                "X-API-KEY": self._api_key,
                "Content-Type": "application/json",
            }
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_untrusted_devices(self) -> list[TwingateDevice]:
        """Fetch all untrusted devices from Twingate (exhaustive pagination).

        Loops through cursor pages until ``hasNextPage`` is ``False``, then
        returns all devices as a flat list.

        Returns:
            A list of :class:`~src.twingate.models.TwingateDevice` instances.

        Raises:
            httpx.HTTPStatusError: On a non-retryable HTTP error.
            ValueError: If the API returns an unexpected response structure.
        """
        devices: list[TwingateDevice] = []
        cursor: str | None = None

        for _page_num in range(_MAX_PAGES):
            variables: dict[str, Any] = {"first": self._batch_size}
            if cursor:
                variables["after"] = cursor

            data = await self._execute(
                _QUERY_UNTRUSTED_DEVICES,
                variables,
                operation="GetUntrustedDevices",
            )

            connection = TwingateDeviceConnection.model_validate(
                data["devices"]
            )

            for edge in connection.edges:
                devices.append(edge.node)

            if not connection.page_info.has_next_page:
                break

            cursor = connection.page_info.end_cursor
        else:
            logger.warning(
                "Twingate pagination safety limit reached — results may be incomplete",
                max_pages=_MAX_PAGES,
            )

        logger.info(
            "Fetched untrusted devices from Twingate",
            count=len(devices),
        )
        return devices

    async def trust_device(self, device_id: str) -> TrustMutationResult:
        """Mark a single device as trusted via the ``deviceUpdate`` mutation.

        Args:
            device_id: The Twingate device ``id`` (GraphQL ``ID`` scalar).

        Returns:
            A :class:`~src.twingate.models.TrustMutationResult` with ``ok``,
            optional ``error``, and optional ``entity``.

        Raises:
            httpx.HTTPStatusError: On a non-retryable HTTP error.
        """
        data = await self._execute(
            _MUTATION_TRUST_DEVICE,
            {"id": device_id},
            operation="TrustDevice",
        )
        result = TrustMutationResult.model_validate(data["deviceUpdate"])

        if result.ok:
            logger.info(
                "Device trusted",
                twingate_device_id=device_id,
                device_name=result.entity.name if result.entity else None,
            )
        else:
            logger.error(
                "deviceUpdate mutation returned ok=false",
                twingate_device_id=device_id,
                error=result.error,
            )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute(
        self,
        query: str,
        variables: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        """Send a single GraphQL request and return the ``data`` dict.

        Raises:
            ValueError: If the response contains GraphQL ``errors``.
            httpx.HTTPStatusError: On non-retryable HTTP errors.
        """
        if self._client is None:
            raise RuntimeError(
                "TwingateClient must be used as an async context manager"
            )

        payload = {"query": query, "variables": variables, "operationName": operation}

        response = await request_with_retry(
            self._client,
            "POST",
            self._endpoint,
            json=payload,
        )
        response.raise_for_status()

        body: dict[str, Any] = response.json()

        if errors := body.get("errors"):
            # Log the first error message (never the full body — may contain IDs)
            first_msg = errors[0].get("message", "unknown") if errors else "unknown"
            logger.error(
                "Twingate GraphQL error",
                operation=operation,
                error=first_msg,
                error_count=len(errors),
            )
            raise ValueError(
                f"Twingate GraphQL returned errors for {operation}: {first_msg}"
            )

        return body.get("data") or {}
