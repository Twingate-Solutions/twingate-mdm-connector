# Rippling provider setup

Rippling is an HR and IT platform that includes unified device management for laptops, phones, and tablets. It manages macOS, Windows, iOS, and Android devices and is the ninth provider supported by the bridge.

## Getting an account

Rippling does not offer a self-service free trial. Request a demo through the sales team at [https://www.rippling.com/](https://www.rippling.com/). Device management is available as part of the **IT Management** add-on in your Rippling plan.

## Generating API credentials

1. Log in to Rippling as an admin.
2. Navigate to **IT Management → API**, or **Settings → Developer → API Keys** (the exact path varies by Rippling version).
3. Create a new **OAuth2 application**.
4. Note the **Client ID** and **Client Secret** generated for the application.
5. Ensure the application is granted the **Device Management: Read** scope. Without this scope the bridge will not be able to retrieve device inventory.

> The client secret is sensitive. Store it only in environment variables — never in the config file directly.

## Configuration

```yaml
providers:
  - type: rippling
    enabled: true
    client_id: ${RIPPLING_CLIENT_ID}
    client_secret: ${RIPPLING_CLIENT_SECRET}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `client_id` | Yes | — | OAuth2 client ID from your Rippling OAuth2 application |
| `client_secret` | Yes | — | OAuth2 client secret from your Rippling OAuth2 application |

## Environment variables

| Variable | Description |
|----------|-------------|
| `RIPPLING_CLIENT_ID` | OAuth2 client ID |
| `RIPPLING_CLIENT_SECRET` | OAuth2 client secret |

## Compliance logic

A device is compliant when it is actively enrolled and managed in Rippling. The bridge evaluates the management status field returned by the device inventory endpoint — a device must indicate that it is under active management to be considered compliant.

## Notes

- Rippling manages **macOS, Windows, iOS, and Android** devices. Serial numbers for all of these platforms are eligible to be matched against Twingate.
- Serial numbers are reported in the `serialNumber` field of each device record.
- This provider uses the standard **OAuth2 client credentials flow**. Tokens are cached and refreshed automatically by the bridge.
- Device management in Rippling requires the **IT Management add-on** to be enabled in your Rippling subscription. If the add-on is not active, the device inventory endpoint will return no results or an error.
- The device inventory is retrieved from `GET /platform/api/devices`.
