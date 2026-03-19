# ManageEngine Endpoint Central provider setup

ManageEngine Endpoint Central (formerly Desktop Central) is an on-premises and cloud-hosted unified endpoint management platform that manages Windows, macOS, Linux, iOS, and Android devices. It handles software deployment, patch management, and device inventory from a single console.

This provider is supported in two variants: **cloud** and **on-premises**. They use different authentication methods and must be configured separately. Cloud is the recommended deployment and is covered first.

---

## Getting an account

ManageEngine offers a **30-day free trial** for both variants — no credit card required.

- **Cloud:** Go to [https://www.manageengine.com/products/desktop-central/](https://www.manageengine.com/products/desktop-central/) and click **Cloud** or **Sign Up for Cloud**. ManageEngine will provision a hosted instance for you. Your URL will be based on your region (e.g. `https://endpointcentral.manageengine.ca` for Canada).
- **On-premises:** Download and run the installer on a Windows Server or Linux machine. The trial activates automatically on first launch.

---

## Cloud setup

### How cloud authentication works

ManageEngine Endpoint Central Cloud is built on Zoho's infrastructure and uses Zoho OAuth2. There are three pieces involved:

| Credential | What it is | Lifetime |
| --- | --- | --- |
| **Client ID** | Permanent identifier for your OAuth app | Never expires |
| **Client Secret** | Permanent secret paired with the Client ID | Never expires |
| **Refresh Token** | Long-lived token the bridge uses to get API access | Permanent until manually revoked |

The **authorization code** you generate during setup is only a temporary stepping stone — you use it once, immediately exchange it for a refresh token, and never need it again. The bridge then runs entirely on the refresh token + client credentials with no further interaction with the Zoho console.

> **In short:** You visit the Zoho API Console once to set up. After that, the bridge handles everything automatically.

### Step 1 — Create a Self Client in the Zoho API Console

The Zoho API Console is where you manage OAuth apps. For a background service like this bridge, you use a **Self Client** — Zoho's OAuth type designed for non-interactive server applications that have no redirect URL.

1. Go to [https://api-console.zoho.com/](https://api-console.zoho.com/) and sign in with the same account you use for ManageEngine Endpoint Central Cloud. If your account is on a regional data centre, you may be redirected to a regional console (e.g. `api-console.zohoone.ca` for Canada) — that is normal.
2. Click **Add Client** and choose **Self Client**. Each Zoho account can only have one Self Client — if one already exists (shown as greyed out), skip this step and use the existing one.
3. Your **Client ID** and **Client Secret** are displayed on the Self Client page. Copy both and store them securely — these are permanent credentials you will put in your config.

### Step 2 — Generate a one-time authorization code

This step produces a short-lived code you will exchange for a permanent refresh token in the next step. You only do this once.

1. In the Self Client view, click the **Generate Code** tab.
2. In the **Scope** field, enter the following (comma-separated, no spaces):

   ```text
   DesktopCentralCloud.Common.READ,DesktopCentralCloud.SOM.READ,DesktopCentralCloud.Inventory.READ
   ```

3. Set the **Time Duration** to **10 minutes** (the maximum available). This is how long you have to complete the exchange in Step 3 — the code is single-use and expires after this window.
4. Optionally add a description (e.g. `twingate-bridge`).
5. Click **Create**. Copy the authorization code immediately.

> **What these scopes do:** `Common.READ` — base access required by the API. `SOM.READ` — read access to the computer list (devices and managed status). `Inventory.READ` — read access to hardware inventory.

### Step 3 — Exchange the code for a refresh token

Run the bridge's helper script to exchange the authorization code for a permanent refresh token. You must complete this within the 10-minute window from Step 2.

**For US accounts:**

```bash
python scripts/test-manageengine-client.py exchange \
  --client-id YOUR_CLIENT_ID \
  --client-secret YOUR_CLIENT_SECRET \
  --auth-code THE_CODE_FROM_STEP_2
```

**For non-US accounts**, add `--token-url` for your region (see the [region reference](#cloud-regions) at the bottom of this page):

```bash
python scripts/test-manageengine-client.py exchange \
  --client-id YOUR_CLIENT_ID \
  --client-secret YOUR_CLIENT_SECRET \
  --auth-code THE_CODE_FROM_STEP_2 \
  --token-url https://accounts.zohocloud.ca/oauth/v2/token
```

The script prints a `refresh_token` in the response. **Copy it — this is what you put in your config.** The authorization code is now consumed; you do not need it again.

> **What happens if I lose the refresh token or it gets revoked?** Go back to Step 2, generate a new authorization code, and run the exchange again. The 10-minute window only applies to the code; your client ID and secret remain valid.
>
> **What happens after 60 days?** Zoho refresh tokens expire if unused for 60 days. Because the bridge refreshes its access token on every sync cycle, the refresh token stays active as long as the bridge is running. If you shut down the bridge for more than 60 days, repeat Steps 2 and 3 to get a new refresh token.

### Step 4 — Configure the bridge

Add the ManageEngine provider to your `config.yaml`. Use environment variable references for all credentials.

```yaml
providers:
  - type: manageengine
    enabled: true
    variant: cloud
    base_url: ${ME_BASE_URL}
    oauth_client_id: ${ME_OAUTH_CLIENT_ID}
    oauth_client_secret: ${ME_OAUTH_CLIENT_SECRET}
    oauth_refresh_token: ${ME_OAUTH_REFRESH_TOKEN}
    oauth_token_url: ${ME_OAUTH_TOKEN_URL}  # omit for US accounts
    compliance:
      require_installed: true   # default — agent must be installed
      require_live: false       # set true to also require the machine is online
```

The `compliance` block is optional. If omitted, `require_installed: true` and `require_live: false` are used automatically.

Add the corresponding values to your `.env` file:

```env
ME_BASE_URL=https://endpointcentral.manageengine.ca
ME_OAUTH_CLIENT_ID=your-client-id-here
ME_OAUTH_CLIENT_SECRET=your-client-secret-here
ME_OAUTH_REFRESH_TOKEN=your-refresh-token-here
ME_OAUTH_TOKEN_URL=https://accounts.zohocloud.ca/oauth/v2/token
```

### Cloud configuration reference

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `type` | Yes | — | Must be `manageengine` |
| `enabled` | Yes | — | Set to `true` to activate this provider |
| `variant` | Yes | — | Must be `cloud` |
| `base_url` | No | `https://endpointcentral.manageengine.com` | Your Endpoint Central Cloud URL. Change the domain suffix for your region (e.g. `.ca`, `.eu`) |
| `oauth_client_id` | Yes | — | Client ID from the Zoho API Console Self Client |
| `oauth_client_secret` | Yes | — | Client Secret from the Zoho API Console Self Client |
| `oauth_refresh_token` | Yes | — | Refresh token obtained from the exchange step |
| `oauth_token_url` | No | `https://accounts.zoho.com/oauth/v2/token` | Zoho token endpoint. Required for non-US regions |
| `compliance.require_installed` | No | `true` | Agent must be installed (`installation_status == 22`) |
| `compliance.require_live` | No | `false` | Machine must be currently reachable (`computer_live_status == 1`) |

### Cloud regions

Set `base_url` and `oauth_token_url` based on where your ManageEngine account is hosted:

| Region | `base_url` | `oauth_token_url` |
| --- | --- | --- |
| United States | `https://endpointcentral.manageengine.com` | `https://accounts.zoho.com/oauth/v2/token` |
| Europe | `https://endpointcentral.manageengine.eu` | `https://accounts.zoho.eu/oauth/v2/token` |
| India | `https://endpointcentral.manageengine.in` | `https://accounts.zoho.in/oauth/v2/token` |
| Australia | `https://endpointcentral.manageengine.com.au` | `https://accounts.zoho.com.au/oauth/v2/token` |
| Japan | `https://endpointcentral.manageengine.jp` | `https://accounts.zoho.jp/oauth/v2/token` |
| Canada | `https://endpointcentral.manageengine.ca` | `https://accounts.zohocloud.ca/oauth/v2/token` |
| United Kingdom | `https://endpointcentral.manageengine.uk` | `https://accounts.zoho.uk/oauth/v2/token` |

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
   - **Role:** Assign a role with read access to computer inventory. The built-in **SDAdmin** role works; for tighter permissions, create a custom role with the "View Computers" permission only.
   - Set a strong password and save.
5. Still in the **Admin** panel, click **API Key Management** (in some versions this is under **General Settings → API Key Management**).
6. Find the technician you just created and click **Generate** to create an API key.
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

```env
ME_API_TOKEN=your-api-token-here
```

### On-premises configuration reference

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `type` | Yes | — | Must be `manageengine` |
| `enabled` | Yes | — | Set to `true` to activate this provider |
| `variant` | Yes | — | Must be `onprem` |
| `base_url` | Yes | — | Full URL to your Endpoint Central server, including port |
| `api_token` | Yes | — | API token generated from Admin → API Key Management |

---

## Compliance logic

**On-premises:** A device is compliant when its `managed_status` is `ACTIVE` or `MANAGED`.

**Cloud:** Compliance is determined by the checks enabled in the `compliance` config block. Both checks are ANDed — a device must pass every enabled check.

| Check | Config key | Default | Pass condition |
| --- | --- | --- | --- |
| Agent installed | `require_installed` | `true` | `installation_status == 22` |
| Machine online | `require_live` | `false` | `computer_live_status == 1` |

The default behaviour (omitting the `compliance` block entirely) only checks that the agent is installed. Enable `require_live: true` if you want the bridge to also reject devices whose agent has stopped reporting in — for example, machines that are powered off or have had their services stopped.

> **Latency note:** `computer_live_status` is updated by ManageEngine on its own schedule — in testing, status transitions (online → offline and offline → online) took approximately 10–12 minutes to be reflected in the API. Devices will continue to pass or fail the `require_live` check based on the last known status until ManageEngine updates it.

---

## Notes

- **Cloud uses a single API call per sync.** The bridge calls `GET /api/1.4/som/computers`, which returns both agent status and serial number (`managedcomputerextn.service_tag`) in one paginated response. The on-prem inventory endpoint (`/dcapi/inventory/complist`) is not used for cloud.
- **On-prem uses two API calls per sync.** The bridge calls `GET /api/1.4/desktop/computers` for agent status and `GET /dcapi/inventory/complist` for serial numbers, then joins the results by computer name (case-insensitive). Devices with no matching serial number are skipped.
- **On-prem serial number resolution:** The bridge checks three fields in order: `sysinfo.SERIALNUMBER`, `sysinfo.serial_number`, `sysinfo.BIOS_SERIALNUMBER`. Devices with no serial in any field are skipped.
- **Timestamps:** Last-contact timestamps from the Endpoint Central API are in epoch milliseconds. The bridge converts them correctly.
- **On-prem TLS:** If your on-premises Endpoint Central server uses a self-signed certificate, the bridge will reject the connection by default. Either install a valid certificate or mount your internal CA into the container.
- **Pagination:** All API endpoints are paginated. The bridge exhausts all pages on every sync to ensure no devices are missed.
- **`computer_live_status` update latency:** ManageEngine does not update `computer_live_status` in real time. In testing, status transitions took approximately 10–12 minutes to be reflected in the API after a machine went offline or came back online. This is a ManageEngine polling interval, not a bridge limitation.
