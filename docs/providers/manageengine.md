# ManageEngine Endpoint Central provider setup

ManageEngine Endpoint Central (formerly Desktop Central) is supported in two variants: **on-premises** and **cloud**.

## On-premises setup

### Prerequisites

1. Log in to Endpoint Central → **Admin → Technician** → create or select a technician.
2. Go to **Admin → API Key Management** and generate an API key.
3. Note your Endpoint Central server URL (e.g. `https://me.corp.local:8383`).

### Configuration

```yaml
providers:
  - type: manageengine
    enabled: true
    variant: onprem
    base_url: https://me.corp.local:8383
    api_token: ${ME_API_TOKEN}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `variant` | Yes | — | Must be `onprem` |
| `base_url` | Yes | — | Endpoint Central server URL |
| `api_token` | Yes | — | API token from Admin → API Key Management |

### Environment variables

| Variable | Description |
|----------|-------------|
| `ME_API_TOKEN` | ManageEngine API token |

---

## Cloud setup

### Prerequisites

1. Log in to ManageEngine Endpoint Central Cloud.
2. Go to **Admin → API → OAuth Application** and create an application.
3. Set the grant type to **Refresh Token** and generate tokens via the Zoho OAuth playground.
4. Copy the **Client ID**, **Client Secret**, and **Refresh Token**.

### Configuration

```yaml
providers:
  - type: manageengine
    enabled: true
    variant: cloud
    oauth_client_id: ${ME_OAUTH_CLIENT_ID}
    oauth_client_secret: ${ME_OAUTH_CLIENT_SECRET}
    oauth_refresh_token: ${ME_OAUTH_REFRESH_TOKEN}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `variant` | Yes | — | Must be `cloud` |
| `oauth_client_id` | Yes | — | Zoho OAuth2 client ID |
| `oauth_client_secret` | Yes | — | Zoho OAuth2 client secret |
| `oauth_refresh_token` | Yes | — | Zoho OAuth2 refresh token (long-lived) |

### Environment variables

| Variable | Description |
|----------|-------------|
| `ME_OAUTH_CLIENT_ID` | Zoho OAuth2 client ID |
| `ME_OAUTH_CLIENT_SECRET` | Zoho OAuth2 client secret |
| `ME_OAUTH_REFRESH_TOKEN` | Zoho OAuth2 refresh token |

---

## Compliance logic

A device is compliant when `managed_status` is `ACTIVE` or `MANAGED`.

## Notes

- The bridge makes **two API calls** per sync: one to `/api/1.4/desktop/computers` (agent status) and one to `/dcapi/inventory/complist` (serial numbers). The results are joined by computer name (case-insensitive).
- Devices in the computers list that have no matching inventory entry are skipped.
- Serial number is read from `sysinfo.SERIALNUMBER`, then `sysinfo.serial_number`, then `sysinfo.BIOS_SERIALNUMBER`.
- `last_contact_time` is in epoch milliseconds.
