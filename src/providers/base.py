"""Abstract base class and shared data types for MDM/EDR provider plugins.

Every provider must:
1. Subclass :class:`ProviderPlugin`.
2. Implement :meth:`authenticate`, :meth:`list_devices`, and
   :meth:`determine_compliance`.
3. Return :class:`ProviderDevice` instances from :meth:`list_devices` with
   ``serial_number`` already normalized (``strip().upper()``).

See ``docs/adding-a-provider.md`` for a step-by-step guide.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ProviderDevice:
    """Normalized device record from any MDM/EDR provider.

    The ``serial_number`` is always stored in normalized form
    (``str.strip().upper()``).  Providers must normalize before constructing
    this object.

    Attributes:
        serial_number: Normalized serial number (``strip().upper()``).
        hostname: Device hostname, if available.
        os_name: Operating system name (e.g. ``"Windows 10"``, ``"macOS"``).
        os_version: OS version string.
        is_online: Whether the device is currently online / recently checked in.
        is_compliant: Whether the device passes the provider's compliance checks.
        last_seen: Timestamp of the device's last check-in, if available.
        raw: The original API response dict, preserved for debugging.
    """

    serial_number: str
    hostname: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    is_online: bool = False
    is_compliant: bool = False
    last_seen: datetime | None = None
    raw: dict = field(default_factory=dict)


class ProviderPlugin(ABC):
    """Abstract base class for MDM/EDR provider integrations.

    Subclasses represent a single enabled provider instance.  The sync engine
    calls these methods once per sync cycle:

    1. :meth:`authenticate` — refresh credentials if needed.
    2. :meth:`list_devices` — fetch and return all managed devices.

    :meth:`determine_compliance` is a helper used internally by
    :meth:`list_devices` to compute ``is_compliant`` on each device.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name used in log output (e.g. ``"ninjaone"``)."""
        ...

    @abstractmethod
    async def authenticate(self) -> None:
        """Establish or refresh authentication with the provider API.

        Called before every ``list_devices`` invocation.  Implementations
        should use :class:`~src.utils.http.TokenCache` to avoid redundant
        token requests.

        Raises:
            Exception: Any auth failure.  The engine catches this and skips
                the provider for the current cycle.
        """
        ...

    @abstractmethod
    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all managed devices from the provider.

        Implementations **must** handle pagination internally and exhaust all
        pages before returning.

        Returns:
            A list of :class:`ProviderDevice` instances.  ``serial_number``
            must already be normalized (``strip().upper()``).

        Raises:
            Exception: Any API error.  The engine catches this and skips the
                provider for the current cycle.
        """
        ...

    @abstractmethod
    def determine_compliance(self, device: dict) -> bool:
        """Evaluate whether a raw provider device record is compliant.

        Called during :meth:`list_devices` to compute ``is_compliant`` on
        each :class:`ProviderDevice`.  The ``device`` dict is the raw JSON
        object from the provider API response.

        Args:
            device: Raw device data from the provider API.

        Returns:
            ``True`` if the device passes compliance checks, ``False``
            otherwise.
        """
        ...

    async def fetch(self) -> list[ProviderDevice]:
        """Authenticate then fetch all devices.

        This is the single entry point called by the sync engine.  It calls
        :meth:`authenticate` then :meth:`list_devices` and returns the result.

        Any exception propagates to the caller (the engine logs it and skips
        the provider for the current cycle).
        """
        await self.authenticate()
        return await self.list_devices()
