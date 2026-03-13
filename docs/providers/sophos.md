# Sophos provider setup

Sophos Central is a cloud-based endpoint security and management platform that provides antivirus, EDR, firewall, and device health telemetry for Windows, macOS, and Linux machines. The bridge queries Sophos Central's endpoint API to determine whether each managed device is in a healthy state before trusting it in Twingate.

## Getting an account

Sophos offers a **30-day free trial** of Sophos Central with no credit card required.

1. Go to [https://www.sophos.com/en-us/free-trial](https://www.sophos.com/en-us/free-trial).
2. Choose **Sophos Central** from the list of trial options.
3. Fill in the registration form. Sophos will email you a link to activate your Sophos Central account.
4. Install the Sophos agent on at least one test machine so you have devices to query.

## Generating API credentials

Sophos Central uses **OAuth2 Client Credentials** (also called a Service Principal) for API access. You create a named credential in the Sophos Central console and are given a Client ID and Client Secret.

1. Log in to [Sophos Central](https://central.sophos.com/).
2. In the left-hand navigation, scroll down to the **Global Settings** section and click it to expand the menu.
3. Click **API Credentials Management**. If you do not see this option, ensure your account has Super Admin or Admin rights — read-only roles cannot create API credentials.
4. Click the **Add Credential** button (top-right of the credentials list).
5. In the "Add Credential" panel:
   - Enter a name such as `twingate-bridge`.
   - Under **Role**, select a role that includes read access to endpoints. The built-in **Service Principal ReadOnly** role is sufficient — it grants read access to endpoint data without any write permissions.
6. Click **Add**. Sophos Central immediately displays the **Client ID** and **Client Secret**.
7. Copy both values now. The Client Secret is shown only once. If you lose it, you must delete the credential and create a new one.

> **Note:** The credential is scoped to your own organisation. If your Sophos Central account is a Partner or Enterprise account that manages sub-tenants, the bridge will query only the top-level organisation associated with the credential — not the sub-tenants.

## Configuration

Add the provider to your `config.yaml` under the `providers` list. You do not need to specify a base URL — the bridge discovers the correct regional data-host automatically using the `/whoami/v1` endpoint.

```yaml
providers:
  - type: sophos
    enabled: true
    client_id: ${SOPHOS_CLIENT_ID}
    client_secret: ${SOPHOS_CLIENT_SECRET}
```

### Fields

| Field           | Required | Default | Description                                                  |
|-----------------|----------|---------|--------------------------------------------------------------|
| `type`          | Yes      | —       | Must be `sophos`                                             |
| `enabled`       | No       | `true`  | Set to `false` to disable without removing the block         |
| `client_id`     | Yes      | —       | OAuth2 client ID from API Credentials Management             |
| `client_secret` | Yes      | —       | OAuth2 client secret from API Credentials Management         |

## Environment variables

Store your credentials in environment variables and reference them in `config.yaml` using `${VAR}` syntax. Never hard-code secrets in the config file.

| Variable               | Description              |
|------------------------|--------------------------|
| `SOPHOS_CLIENT_ID`     | Sophos API client ID     |
| `SOPHOS_CLIENT_SECRET` | Sophos API client secret |

Example `.env` file (for local testing only — use your secrets manager in production):

```env
SOPHOS_CLIENT_ID=your-client-id-here
SOPHOS_CLIENT_SECRET=your-client-secret-here
```

## Compliance logic

### Overall health

A device is considered compliant when the `health.overall` field equals `"good"`. Sophos Central aggregates several health signals — antivirus status, threat detection, tamper protection, and update status — into this single field. If any of those signals are degraded, `health.overall` will be `"suspicious"` or `"bad"`, and the bridge will treat the device as non-compliant.

### Online status

A device is considered online (reachable) when `health.services.status` equals `"good"`. This indicates the Sophos agent is running and communicating with Sophos Central. Devices that are offline are still evaluated for compliance based on their last-known health state.

In practice, most deployments only care about the overall health field. The online status is surfaced in logs for observability but does not block a trust decision on its own.

## Notes

- **Two-step authentication:** Sophos uses a two-step auth flow. The bridge first obtains an OAuth2 bearer token from `id.sophos.com` using your Client ID and Secret. It then calls `https://api.central.sophos.com/whoami/v1` with that token to discover the tenant ID and the correct regional API host (for example `https://api-eu01.central.sophos.com`). All subsequent API calls are made against that regional host. This is handled automatically — you do not need to configure a region or base URL.

- **Partner and Enterprise accounts:** If your Sophos Central account manages multiple sub-tenants (a Partner or MSP setup), the bridge queries only the organisation that the API credential belongs to. Sub-tenant data is not queried. Each sub-tenant would require its own credential and a separate config entry.

- **Serial number resolution:** Sophos devices may report serial numbers in different fields depending on how the agent was installed and what platform the device runs. The bridge checks the following fields in order and uses the first non-empty value: `serialNumber`, then `os.serialNumber`, then `metadata.computerSerial`. If none of these fields contain a value, the device is skipped.

- **Pagination:** Sophos uses cursor-based pagination with `pageFromKey` and `nextKey` fields. The bridge automatically fetches all pages until `nextKey` is absent, ensuring no devices are missed.

- **Token caching:** The OAuth2 bearer token and the `/whoami/v1` tenant discovery response are both cached in memory for the lifetime of the token. The bridge re-authenticates automatically when the token nears expiry.
