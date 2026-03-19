# FleetDM provider setup

Fleet is an open-source device management platform built on osquery. It collects real-time telemetry from macOS, Windows, and Linux endpoints and evaluates policy compliance based on SQL-based queries you define. It can be self-hosted for free or used via Fleet's managed cloud offering.

---

## Getting an account

Fleet is **open-source and free to self-host**. There is also a commercial cloud trial available, but for testing with this connector the self-hosted OSS version is recommended because it requires no sign-up and is ready in minutes via Docker.

### Option A — Self-hosted (recommended for testing)

Run a local Fleet instance with a single Docker command:

```bash
docker run -p 8080:8080 fleetdm/fleet:latest
```

Fleet will be available at `http://localhost:8080`. Complete the setup wizard in your browser to create the initial admin account.

For a persistent setup with a backing database, see the [Fleet documentation](https://fleetdm.com/docs/deploy/deploy-fleet) for a full Docker Compose example.

### Option B — Fleet Cloud trial

1. Go to [https://fleetdm.com/](https://fleetdm.com/) and click **Try Fleet**.
2. Fill in your work email and company details. Fleet will provision a hosted trial environment.
3. Follow the onboarding steps to enrol at least one device before configuring this connector.

> **TLS note:** If you are running Fleet self-hosted, the bridge requires a valid TLS certificate on your Fleet instance (not a self-signed cert) unless you configure a trusted CA in the Docker environment running the bridge. For local testing, running Fleet on `http://` (no TLS) and setting `base_url` to `http://localhost:8080` is acceptable.

---

## Generating API credentials

Fleet supports two ways to obtain an API token. A dedicated service account is strongly recommended for production — it avoids the token being invalidated when a human user logs out or changes their password.

### Option A — Personal API token (quickest for testing)

1. Log in to your Fleet instance.
2. Click your **avatar** in the top-right corner of the Fleet UI.
3. Select **My account**.
4. Under the **Get API token** section, click **Get API token** to reveal and copy your token.

### Option B — Dedicated service account (recommended for production)

1. In Fleet, go to **Settings → Users**.
2. Click **Create user**.
3. Fill in a name (e.g. `twingate-bridge`) and email address, set a strong password, and assign the **Observer** role. Observer is the minimum role needed — it allows reading host details and policy results but cannot make changes to Fleet.
4. Log in to Fleet as that new user (in a separate browser session or incognito window).
5. Follow Option A steps 2–4 to retrieve the API token for the service account.

> **Token lifetime:** Fleet API tokens do not expire automatically, but they are tied to the user session. If the user's password is changed or the session is invalidated, you will need to generate a new token.

---

## Configuration

Add the following block to your `config.yaml`:

```yaml
providers:
  - type: fleetdm
    enabled: true
    base_url: https://fleet.corp.example.com
    api_token: ${FLEETDM_API_TOKEN}
```

### Fields

| Field       | Required | Default | Description                                          |
|-------------|----------|---------|------------------------------------------------------|
| `type`      | Yes      | —       | Must be `fleetdm`                                    |
| `enabled`   | Yes      | —       | Set to `true` to activate this provider              |
| `base_url`  | Yes      | —       | Full URL of your Fleet instance (no trailing slash)  |
| `api_token` | Yes      | —       | Fleet API token (use an env-var reference)           |

---

## Environment variables

Store your credentials in environment variables and reference them in `config.yaml` using `${VAR}` syntax. Never hard-code secrets in the config file.

| Variable            | Description                       |
|---------------------|-----------------------------------|
| `FLEETDM_API_TOKEN` | Fleet API token for the connector |

Example `.env` file (for local testing only — use your secrets manager in production):

```env
FLEETDM_API_TOKEN=your-fleet-api-token-here
```

---

## Compliance logic

A device is marked **compliant** when **all** policies applied to it have `response == "pass"`. A device with no policies assigned is also considered compliant (there is nothing to fail).

Policy results are read from the per-host detail endpoint (`GET /api/v1/fleet/hosts/{id}`), which returns a `policies` array. Each entry in that array has a `response` field that can be `"pass"`, `"fail"`, or `""` (not yet evaluated). The bridge treats any response other than `"pass"` or `""` as a failure.

The bridge fetches host details **concurrently** (up to 20 hosts in parallel) to keep sync times low on large fleets.

Fleet does **not** expose an explicit online/offline status — all enrolled devices are reported as online by this connector.

---

## Notes

- **Pagination:** The bridge first pages through `GET /api/v1/fleet/hosts` using 1-indexed pages with a page size of 1000. It stops when `meta.has_next_results` is `false`. After collecting all host IDs, it concurrently fetches the detail endpoint for each host to retrieve policy results.
- **Detail call fallback:** If the per-host detail call fails for a specific host (e.g. due to a transient error), the bridge falls back to the data available from the list endpoint. The host will have no policy information and will be treated as compliant. An error is logged so you can investigate.
- **Serial numbers:** The hardware serial number is read from the `hardware_serial` field on the host object. Hosts without a serial number are silently skipped.
- **TLS certificates:** Self-hosted Fleet instances must present a TLS certificate that the bridge's Docker container trusts. If you use a private CA, mount the CA certificate into the container and configure it as a trusted CA.
- **Policy scope:** Fleet policies can be scoped to specific teams. The bridge reads all policies visible to the API token's user, regardless of team assignment.
