"""Minimal async HTTP health-check server.

Listens on a configurable TCP port and responds to every request with::

    HTTP/1.1 200 OK
    Content-Type: text/plain

    ok

Enable by setting the ``HEALTHZ_PORT`` environment variable before starting
the application (or in docker-compose / Kubernetes liveness probes).

Example::

    HEALTHZ_PORT=8080 python -m src.main

The server is started as a background ``asyncio.Task`` from :mod:`src.main`
and is cancelled automatically on shutdown.
"""

import asyncio

from src.utils.logging import get_logger

log = get_logger(__name__)

_RESPONSE = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Content-Length: 2\r\n"
    b"Connection: close\r\n"
    b"\r\n"
    b"ok"
)


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Read the incoming request (enough to drain it) then send 200 ok."""
    try:
        # Drain the request line + headers without parsing them.
        await asyncio.wait_for(reader.read(4096), timeout=5.0)
    except (asyncio.TimeoutError, ConnectionResetError):
        pass
    finally:
        try:
            writer.write(_RESPONSE)
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        writer.close()


async def serve_healthz(port: int) -> None:
    """Start the health-check server and serve until cancelled.

    Args:
        port: TCP port to listen on (e.g. ``8080``).

    Raises:
        asyncio.CancelledError: Propagated cleanly on shutdown — the server is
            closed before re-raising so all resources are freed.
    """
    server = await asyncio.start_server(_handle, host="0.0.0.0", port=port)
    log.debug("Health-check server listening", port=port)
    try:
        async with server:
            await server.serve_forever()
    except asyncio.CancelledError:
        log.debug("Health-check server stopped")
        raise
