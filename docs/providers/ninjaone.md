# NinjaOne provider setup

NinjaOne is a remote monitoring and management (RMM) platform used to manage, patch, and monitor Windows, macOS, and Linux endpoints across an organisation. It provides detailed device health, patch status, and antivirus telemetry that this bridge uses to determine whether a device should be trusted in Twingate.

## Getting an account

NinjaOne offers a **30-day free trial** — no credit card required.

1. Go to [https://www.ninjaone.com/](https://www.ninjaone.com/).
2. Click **Get a Demo** or **Start Free Trial** on the homepage.
3. Fill in the registration form. You will receive a confirmation email with access to your NinjaOne tenant.

Your tenant is hosted on one of NinjaOne's regional instances. The region you are assigned during sign-up determines which `region` value you will use in the config (see the Fields table below).

## Generating API credentials

NinjaOne uses **OAuth2 Client Credentials** for machine-to-machine authentication. You will create a dedicated API application and note down its Client ID and Client Secret.

1. Log in to your NinjaOne tenant.
2. In the left-hand sidebar, click **Administration**.
3. Under the Administration menu, select **Apps**, then click **API**.
4. On the API page, click the **Add** button (top-right area of the page).
5. In the "Create Application" dialog:
   - Set **Application type** to **Client Credentials** (not Authorization Code — that type is for user-facing OAuth flows).
   - Give the application a descriptive name, for example `twingate-bridge`.
   - Under **Scopes**, enable **Monitoring** only (read-only access to device data). Do not enable **Management** — NinjaOne will reject token requests if the application is not explicitly granted that scope, returning a `400 invalid_scope` error.
6. Click **Save**.
7. NinjaOne will display the **Client ID** and **Client Secret** once. Copy both values immediately and store them in a password manager or secrets vault — the Client Secret cannot be retrieved again after you close this dialog.

> **Tip:** If you lose the Client Secret, you can regenerate it from the API page by editing the application. The Client ID stays the same.

## Configuration

Add the provider to your `config.yaml` under the `providers` list:

```yaml
providers:
  - type: ninjaone
    enabled: true
    region: app        # app (US), eu, ca, au, oc — see table below
    client_id: ${NINJAONE_CLIENT_ID}
    client_secret: ${NINJAONE_CLIENT_SECRET}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `type` | Yes | — | Must be `ninjaone` |
| `enabled` | No | `true` | Set to `false` to disable this provider without removing the config block |
| `region` | No | `app` | Regional endpoint prefix. `app` = US (legacy), `api` = US (current), `eu` = Europe, `ca` = Canada, `au` = Australia, `oc` = Oceania |
| `client_id` | Yes | — | OAuth2 client ID copied from the API application |
| `client_secret` | Yes | — | OAuth2 client secret copied from the API application |

The base URL is constructed as `https://{region}.ninjarmm.com`. US-hosted tenants may use either `app` (`https://app.ninjarmm.com`) or `api` (`https://api.ninjarmm.com`) — if `app` returns authentication errors, try `api`.

## Environment variables

Store your credentials in environment variables and reference them in `config.yaml` using `${VAR}` syntax. Never hard-code secrets in the config file.

| Variable               | Description                                    |
|------------------------|------------------------------------------------|
| `NINJAONE_CLIENT_ID`   | OAuth2 client ID from the API application      |
| `NINJAONE_CLIENT_SECRET` | OAuth2 client secret from the API application |

Example `.env` file (for local testing only — use your secrets manager in production):

```env
NINJAONE_CLIENT_ID=your-client-id-here
NINJAONE_CLIENT_SECRET=your-client-secret-here
```

## Compliance logic

The bridge evaluates two fields returned by the NinjaOne device detail API to decide whether a device is compliant. Both conditions must pass:

### 1. Antivirus protection

The `antivirus.threatStatus` field must be `PROTECTED` or absent. Any other value (for example `AT_RISK`, `INFECTED`, or `UNKNOWN`) is treated as non-compliant. If the field is absent entirely — which happens on devices where NinjaOne has no antivirus integration — the bridge gives the benefit of the doubt and treats the device as passing this check.

### 2. Patch status

The `patches.patchStatus` field must be `OK` or absent. If there are outstanding patches, NinjaOne sets this to a non-OK value and the device is treated as non-compliant.

A device must pass **both** checks to be considered compliant. If either field is non-OK, the device is skipped and will not be trusted in Twingate by this provider.

## Notes

- **Platform coverage:** Only Windows and macOS agents report hardware serial numbers via the NinjaOne API. Linux agents, network devices, and mobile devices either do not report a serial number or report one that cannot be reliably matched. Devices without a serial number are silently skipped — they will not cause errors, but they also cannot be matched to Twingate devices.

- **Serial number fields:** NinjaOne exposes serial numbers across three fields within the `system` object: `serialNumber`, `biosSerialNumber`, and `assetSerialNumber`. The bridge tries each in order and uses the first non-empty value. This handles devices (particularly VMs and some OEM hardware) where `serialNumber` is blank but the BIOS or asset serial is populated.

- **Pagination:** NinjaOne uses cursor-based pagination. The `after` query parameter is used to fetch the next page of results. The bridge automatically follows all cursors and exhausts every page before returning results — you will never get a partial device list.

- **Rate limiting:** NinjaOne enforces a limit of approximately 10 requests per second per organisation. If the bridge exceeds this, NinjaOne returns HTTP 429. The bridge's built-in retry helper detects 429 responses and applies exponential back-off automatically. For organisations with very large device counts (tens of thousands of devices), a single sync cycle may take a few minutes due to this limit.

- **Token caching:** The OAuth2 access token is cached in memory and reused until it nears expiry. The bridge requests a new token automatically before the old one expires, so you will not see authentication errors mid-cycle.

- **Single organisation:** Each config entry is scoped to the organisation associated with the Client Credentials application. If you manage multiple NinjaOne organisations, add a separate provider entry for each with its own credentials.
