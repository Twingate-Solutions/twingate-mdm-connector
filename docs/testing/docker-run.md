# Running the Bridge via Docker

This document covers pulling the published GHCR image and running the bridge in a container. For a local Python setup, see [local-run.md](local-run.md).

## Prerequisites

- Docker Desktop (or Docker Engine + Compose plugin)
- A `config.yaml` for the test run (see [local-run.md](local-run.md) for a minimal example)
- Environment variables or a `.env` file for secrets

## Step 1: Pull the image

The image is public — no Docker login required.

```bash
docker pull ghcr.io/twingate-solutions/twingate-mdm-connector:latest
```

To pull a specific version:

```bash
docker pull ghcr.io/twingate-solutions/twingate-mdm-connector:1.03
```

Available tags: `latest`, and version tags like `1.00`, `1.01`, etc.

## Step 2: Write a config.yaml

Create a `config.yaml` in your working directory. The container mounts this file at `/config/config.yaml`.

Example using JumpCloud:

```yaml
twingate:
  tenant: your-tenant-name
  api_key: ${TWINGATE_API_KEY}

sync:
  interval_seconds: 60
  dry_run: true

trust:
  mode: any

providers:
  - type: jumpcloud
    enabled: true
    api_key: ${JUMPCLOUD_API_KEY}
```

## Step 3: Create a .env file for secrets

Create `.env` in the same directory as `docker-compose.yml`:

```
TWINGATE_API_KEY=your_twingate_api_key
JUMPCLOUD_API_KEY=your_jumpcloud_api_key
```

Add `.env` to `.gitignore` to avoid committing credentials.

## Step 4: Create a docker-compose.override.yml

Rather than editing the base `docker-compose.yml`, create a `docker-compose.override.yml` alongside it. Docker Compose merges the two files automatically.

```yaml
services:
  twingate-mdm-connector:
    image: ghcr.io/twingate-solutions/twingate-mdm-connector:latest
    volumes:
      - ./config.yaml:/config/config.yaml:ro
    environment:
      - CONFIG_FILE=/config/config.yaml
      - TWINGATE_API_KEY=${TWINGATE_API_KEY}
      - JUMPCLOUD_API_KEY=${JUMPCLOUD_API_KEY}
      - HEALTHZ_PORT=8080
    ports:
      - "8080:8080"
    restart: unless-stopped
```

If you have additional providers, add their env vars to the `environment` list and update `config.yaml`.

## Step 5: Start the container

```bash
docker compose up -d
```

To see startup logs immediately:

```bash
docker compose up
```

(without `-d` — runs in the foreground)

## Step 6: Read logs

```bash
# Follow logs (Ctrl+C to stop following)
docker logs -f twingate-mdm-connector

# Show last 50 lines
docker logs --tail 50 twingate-mdm-connector

# Pretty-print JSON (requires jq)
docker logs twingate-mdm-connector 2>&1 | jq .

# Filter for trust events only
docker logs twingate-mdm-connector 2>&1 | jq 'select(.action != null)'

# Filter for errors
docker logs twingate-mdm-connector 2>&1 | jq 'select(.level == "error")'
```

## Step 7: Check the health endpoint

When `HEALTHZ_PORT` is set, the bridge starts a TCP listener on that port. Any successful TCP connection means the process is alive. This is designed for Docker `HEALTHCHECK` directives and Kubernetes liveness probes.

```bash
# Using curl (shows connection result)
curl -v telnet://localhost:8080

# Using netcat
nc -z localhost 8080 && echo "healthy" || echo "not responding"

# Using PowerShell (Windows)
Test-NetConnection -ComputerName localhost -Port 8080
```

To add a Docker health check to `docker-compose.override.yml`:

```yaml
services:
  twingate-mdm-connector:
    healthcheck:
      test: ["CMD", "nc", "-z", "localhost", "8080"]
      interval: 30s
      timeout: 5s
      retries: 3
```

## Step 8: Stop the container

```bash
docker compose down
```

## Running without Compose

If you prefer `docker run` directly:

**Linux / macOS:**

```bash
docker run -d \
  --name twingate-mdm-connector \
  -v "$(pwd)/config.yaml:/config/config.yaml:ro" \
  -e CONFIG_FILE=/config/config.yaml \
  -e TWINGATE_API_KEY=your_key \
  -e JUMPCLOUD_API_KEY=your_key \
  -e HEALTHZ_PORT=8080 \
  -p 8080:8080 \
  ghcr.io/twingate-solutions/twingate-mdm-connector:latest
```

**Windows (PowerShell):**

```powershell
docker run -d `
  --name twingate-mdm-connector `
  -v "${PWD}/config.yaml:/config/config.yaml:ro" `
  -e CONFIG_FILE=/config/config.yaml `
  -e TWINGATE_API_KEY=your_key `
  -e JUMPCLOUD_API_KEY=your_key `
  -e HEALTHZ_PORT=8080 `
  -p 8080:8080 `
  ghcr.io/twingate-solutions/twingate-mdm-connector:latest
```

## Switching between dry-run and live mode

Edit `config.yaml` and change `dry_run: true` to `dry_run: false`, then restart:

```bash
docker compose restart twingate-mdm-connector
```

Or stop and start:

```bash
docker compose down && docker compose up -d
```

## Rebuilding from source

To build the image from the local Dockerfile instead of pulling from GHCR:

```bash
docker build -t twingate-mdm-connector:local .
```

Then update your `docker-compose.override.yml`:

```yaml
services:
  twingate-mdm-connector:
    image: twingate-mdm-connector:local
```
