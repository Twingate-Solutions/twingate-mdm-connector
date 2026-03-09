"""Pydantic models for Twingate GraphQL API objects.

These models represent the data returned by the Twingate GraphQL API —
specifically the device objects we query and the mutation results we receive.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class TwingateUser(BaseModel):
    """Minimal user object embedded in a device record."""

    email: str | None = None


class TwingateDevice(BaseModel):
    """A device object as returned by the Twingate GraphQL API.

    Corresponds to the fields requested in the ``GetUntrustedDevices`` query.
    All fields except ``id`` are nullable — Twingate may not have data for
    every field on every device.
    """

    id: str
    name: str | None = None
    serial_number: str | None = Field(None, alias="serialNumber")
    os_name: str | None = Field(None, alias="osName")
    os_version: str | None = Field(None, alias="osVersion")
    hostname: str | None = None
    username: str | None = None
    is_trusted: bool = Field(False, alias="isTrusted")
    active_state: str | None = Field(None, alias="activeState")
    last_connected_at: datetime | None = Field(None, alias="lastConnectedAt")
    user: TwingateUser | None = None

    model_config = {"populate_by_name": True}


class TwingatePageInfo(BaseModel):
    """Cursor-pagination metadata returned by the Twingate GraphQL API."""

    has_next_page: bool = Field(alias="hasNextPage")
    end_cursor: str | None = Field(None, alias="endCursor")

    model_config = {"populate_by_name": True}


class TwingateDeviceEdge(BaseModel):
    """A single edge in the ``devices`` connection."""

    node: TwingateDevice


class TwingateDeviceConnection(BaseModel):
    """The paginated ``devices`` connection returned by the API."""

    page_info: TwingatePageInfo = Field(alias="pageInfo")
    edges: list[TwingateDeviceEdge] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class TrustMutationEntity(BaseModel):
    """The ``entity`` object returned by a successful ``deviceUpdate`` mutation."""

    id: str
    name: str | None = None
    is_trusted: bool = Field(False, alias="isTrusted")

    model_config = {"populate_by_name": True}


class TrustMutationResult(BaseModel):
    """The result object returned by the ``deviceUpdate`` mutation.

    ``ok`` is ``True`` when the mutation succeeded. When ``False``, ``error``
    contains a human-readable message from Twingate.
    """

    ok: bool
    error: str | None = None
    entity: TrustMutationEntity | None = None
