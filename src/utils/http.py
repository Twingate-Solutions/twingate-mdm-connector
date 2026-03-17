"""Shared HTTP client utilities for twingate-device-trust-bridge.

Provides:
- :func:`build_client` — factory for a pre-configured ``httpx.AsyncClient``.
- :func:`request_with_retry` — executes a single HTTP request with
  exponential-backoff retry on transient errors (429, 5xx).
- :class:`TokenCache` — lightweight OAuth2 access-token cache with
  proactive refresh before expiry.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CONNECT_TIMEOUT = 10.0   # seconds
DEFAULT_READ_TIMEOUT = 30.0      # seconds
DEFAULT_MAX_RETRIES = 4
DEFAULT_BACKOFF_BASE = 1.0       # seconds — doubles each attempt
DEFAULT_BACKOFF_MAX = 60.0       # seconds — upper cap
DEFAULT_JITTER_FACTOR = 0.25     # ±25 % random jitter

# HTTP status codes that warrant a retry.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def build_client(
    base_url: str = "",
    headers: dict[str, str] | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
) -> httpx.AsyncClient:
    """Create a pre-configured :class:`httpx.AsyncClient`.

    Each provider should create its own client instance to benefit from
    per-provider connection pooling. The caller is responsible for closing
    the client (``async with`` or ``await client.aclose()``).

    Args:
        base_url: Optional base URL prepended to all relative request paths.
        headers: Default headers included with every request.
        connect_timeout: TCP connection timeout in seconds.
        read_timeout: Response read timeout in seconds.

    Returns:
        A configured :class:`httpx.AsyncClient`.
    """
    timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=10.0, pool=5.0)
    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers or {},
        timeout=timeout,
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    backoff_max: float = DEFAULT_BACKOFF_MAX,
    **kwargs: Any,
) -> httpx.Response:
    """Execute an HTTP request with exponential-backoff retry.

    Retries on network errors and responses with status codes in
    ``{429, 500, 502, 503, 504}``.  Respects the ``Retry-After`` header
    when present on 429 responses.

    Args:
        client: An active :class:`httpx.AsyncClient`.
        method: HTTP method string (``"GET"``, ``"POST"``, etc.).
        url: Request URL (may be relative if *client* has a ``base_url``).
        max_retries: Maximum number of retry attempts after the initial
            request. Total attempts = ``max_retries + 1``.
        backoff_base: Initial back-off wait in seconds; doubles each attempt.
        backoff_max: Upper bound for computed back-off before jitter.
        **kwargs: Passed verbatim to :meth:`httpx.AsyncClient.request`.

    Returns:
        The first successful :class:`httpx.Response`.

    Raises:
        httpx.HTTPStatusError: If all attempts are exhausted and the last
            response had a non-retryable error status.
        httpx.RequestError: If a network-level error persists after all
            retries.
    """
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = await client.request(method, url, **kwargs)

            if response.status_code not in _RETRYABLE_STATUS:
                return response

            # --- retryable status code ---
            wait = _compute_wait(response, attempt, backoff_base, backoff_max)
            _log_retry(method, url, response.status_code, attempt, max_retries, wait)

            if attempt < max_retries:
                await asyncio.sleep(wait)

            last_exc = None  # clear any previous network error

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            wait = _backoff_with_jitter(attempt, backoff_base, backoff_max)
            _log_retry(method, url, None, attempt, max_retries, wait, error=str(exc))
            last_exc = exc

            if attempt < max_retries:
                await asyncio.sleep(wait)

    # All attempts exhausted.
    if last_exc is not None:
        raise last_exc

    # Return the last response (retryable status but retries exhausted).
    response.raise_for_status()
    return response  # unreachable if raise_for_status raises, but satisfies type checker


def _backoff_with_jitter(attempt: int, base: float, cap: float) -> float:
    """Compute exponential back-off with ±25 % random jitter."""
    delay = min(base * (2 ** attempt), cap)
    jitter = delay * DEFAULT_JITTER_FACTOR * (2 * random.random() - 1)
    return max(0.0, delay + jitter)


def _compute_wait(
    response: httpx.Response, attempt: int, base: float, cap: float
) -> float:
    """Return wait time, honouring the ``Retry-After`` header if present."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return _backoff_with_jitter(attempt, base, cap)


def _log_retry(
    method: str,
    url: str,
    status: int | None,
    attempt: int,
    max_retries: int,
    wait: float,
    error: str | None = None,
) -> None:
    reason = f"HTTP {status}" if status else f"network error: {error}"
    logger.warning(
        "HTTP retry scheduled",
        extra={
            "method": method,
            "url": url,
            "reason": reason,
            "attempt": attempt + 1,
            "max_attempts": max_retries + 1,
            "wait_seconds": round(wait, 2),
        },
    )


# ---------------------------------------------------------------------------
# OAuth2 token cache
# ---------------------------------------------------------------------------


@dataclass
class TokenCache:
    """Simple in-memory cache for OAuth2 access tokens.

    Stores a token and its expiry epoch, and provides a helper to check
    whether the token needs refreshing.  A *proactive refresh margin*
    (default 60 s) ensures the token is renewed before it actually expires,
    reducing the chance of using a stale token mid-request.

    Example::

        cache = TokenCache()
        if cache.needs_refresh():
            token, expires_in = await _fetch_token(...)
            cache.set(token, expires_in)
        headers = {"Authorization": f"Bearer {cache.token}"}
    """

    token: str = field(default="")
    expires_at: float = field(default=0.0)
    refresh_margin_seconds: int = field(default=60)

    def set(self, token: str, expires_in: int) -> None:
        """Store a token and record its expiry time.

        Args:
            token: The raw access token string.
            expires_in: Lifetime of the token in seconds, as returned by
                the auth server (e.g. ``3600``).
        """
        self.token = token
        self.expires_at = time.monotonic() + expires_in

    def needs_refresh(self) -> bool:
        """Return ``True`` if the token is missing or about to expire."""
        return not self.token or time.monotonic() >= (
            self.expires_at - self.refresh_margin_seconds
        )
