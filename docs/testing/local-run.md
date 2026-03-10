# Running the Bridge Locally

This document covers running the bridge directly on your host machine using Python and pip. For Docker-based setup, see [docker-run.md](docker-run.md).

## Prerequisites

- Python 3.12 or later (`python --version`)
- `pip` and `venv`
- A `config.yaml` (see below)
- Environment variables for secrets

## Step 1: Install

```bash
# Clone the repo
git clone https://github.com/twingate-solutions/twingate-mdm-connector.git
cd twingate-mdm-connector

# Create and activate a virtual environment
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Install dependencies (including dev extras for test tools)
pip install -e ".[dev]"
```

## Step 2: Write a minimal config.yaml

For a first test, use a single provider. The example below uses **JumpCloud** (free tier, simplest auth). See [credentials.md](credentials.md) for other providers.

Create `config.yaml` in the project root:

```yaml
twingate:
  tenant: your-tenant-name   # e.g. "acme" from acme.twingate.com
  api_key: ${TWINGATE_API_KEY}

sync:
  interval_seconds: 60
  dry_run: true   # always start with dry_run: true

trust:
  mode: any   # trust if compliant in ANY enabled provider

providers:
  - type: jumpcloud
    enabled: true
    api_key: ${JUMPCLOUD_API_KEY}
```

The `${VAR}` syntax reads the value from the environment at startup. Never put raw secrets in `config.yaml` if the file may be committed to version control.

## Step 3: Set environment variables

**Linux / macOS:**

```bash
export TWINGATE_API_KEY=your_twingate_api_key
export JUMPCLOUD_API_KEY=your_jumpcloud_api_key
```

**Windows (PowerShell):**

```powershell
$env:TWINGATE_API_KEY = "your_twingate_api_key"
$env:JUMPCLOUD_API_KEY = "your_jumpcloud_api_key"
```

## Step 4: Run the bridge

```bash
python -m src.main
```

To use a config file at a custom path:

```bash
CONFIG_FILE=/path/to/config.yaml python -m src.main
```

**Windows (PowerShell):**

```powershell
$env:CONFIG_FILE = "C:\path\to\config.yaml"
python -m src.main
```

## What startup looks like

The bridge outputs structured JSON logs to stdout. Each log line is a JSON object. A successful startup looks like:

```json
{"event": "config_loaded", "providers": ["jumpcloud"], "dry_run": true, "level": "info", "timestamp": "2025-03-10T10:00:00Z"}
{"event": "scheduler_started", "interval_seconds": 60, "level": "info", "timestamp": "2025-03-10T10:00:00Z"}
{"event": "sync_started", "cycle": 1, "level": "info", "timestamp": "2025-03-10T10:00:01Z"}
{"event": "provider_auth_ok", "provider": "jumpcloud", "level": "info", "timestamp": "2025-03-10T10:00:01Z"}
{"event": "provider_devices_fetched", "provider": "jumpcloud", "count": 3, "level": "info", "timestamp": "2025-03-10T10:00:02Z"}
{"event": "twingate_devices_fetched", "count": 1, "level": "info", "timestamp": "2025-03-10T10:00:02Z"}
```

## What a successful dry-run trust event looks like

When the bridge finds a matching, compliant device in dry-run mode:

```json
{"event": "device_would_trust", "action": "would_trust", "device_serial": "VMW-1234-5678", "twingate_device_id": "RGV2aWNlOjE2MzU4NA==", "provider": "jumpcloud", "level": "info", "timestamp": "2025-03-10T10:00:02Z"}
```

And the sync summary:

```json
{"event": "sync_complete", "cycle": 1, "would_trust": 1, "already_trusted": 0, "no_match": 0, "skipped": 0, "errors": 0, "level": "info", "timestamp": "2025-03-10T10:00:02Z"}
```

## Step 5: Switch to a live run

When the dry run shows `"action": "would_trust"` for the right device, switch to live mode:

1. Change `dry_run: true` to `dry_run: false` in `config.yaml`.
2. Restart the bridge.

A successful live trust event:

```json
{"event": "device_trusted", "action": "trusted", "device_serial": "VMW-1234-5678", "twingate_device_id": "RGV2aWNlOjE2MzU4NA==", "provider": "jumpcloud", "level": "info", "timestamp": "2025-03-10T10:00:05Z"}
```

After this log line, verify the device shows **Trust Status: Trusted** in the Twingate Admin Console.

## Reading and filtering logs

Each log line is a JSON object. Useful fields:

| Field | Description |
|---|---|
| `event` | What happened (e.g. `device_trusted`, `provider_auth_error`) |
| `action` | Trust decision: `trusted`, `would_trust`, `no_match`, `skipped`, `already_trusted` |
| `device_serial` | Normalised device serial number |
| `twingate_device_id` | Twingate device ID (base64 encoded) |
| `provider` | Provider name that triggered the trust decision |
| `level` | Log level: `debug`, `info`, `warning`, `error` |

**Pretty-print logs:**

```bash
python -m src.main | python -m json.tool
```

**Filter for trust events only (requires `jq`):**

```bash
python -m src.main | jq 'select(.action != null)'
```

**Filter for errors:**

```bash
python -m src.main | jq 'select(.level == "error")'
```

## Stopping the bridge

Press `Ctrl+C`. The bridge handles `SIGINT` with a graceful shutdown and logs:

```json
{"event": "shutdown", "reason": "signal", "level": "info", "timestamp": "2025-03-10T10:05:00Z"}
```

## Running tests

The test suite uses pytest with mocked HTTP responses — no real credentials needed:

```bash
pytest tests/ -v
```

To run a specific test file:

```bash
pytest tests/test_matching.py -v
pytest tests/providers/test_jumpcloud.py -v
```
