# CrowdStrike Falcon provider setup

CrowdStrike Falcon is a cloud-native endpoint protection platform (EPP/EDR). Its sensor reports host inventory, sensor health, and containment state for Windows, macOS, and Linux machines. The connector queries the Falcon **Hosts API** to enumerate every host the sensor knows about, and trusts a device in Twingate when its sensor is healthy and the host has not been network-isolated.

## Getting an account

CrowdStrike offers a **15-day free trial** of the Falcon Go bundle (Falcon Prevent, Device Control, and Falcon for Mobile); additional modules can be tested during the trial at no extra cost.

1. Go to [https://www.crowdstrike.com/en-us/products/trials/try-falcon-prevent/](https://www.crowdstrike.com/en-us/products/trials/try-falcon-prevent/).
2. Complete the registration form. CrowdStrike emails you a link to activate your Falcon console.
3. Install the Falcon sensor on at least one test machine so you have hosts to query. In the console: **Host setup and management → Deploy → Sensor downloads**, and note your **Customer ID (CID)** — the installer needs it.

## Generating API credentials

Falcon uses **OAuth2 Client Credentials**. You create an API client in the Falcon console and are given a Client ID and Client Secret.

1. Log in to your Falcon console (e.g. `https://falcon.crowdstrike.com/`).
2. Open the menu and go to **Support and resources → Resources and tools → API clients and keys**. On older console layouts this is **Support → API Clients and Keys**.
3. Under **OAuth2 API clients**, click **Create API client** (labelled **Add new API client** on older consoles). If the button is unavailable, your role lacks permission — creating API clients requires the **Falcon Administrator** role.
4. In the panel that opens:
   - **Client name:** something identifiable, e.g. `twingate-mdm-connector`.
   - **Description:** optional.
   - **API scopes:** find **Hosts** in the list and tick **Read**. Leave every other scope unticked — the connector only ever reads host data, and it never needs `Write` on any scope.
5. Click **Create** (**Add** on older consoles). Falcon displays the **Client ID**, **Client Secret**, and **Base URL**.
6. Copy all three values now. The Client Secret is shown only once. If you lose it, you must reset the secret or create a new client.

The **Base URL** shown on that screen tells you which Falcon cloud to configure — see the table below.

### Falcon clouds

Set `cloud` to the value matching the Base URL that Falcon showed you:

| Base URL shown in the console | `cloud` value |
|-------------------------------|---------------|
| `https://api.crowdstrike.com` | `us-1` |
| `https://api.us-2.crowdstrike.com` | `us-2` |
| `https://api.us-3.crowdstrike.com` | `us-3` |
| `https://api.eu-1.crowdstrike.com` | `eu-1` |
| `https://api.laggar.gcw.crowdstrike.com` | `us-gov-1` |
| `https://api.us-gov-2.crowdstrike.mil` | `us-gov-2` |

`cloud` is a closed set. A value that is not in this list is rejected at startup rather than turned into a request to a nonexistent host.

## Configuration

Add the provider to your `config.yaml` under the `providers` list.

```yaml
providers:
  - type: crowdstrike
    enabled: true
    cloud: us-1
    client_id: ${CROWDSTRIKE_CLIENT_ID}
    client_secret: ${CROWDSTRIKE_CLIENT_SECRET}
```

### Fields

| Field                                | Required | Default | Description                                                          |
|--------------------------------------|----------|---------|----------------------------------------------------------------------|
| `type`                               | Yes      | —       | Must be `crowdstrike`                                                |
| `enabled`                            | No       | `false` | Set to `false` to disable without removing the block                 |
| `cloud`                              | No       | `us-1`  | Falcon cloud: `us-1`, `us-2`, `us-3`, `eu-1`, `us-gov-1`, `us-gov-2` |
| `client_id`                          | Yes      | —       | OAuth2 client ID from API clients and keys                           |
| `client_secret`                      | Yes      | —       | OAuth2 client secret                                                 |
| `compliance.require_not_contained`   | No       | `true`  | Reject hosts that have been network-contained                        |
| `compliance.require_full_sensor`     | No       | `true`  | Reject hosts running in Reduced Functionality Mode                   |
| `compliance.require_live`            | No       | `false` | Require a currently-connected sensor (extra API call)                |

The `compliance` block is optional — omit it entirely to use the defaults above.

## Environment variables

Store your credentials in environment variables and reference them in `config.yaml` using `${VAR}` syntax. Never hard-code secrets in the config file.

| Variable                     | Description                    |
|------------------------------|--------------------------------|
| `CROWDSTRIKE_CLIENT_ID`      | Falcon API client ID           |
| `CROWDSTRIKE_CLIENT_SECRET`  | Falcon API client secret       |

Example `.env` file (for local testing only — use your secrets manager in production):

```env
CROWDSTRIKE_CLIENT_ID=your-client-id-here
CROWDSTRIKE_CLIENT_SECRET=your-client-secret-here
```

## Compliance logic

Two checks run against each host record, both enabled by default. A host must pass every enabled check to be considered compliant.

### Containment status (`require_not_contained`)

A host is non-compliant when its `status` is `contained` or `containment_pending`. Those values mean an analyst has network-isolated the host from the Falcon console — normally in response to a detection — which is the strongest possible signal not to grant it network access. `normal` and `lifted_containment` pass.

### Sensor health (`require_full_sensor`)

A host is non-compliant when `reduced_functionality_mode` is `yes`. RFM means the sensor is running in a degraded state (typically an unsupported kernel on Linux, or a macOS host missing its Full Disk Access / system-extension grant), so its telemetry and prevention capability cannot be relied on.

### Fail-open behaviour

Both checks fail **open** on an absent or unrecognised value. `reduced_functionality_mode` is frequently missing from host records on macOS and Linux, and `status` can be absent on freshly enrolled hosts. A host that does not report a signal is not treated as failing it — only an explicit failing value marks a host non-compliant. This matches the behaviour of the other providers in this connector.

### Online status (`require_live`)

By default, `is_online` means **"this sensor has checked in at least once"** — i.e. the host record has a `last_seen` timestamp. This is deliberately lenient: CrowdStrike's real-time online signal means "the sensor is connected *right now*", and since `trust.require_online` defaults to `true`, mapping it straight through would leave every closed laptop untrustable. Check-in *recency* is handled centrally and consistently by `trust.max_days_since_checkin` (7 days by default), so it is not double-counted here.

Set `compliance.require_live: true` to opt into the stricter reading. The connector then calls `/devices/entities/online-state/v1` (batched 100 hosts per request) and sets `is_online` only for hosts whose state is `online`. Combined with the default `trust.require_online: true`, this restricts trust to hosts that are powered on and connected to Falcon at the moment the sync cycle runs — a host absent from the online-state response is treated as not live.

## Notes

- **Two-phase fetch:** the connector first drains `GET /devices/queries/devices-scroll/v1` into a complete list of host IDs, then hydrates those IDs in batches via `POST /devices/entities/devices/v2` (500 IDs per request). The two phases are strictly sequential and never interleaved: the scroll offset pointer expires after two minutes of inactivity, so a slow detail call in the middle of enumeration could invalidate the pointer and silently truncate the inventory.

- **Fleet size:** the scroll endpoint has no 10,000-record ceiling (unlike the older `/devices/queries/devices/v1` offset pagination), so the full inventory is enumerated regardless of fleet size. A `_MAX_PAGES` safety valve of 500 pages × 5,000 IDs logs a warning rather than looping forever.

- **Serial numbers:** the connector reads the host's `serial_number` field and normalises it with `strip().upper()`. Hosts that report no serial — common for some virtual machines and cloud instances — are skipped with a `DEBUG` log line and never reach the trust evaluation.

- **Token caching:** Falcon access tokens live for 30 minutes. The token is cached in memory and refreshed proactively 60 s before expiry, so a single token typically covers several sync cycles.

- **Rate limits:** all calls go through the shared retry helper, which honours `Retry-After` on 429 responses and backs off exponentially with jitter on 5xx.

- **MSSP / Flight Control:** the connector queries only the CID that the API client belongs to. Member CIDs under a parent (Falcon Flight Control) are not enumerated — each member CID needs its own API client and its own provider entry.

- **Hidden hosts:** hosts that have been hidden in the Falcon console (`/devices/queries/devices-hidden/v1`) are not queried. Only visible, enrolled hosts are considered.
