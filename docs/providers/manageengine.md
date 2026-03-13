# ManageEngine Endpoint Central provider setup

ManageEngine Endpoint Central (formerly Desktop Central) is an on-premises and cloud-hosted unified endpoint management platform that manages Windows, macOS, Linux, iOS, and Android devices. It handles software deployment, patch management, and device inventory from a single console.

This provider is supported in two variants: **on-premises** and **cloud**. They use different authentication methods and must be configured separately.

---

## Getting an account

ManageEngine offers a **30-day free trial** for both the on-premises and cloud versions — no credit card required.

- **On-premises:** Download the installer from [https://www.manageengine.com/products/desktop-central/](https://www.manageengine.com/products/desktop-central/). Run the installer on a Windows Server or Linux machine. The trial activates automatically on first launch.
- **Cloud:** Go to the same URL and click **Cloud** or **Sign Up for Cloud**. Fill in your details and ManageEngine will provision a hosted instance at `yourdomain.endpointcentral.com`.

---

## On-premises setup

### On-premises API credentials

The on-premises version uses a static API token tied to a Technician account. Create a dedicated Technician account for the bridge so you can revoke access independently.

1. Log in to your Endpoint Central server as an administrator.
2. In the top navigation bar, click **Admin**.
3. In the Admin panel, click **Technician** under the User Management section.
4. Click **Add Technician** and fill in the details:
   - **Name:** e.g. `Twingate Bridge`
   - **Login Name:** e.g. `twingate-bridge`
   - **Role:** Assign a role that has read access to computer inventory. The built-in **SDAdmin** role works; for tighter permissions, create a custom role with the "View Computers" permission only.
   - Set a strong password and save.
5. Still in the **Admin** panel, click **API Key Management** (in some versions this is under **General Settings → API Key Management**).
6. Find the technician you just created and click **Generate** to create an API key for that account.
7. Copy the API key — it will not be shown again.
8. Note your Endpoint Central server URL, including the port (the default is `8383` for HTTPS, e.g. `https://me.corp.local:8383`).

### On-premises configuration

```yaml
providers:
  - type: manageengine
    enabled: true
    variant: onprem
    base_url: https://me.corp.local:8383
    api_token: ${ME_API_TOKEN}
```

### On-premises fields

| Field       | Required | Default | Description                                                |
|-------------|----------|---------|------------------------------------------------------------|
| `type`      | Yes      | —       | Must be `manageengine`                                     |
| `enabled`   | Yes      | —       | Set to `true` to activate this provider                    |
| `variant`   | Yes      | —       | Must be `onprem`                                           |
| `base_url`  | Yes      | —       | Full URL to your Endpoint Central server, including port   |
| `api_token` | Yes      | —       | API token generated from Admin → API Key Management        |

### On-premises environment variables

| Variable       | Description                          |
|----------------|--------------------------------------|
| `ME_API_TOKEN` | ManageEngine on-prem API token       |

Example `.env` file:

```env
ME_API_TOKEN=your-api-token-here
```

---

## Cloud setup

### Cloud API credentials

The cloud version uses Zoho OAuth2 (ManageEngine's cloud platform is built on Zoho infrastructure). You will create an OAuth application and obtain a long-lived refresh token.

1. Log in to ManageEngine Endpoint Central Cloud.
2. In the top navigation bar, click **Admin**.
3. Under the Developer section (or **Integrations** in some versions), click **API → OAuth Application**.
4. Click **Add Application** and fill in:
   - **Application Name:** e.g. `Twingate Bridge`
   - **Grant Type:** Select **Refresh Token** (not Authorization Code — that requires browser interaction).
   - **Redirect URI:** You can enter `https://localhost` as a placeholder; it will not be used for a Refresh Token grant.
5. Click **Save**. The page will display your **Client ID** and **Client Secret** — copy both and store them securely.
6. To obtain the Refresh Token, use the **Zoho OAuth Playground**:
   - Go to [https://api-console.zoho.com/](https://api-console.zoho.com/) and log in with the same Zoho account used for ManageEngine.
   - Select your application from the list.
   - Under **Self Client**, enter the scope `SDPOnDemand.computers.READ` (or `ITAM.READ` depending on your version).
   - Click **Generate Code** to produce a short-lived authorization code.
   - Exchange the code for tokens by clicking **Generate Token**. Copy the **Refresh Token** from the response.

> **Refresh token lifetime:** Zoho refresh tokens are long-lived but can expire if unused for 60 days. The bridge automatically uses the refresh token to obtain a new access token on each sync, which keeps the refresh token active.

### Cloud configuration

```yaml
providers:
  - type: manageengine
    enabled: true
    variant: cloud
    oauth_client_id: ${ME_OAUTH_CLIENT_ID}
    oauth_client_secret: ${ME_OAUTH_CLIENT_SECRET}
    oauth_refresh_token: ${ME_OAUTH_REFRESH_TOKEN}
```

### Cloud fields

| Field                  | Required | Default | Description                                            |
|------------------------|----------|---------|--------------------------------------------------------|
| `type`                 | Yes      | —       | Must be `manageengine`                                 |
| `enabled`              | Yes      | —       | Set to `true` to activate this provider                |
| `variant`              | Yes      | —       | Must be `cloud`                                        |
| `oauth_client_id`      | Yes      | —       | Client ID from Admin → API → OAuth Application         |
| `oauth_client_secret`  | Yes      | —       | Client Secret from Admin → API → OAuth Application     |
| `oauth_refresh_token`  | Yes      | —       | Zoho OAuth2 refresh token (long-lived)                 |

### Cloud environment variables

| Variable                 | Description                             |
|--------------------------|-----------------------------------------|
| `ME_OAUTH_CLIENT_ID`     | Zoho OAuth2 client ID                   |
| `ME_OAUTH_CLIENT_SECRET` | Zoho OAuth2 client secret               |
| `ME_OAUTH_REFRESH_TOKEN` | Zoho OAuth2 refresh token               |

Example `.env` file:

```env
ME_OAUTH_CLIENT_ID=your-client-id-here
ME_OAUTH_CLIENT_SECRET=your-client-secret-here
ME_OAUTH_REFRESH_TOKEN=your-refresh-token-here
```

---

## Compliance logic

A device is marked **compliant** when its `managed_status` field is either `ACTIVE` or `MANAGED`.

In plain English: the device must be actively managed by Endpoint Central. Devices that have been retired, have a stale agent, or are in a pending-enrollment state will have a different `managed_status` value and will not be trusted.

---

## Notes

- **Two API calls per sync:** The bridge makes two separate API calls to build a complete picture of each device. The first call (`/api/1.4/desktop/computers`) returns agent status and `managed_status`. The second call (`/dcapi/inventory/complist`) returns hardware inventory including serial numbers. The two result sets are joined by computer name (case-insensitive). Devices that appear in the computers list but have no matching inventory entry are skipped.
- **Serial number resolution:** The bridge attempts to read the serial number from three fields in order: `sysinfo.SERIALNUMBER`, then `sysinfo.serial_number`, then `sysinfo.BIOS_SERIALNUMBER`. The first non-empty value found is used. Devices with no serial number in any of these fields are silently skipped.
- **Timestamps:** The `last_contact_time` field from the Endpoint Central API is in **epoch milliseconds** (not seconds). The bridge converts this correctly when calculating device last-seen times.
- **On-prem TLS:** If your on-premises Endpoint Central server uses a self-signed certificate, the bridge's Docker container will reject the connection by default. Either install a valid certificate on the server or mount your internal CA certificate into the container and configure it as trusted.
- **Cloud region:** ManageEngine Cloud is hosted on Zoho's infrastructure. The OAuth token endpoint is `https://accounts.zoho.com/oauth/v2/token`. If your ManageEngine account is on a non-US Zoho data centre (e.g. EU at `zoho.eu`, India at `zoho.in`), the token endpoint domain will differ — check your account's region in the Zoho admin console.
- **Pagination:** Both API endpoints are paginated. The bridge exhausts all pages on every sync to ensure no devices are missed.
