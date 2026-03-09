# Sophos provider setup

## Prerequisites

You need a Sophos Central API credential with **Service Principal** access.

1. Log in to Sophos Central → **Global Settings → API Credentials Management**.
2. Click **Add Credential**.
3. Grant the **Endpoint** read scope.
4. Copy the **Client ID** and **Client Secret**.

## Configuration

```yaml
providers:
  - type: sophos
    enabled: true
    client_id: ${SOPHOS_CLIENT_ID}
    client_secret: ${SOPHOS_CLIENT_SECRET}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `client_id` | Yes | — | Sophos API client ID |
| `client_secret` | Yes | — | Sophos API client secret |

## Environment variables

| Variable | Description |
|----------|-------------|
| `SOPHOS_CLIENT_ID` | Sophos API client ID |
| `SOPHOS_CLIENT_SECRET` | Sophos API client secret |

## Compliance logic

A device is compliant when `health.overall` equals `"good"`.

A device is online when `health.services.status` equals `"good"`.

## Notes

- Authentication is a two-step process: the bridge first obtains an OAuth2 token from `id.sophos.com`, then calls `/whoami/v1` to discover the tenant ID and regional data-host before querying endpoints.
- For **Partner** or **Enterprise** accounts that manage multiple tenants, the bridge queries the tenant associated with the credential's own organisation only.
- Pagination uses `pageFromKey` / `nextKey` cursors; all pages are fetched automatically.
- Serial number is read from `serialNumber`, then `os.serialNumber`, then `metadata.computerSerial` — the first non-empty value wins.
