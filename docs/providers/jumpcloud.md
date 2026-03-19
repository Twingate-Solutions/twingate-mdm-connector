# JumpCloud provider setup

JumpCloud is a cloud-based directory and MDM platform that manages identities and devices (macOS, Windows, Linux) from a single console. It is commonly used by small-to-medium businesses to replace on-premises Active Directory.

---

## Getting an account

JumpCloud offers a **free forever** tier that covers up to 10 devices and 10 users — no credit card required.

1. Go to [https://jumpcloud.com/](https://jumpcloud.com/) and click **Get Started Free**.
2. Fill in your name, work email, and company name, then follow the email verification flow.
3. Once logged in, install the JumpCloud agent on at least one device to verify your setup before configuring this connector.

---

## Generating API credentials

JumpCloud uses a single API key per admin account. There is no scope granularity — the key has the same permissions as the user it belongs to, so using a dedicated read-only service account is strongly recommended for production.

### Option A — Personal API key (quickest for testing)

1. Log in to the JumpCloud Admin Console at [https://console.jumpcloud.com/](https://console.jumpcloud.com/).
2. Click your **avatar / initials** in the top-right corner.
3. Select **My API Key** from the dropdown menu.
4. Click **Show API Key** and copy the value.

### Option B — Dedicated service account (recommended for production)

1. In the JumpCloud Admin Console, go to **User Management → Users**.
2. Click **+ (Add User)** and create a new admin user (e.g. `svc-twingate-connector@yourdomain.com`). Set a strong password and mark the account as an Administrator.
3. Log in to JumpCloud as that service account in a separate browser session (or incognito window).
4. Follow Option A steps 2–4 above to retrieve the API key for that account.
5. Keep the service account credentials in a password manager; you only need the API key for this connector.

> **Security note:** There is currently no way to create a read-only API key in JumpCloud. The API key has full admin privileges. Treat it as a highly sensitive secret and rotate it if it is ever exposed.

---

## Configuration

Add the following block to your `config.yaml`:

```yaml
providers:
  - type: jumpcloud
    enabled: true
    api_key: ${JUMPCLOUD_API_KEY}
```

### Fields

| Field     | Required | Default | Description                                   |
|-----------|----------|---------|-----------------------------------------------|
| `type`    | Yes      | —       | Must be `jumpcloud`                           |
| `enabled` | Yes      | —       | Set to `true` to activate this provider       |
| `api_key` | Yes      | —       | JumpCloud API key (use an env-var reference)  |

---

## Environment variables

Store your credentials in environment variables and reference them in `config.yaml` using `${VAR}` syntax. Never hard-code secrets in the config file.

| Variable             | Description                                          |
|----------------------|------------------------------------------------------|
| `JUMPCLOUD_API_KEY`  | JumpCloud API key copied from the Admin Console      |

Example `.env` file (for local testing only — use your secrets manager in production):

```env
JUMPCLOUD_API_KEY=abc123def456...
```

---

## Compliance logic

A device is marked **compliant** when both of the following are true:

- **`active` is `true`** — the JumpCloud agent has checked in and the device is enrolled. Devices that have been manually deactivated, or that have never completed enrollment, have `active: false` and are treated as non-compliant.
- **Full-disk encryption (`fde`) is not explicitly disabled** — if the `fde` object is absent from the API response, the device is treated as compliant (FDE data is only present when the agent actively reports it). If `fde` is present, `fde.active` must not be `false`.

A device is considered **online** when `active` is `true`.

In plain English: the device must be enrolled and active, and if JumpCloud has disk-encryption data for it, encryption must not be off.

---

## Notes

- **Scope:** Only desktop/laptop systems (macOS, Windows, Linux) are returned by the `/systems` endpoint. Mobile devices enrolled via JumpCloud MDM (iOS/Android) do **not** appear here and will not be matched against Twingate devices.
- **Pagination:** The bridge uses `skip` / `limit=100` pagination and reads `totalCount` from the response headers to know when all pages have been fetched. Every page is always exhausted before the sync proceeds.
- **Authentication:** The API key is sent in the `x-api-key` request header on every call. There is no token expiry to manage.
- **Multi-tenant:** If your organisation has multiple JumpCloud tenants (e.g. per-subsidiary), configure a separate provider block for each, using the API key that belongs to the target tenant.
- **Rate limits:** JumpCloud's API is rate-limited. The bridge uses a page size of 100 (the maximum) to minimise the number of requests per sync.
