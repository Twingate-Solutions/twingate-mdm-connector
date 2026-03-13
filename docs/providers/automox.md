# Automox provider setup

Automox is a cloud-native patch management and endpoint hardening platform that manages patching for Windows, macOS, and Linux devices from a single console. The bridge queries the Automox device API to check patch compliance before trusting a device in Twingate.

## Getting an account

Automox offers a **30-day free trial** with no credit card required.

1. Go to [https://www.automox.com/](https://www.automox.com/).
2. Click **Start Free Trial** on the homepage.
3. Complete the registration form. You will receive a confirmation email with a link to activate your account and set up your first organisation.
4. Install the Automox agent on at least one test device so you have endpoints to query.

## Generating API credentials

Automox uses a simple **API key** for authentication — there is no OAuth2 flow. The key is tied to your user account and grants access to all organisations your account belongs to.

1. Log in to the [Automox console](https://console.automox.com/).
2. Click your user avatar or name in the **top-right corner** of the console to open the account menu.
3. Select **Settings** from the dropdown.
4. In the Settings sidebar, click **API Keys**.
5. Click **Generate API Key**. Automox will display the new key immediately.
6. Copy the key and store it in a password manager or secrets vault. The key is shown in full only once — if you lose it, you will need to delete it and generate a new one.

> **Tip:** Give the key a descriptive label if the UI allows it, for example `twingate-bridge`, so you can identify it later when auditing API key usage.

## Configuration

Add the provider to your `config.yaml` under the `providers` list:

```yaml
providers:
  - type: automox
    enabled: true
    api_key: ${AUTOMOX_API_KEY}
```

### Fields

| Field     | Required | Default | Description                                          |
|-----------|----------|---------|------------------------------------------------------|
| `type`    | Yes      | —       | Must be `automox`                                    |
| `enabled` | No       | `true`  | Set to `false` to disable without removing the block |
| `api_key` | Yes      | —       | Automox API key from the Settings page               |

## Environment variables

Store your credentials in environment variables and reference them in `config.yaml` using `${VAR}` syntax. Never hard-code secrets in the config file.

| Variable          | Description       |
|-------------------|-------------------|
| `AUTOMOX_API_KEY` | Automox API key   |

Example `.env` file (for local testing only — use your secrets manager in production):

```env
AUTOMOX_API_KEY=your-api-key-here
```

## Compliance logic

### Patch compliance

The bridge evaluates two fields on each Automox device record. Both must be true for a device to be considered compliant:

- `is_compatible` must be `true`. Automox sets this flag to indicate that the device's operating system is supported and the agent is functioning correctly. A value of `false` typically means the OS version is no longer supported or the agent cannot operate on that device.
- `pending_patches` must be `0`. If Automox has identified patches that have not yet been applied, this count will be greater than zero and the device is treated as non-compliant.

If either condition is not met, the device is skipped and will not be trusted in Twingate by this provider.

### Online status

A device is considered online (reachable) when `status.agent_status` equals `"connected"`. This indicates the Automox agent is actively communicating with the Automox cloud. Offline devices are still evaluated for compliance using their last-known state, but the online status is surfaced in logs for observability.

## Notes

- **Organisation scope:** The Automox API key is associated with your user account and returns devices across all organisations your account has access to. If you only want to sync one organisation, you can use a dedicated account that belongs only to that organisation, or filter at the Twingate side using device attributes.

- **Multiple organisations:** If you manage several separate Automox organisations and need each treated independently, add a separate provider entry in `config.yaml` for each one, using an API key that belongs to a user scoped to that organisation.

- **Pagination:** Automox uses offset-based pagination. The bridge requests pages of 500 devices at a time using the `page` and `limit` query parameters, starting at page 0, and continues until a page returns fewer results than the limit. All pages are fetched automatically before matching begins.

- **Serial numbers:** Devices without a `serial_number` field in the Automox API response are silently skipped — they will not cause errors, but they cannot be matched to Twingate devices. This is most common for virtual machines or devices where the agent could not read the hardware serial number.

- **Patch policy timing:** Automox evaluates patch status on a schedule defined by your patch policies. A device may briefly show `pending_patches > 0` immediately after Automox discovers new patches and before a scheduled maintenance window runs. Consider this when interpreting trust decisions for devices that are technically in a patch window.
