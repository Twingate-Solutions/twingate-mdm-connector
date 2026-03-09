# Adding a new MDM/EDR provider

This guide walks through every step needed to add a new provider plugin to twingate-device-trust-bridge.

## Overview

Each provider is a Python module in `src/providers/` that implements the `ProviderPlugin` abstract base class. The engine calls `plugin.fetch()` which in turn calls `authenticate()` then `list_devices()`. You only need to implement those two methods plus the compliance helper.

## Step 1 — Add a config model

Open [src/config.py](../src/config.py) and add a Pydantic model for your provider's configuration:

```python
class AcmeConfig(ProviderConfig):
    type: Literal["acme"] = "acme"
    api_key: str
    base_url: str = "https://api.acme.example.com"
```

Then register it in the `ProviderConfigUnion` discriminated union so the YAML loader can instantiate it:

```python
ProviderConfigUnion = Annotated[
    NinjaOneConfig | SophosConfig | ... | AcmeConfig,
    Field(discriminator="type"),
]
```

## Step 2 — Write the provider module

Create `src/providers/acme.py`. Every provider must:

- Inherit from `ProviderPlugin`.
- Implement `name` (property returning a short slug, e.g. `"acme"`).
- Implement `authenticate()` — fetch/cache OAuth tokens or no-op if using static keys.
- Implement `list_devices()` — paginate the API, normalise serials, return `list[ProviderDevice]`.
- Implement `determine_compliance(device: dict) -> bool`.
- Use `request_with_retry` for all HTTP calls — never call `httpx` directly.
- Normalise serial numbers: `serial.strip().upper()`.
- Skip devices without a serial number (log with `log.debug`).
- Exhaust all pages every run.

```python
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import TokenCache, build_client, request_with_retry

class AcmeProvider(ProviderPlugin):
    @property
    def name(self) -> str:
        return "acme"

    def __init__(self, config: AcmeConfig) -> None:
        self._config = config
        self._token_cache = TokenCache()
        self._client = build_client(base_url=config.base_url)

    async def authenticate(self) -> None:
        if not self._token_cache.needs_refresh():
            return
        resp = await request_with_retry(
            self._client, "POST", "/oauth/token",
            data={"grant_type": "client_credentials", ...},
        )
        resp.raise_for_status()
        data = resp.json()
        self._token_cache.set(data["access_token"], data.get("expires_in", 3600))

    async def list_devices(self) -> list[ProviderDevice]:
        devices = []
        page = 1
        while True:
            resp = await request_with_retry(
                self._client, "GET", "/devices",
                headers={"Authorization": f"Bearer {self._token_cache.token}"},
                params={"page": page, "per_page": 100},
            )
            resp.raise_for_status()
            data = resp.json()
            for raw in data.get("devices") or []:
                device = self._build_device(raw)
                if device.serial_number:
                    devices.append(device)
            if not data.get("has_more"):
                break
            page += 1
        return devices

    def determine_compliance(self, device: dict) -> bool:
        return device.get("status") == "managed"

    def _build_device(self, device: dict) -> ProviderDevice:
        return ProviderDevice(
            serial_number=(device.get("serial") or "").strip().upper(),
            hostname=device.get("hostname"),
            os_name=device.get("os"),
            os_version=device.get("os_version"),
            is_online=device.get("online", False),
            is_compliant=self.determine_compliance(device),
            last_seen=None,  # parse from device["last_seen"] if available
            raw=device,
        )
```

## Step 3 — Register in the provider factory

Open [src/main.py](../src/main.py) and add a `case` to `_build_providers()`:

```python
case AcmeConfig():
    from src.providers.acme import AcmeProvider
    plugins.append(AcmeProvider(provider_config))
```

Also add `AcmeConfig` to the import block at the top of `_build_providers()`.

## Step 4 — Write tests

Create `tests/providers/test_acme.py`. Follow the same pattern used by existing tests:

- Use `_make_response(body, status_code)` returning `MagicMock(spec=httpx.Response)`.
- Patch `src.providers.acme.request_with_retry` with `AsyncMock`.
- Test: authentication (success, skip when fresh, error propagation).
- Test: `list_devices` (single page, multi-page, missing serial skipped, normalised serial).
- Test: `determine_compliance` (each branch).
- Test: `name` property.

## Step 5 — Write a setup doc

Create `docs/providers/acme.md` describing:
- Which API credentials are needed and how to generate them.
- The full YAML config block with every field.
- Environment variable names for secrets.
- Any regional or variant options.

## Checklist

- [ ] Config model in `src/config.py` with correct `Literal["acme"]` type discriminator
- [ ] `AcmeConfig` added to `ProviderConfigUnion`
- [ ] `src/providers/acme.py` implementing all abstract methods
- [ ] Registered in `_build_providers()` in `src/main.py`
- [ ] `tests/providers/test_acme.py` with full coverage
- [ ] `docs/providers/acme.md` setup guide
- [ ] `ruff check .` passes
- [ ] `mypy src` passes
- [ ] `pytest` passes
