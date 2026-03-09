# Twingate MDM Connector

Automatically trust devices in [Twingate](https://www.twingate.com/) by cross-referencing your MDM/EDR providers. Runs as a lightweight Docker container, requires no database, and never untrusts a device.

## How it works

On each sync cycle the bridge:

1. Queries every enabled MDM/EDR provider in parallel for their device inventory.
2. Matches devices to Twingate resources by serial number.
3. Marks a device as trusted in Twingate if it passes the compliance check for the configured trust mode.

```
MDM/EDR providers          twingate-device-trust-bridge          Twingate
──────────────────         ──────────────────────────────         ─────────
NinjaOne  ─────────┐
Sophos    ─────────┤
ManageEngine  ─────┤──►  serial number match + compliance  ──►  isTrusted: true
Automox   ─────────┤       (never sets isTrusted: false)
JumpCloud ─────────┤
FleetDM   ─────────┤
Mosyle    ─────────┤
Datto RMM ─────────┘
```

## Quick start

### 1. Create a config file

```yaml
# config.yaml
twingate:
  api_url: https://your-network.twingate.com/api/graphql/
  api_key: ${TWINGATE_API_KEY}

trust:
  mode: any           # trust if compliant in ANY enabled provider
  max_days_since_checkin: 30

sync:
  interval_seconds: 300
  dry_run: false      # set true to log decisions without mutating Twingate

logging:
  level: INFO

providers:
  - type: ninjaone
    enabled: true
    region: app
    client_id: ${NINJAONE_CLIENT_ID}
    client_secret: ${NINJAONE_CLIENT_SECRET}

  - type: datto
    enabled: true
    api_url: https://pinotage-api.centrastage.net
    api_key: ${DATTO_API_KEY}
    api_secret: ${DATTO_API_SECRET}
```

See [docs/providers/](docs/providers/) for per-provider configuration guides.

### 2. Run with Docker

```bash
docker run --rm \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  -e TWINGATE_API_KEY=your-key \
  -e NINJAONE_CLIENT_ID=your-id \
  -e NINJAONE_CLIENT_SECRET=your-secret \
  ghcr.io/your-org/twingate-device-trust-bridge:latest
```

### 3. Run with Docker Compose

```yaml
# docker-compose.yml
services:
  bridge:
    image: ghcr.io/your-org/twingate-device-trust-bridge:latest
    restart: unless-stopped
    volumes:
      - ./config.yaml:/app/config.yaml:ro
    env_file: .env
    environment:
      HEALTHZ_PORT: "8080"   # optional liveness probe
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:8080"]
      interval: 60s
      timeout: 5s
      retries: 3
```

## Configuration reference

### Top-level keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `twingate.api_url` | string | — | GraphQL API endpoint |
| `twingate.api_key` | string | — | Twingate API key |
| `trust.mode` | `any` \| `all` | `any` | Trust if compliant in any vs all providers |
| `trust.max_days_since_checkin` | int | `30` | Devices not seen in this many days are skipped |
| `sync.interval_seconds` | int | `300` | How often to run a sync cycle |
| `sync.dry_run` | bool | `false` | Log decisions without mutating Twingate |
| `logging.level` | string | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Environment variable interpolation

Any config value can be replaced with `${ENV_VAR}` and the bridge will substitute the value at startup. Secrets should always be passed as environment variables rather than embedded in the config file.

### Health check

Set `HEALTHZ_PORT=8080` (or any port) to enable a minimal TCP HTTP server that responds `200 ok` to every request. Use this as a Docker/Kubernetes liveness probe.

## Supported providers

| Provider | Auth | Docs |
|----------|------|------|
| NinjaOne | OAuth2 client credentials | [docs/providers/ninjaone.md](docs/providers/ninjaone.md) |
| Sophos | OAuth2 client credentials + tenant discovery | [docs/providers/sophos.md](docs/providers/sophos.md) |
| ManageEngine | API token (on-prem) or Zoho OAuth2 (cloud) | [docs/providers/manageengine.md](docs/providers/manageengine.md) |
| Automox | API key | [docs/providers/automox.md](docs/providers/automox.md) |
| JumpCloud | API key | [docs/providers/jumpcloud.md](docs/providers/jumpcloud.md) |
| FleetDM | Bearer token | [docs/providers/fleetdm.md](docs/providers/fleetdm.md) |
| Mosyle | Access token + email + password | [docs/providers/mosyle.md](docs/providers/mosyle.md) |
| Datto RMM | OAuth2 password grant | [docs/providers/datto.md](docs/providers/datto.md) |

## Trust logic

- **`trust.mode: any`** — A device is trusted if it is enrolled and compliant in at least one enabled provider.
- **`trust.mode: all`** — A device must be enrolled and compliant in every enabled provider that recognises it.
- Devices matched in zero providers are never trusted.
- Devices last seen more than `max_days_since_checkin` days ago are skipped.
- A device that is already trusted in Twingate is never set to untrusted.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check .

# Type-check
mypy src
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contribution guide, and [docs/adding-a-provider.md](docs/adding-a-provider.md) to learn how to add a new MDM/EDR provider.

## License

Apache 2.0 — see [LICENSE](LICENSE).
