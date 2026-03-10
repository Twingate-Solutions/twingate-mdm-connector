# Windows VM Setup

This document covers spinning up a Windows test VM, enrolling it in a provider, and confirming that the serial number matches between the provider UI and the bridge's matching logic.

The bridge matches devices by **serial number**, normalised to `.strip().upper()` before comparison. A mismatch between what the provider reports and what Twingate reports will result in `"action": "no_match"` — the device will never be trusted.

## VM requirements

| Requirement | Notes |
|---|---|
| OS | Windows 10 or Windows 11 **Pro** or **Enterprise** — Home editions may lack Group Policy features required by some MDM agents |
| CPU | 2 vCPU minimum |
| RAM | 4 GB minimum |
| Disk | 60 GB minimum |
| Network | Internet access required — agents need to phone home |

**Supported hypervisors:** Hyper-V, VMware Workstation/Fusion/ESXi, VirtualBox, UTM (Apple Silicon), Parallels, Proxmox VE.

Note: VMs typically receive a synthetic (software-generated) serial number. This is fine for testing — the bridge does not care whether it is a physical or virtual serial. What matters is that the serial shown in the provider UI matches `(Get-WmiObject Win32_BIOS).SerialNumber` on the VM.

## Step 1: Verify the serial number

Run this in PowerShell on the VM (no admin rights required):

```powershell
(Get-WmiObject Win32_BIOS).SerialNumber
```

Or equivalently:

```powershell
wmic bios get serialnumber
```

**Write this value down.** This is what the bridge will normalise (strip whitespace, convert to uppercase) and use for matching. You need to confirm this exact value appears in the provider UI.

If the output is blank, `"None"`, `"To Be Filled By O.E.M."`, or another placeholder, the hypervisor has not assigned a real serial. Check your hypervisor settings:

- **Hyper-V:** Edit the VM settings in PowerShell: `Set-VMBios -VMName "TestVM" -BiosSerialNumber "TEST-SERIAL-001"`
- **VMware:** Add `serialNumber = "TEST-SERIAL-001"` to the `.vmx` file (VM must be powered off)
- **VirtualBox:** `VBoxManage setextradata "TestVM" "VBoxInternal/Devices/pcbios/0/Config/DmiSystemSerial" "TEST-SERIAL-001"`
- **Proxmox VE:** Add `smbios1: serial=TEST-SERIAL-001` to the VM's configuration file at `/etc/pve/qemu-server/{vmid}.conf` (VM must be stopped), or via the web UI under Hardware > Add > SMBios Settings

Use `scripts/dump-serial.ps1` (from the repo) for a formatted output including a warning if the serial is a known placeholder.

## Step 2: Install provider agents

Install the agent for each provider you want to test. You only need to install the agents for providers you have enabled in `config.yaml`.

### NinjaOne

1. In the NinjaOne console, go to **Devices > Add Device**.
2. Select **Windows** and follow the wizard to download the installer `.exe`.
3. Run the installer on the VM (requires admin rights).
4. The device appears in the console within ~5 minutes.

### Sophos Central

1. In Sophos Central Admin, go to **Download Software > Endpoint Protection**.
2. Download the **Windows installer**.
3. Run the installer on the VM.
4. The device appears under **Computers** within ~5 minutes.

### ManageEngine Endpoint Central

**Cloud:** Use the Agent Deploy wizard in the ManageEngine console.

**On-prem:**
1. Open the ManageEngine Desktop Central web console.
2. Go to **Deployment > Agent Installation > Download Agent**.
3. Copy the installer to the VM and run it.

### Automox

1. In the Automox console, go to **Devices > Add Device**.
2. Select **Direct Install** and download the Windows installer.
3. Run the `.exe` on the VM.
4. The device appears in the console within a few minutes.

### JumpCloud

This is the simplest agent install — no downloaded file required.

1. In the JumpCloud Admin Console, go to **Devices > Add Device**.
2. Select **Windows** and copy the one-line **PowerShell install command** shown.
3. Open PowerShell **as Administrator** on the VM.
4. Paste and run the command.
5. The device appears in the console almost immediately.

### FleetDM (osquery / fleetd)

1. In Fleet, go to **Hosts > Add Hosts**.
2. Download the **fleetd** installer package for Windows.
3. Run the installer on the VM.
4. The device appears under **Hosts** in the Fleet UI within a few minutes.

For more detail, see the Fleet docs: https://fleetdm.com/docs/using-fleet/adding-hosts

### Mosyle

Mosyle manages **Apple devices only**. Skip this section for a Windows VM. Use a Mac for Mosyle testing.

### Datto RMM

1. In the Datto RMM console, go to your site and select **Download Agent**.
2. Download the Windows agent installer.
3. Run the installer on the VM.
4. The device appears under **Devices** in the console.

## Step 3: Confirm the serial in the provider UI

After the agent checks in, find the device in the provider console and confirm its serial number matches your PowerShell output. Comparison is **case-insensitive** — the bridge normalises both sides to uppercase.

| Provider | Where to find the serial |
|---|---|
| NinjaOne | Devices > click device > **System** tab > Serial Number |
| Sophos | Computers > click device > **Summary** > Serial number |
| ManageEngine | Inventory > Computers > click device > **Hardware** > BIOS Serial |
| Automox | Devices > click device > **Details** > Serial Number |
| JumpCloud | Devices > Systems > click device > **Details** > Serial Number |
| FleetDM | Hosts > click device > **Hardware** > Serial number |
| Datto | Devices > click device > **Summary** > Serial Number |

If the provider shows a different serial than PowerShell, the bridge will log `"action": "no_match"` for this device. Resolve the discrepancy before proceeding.

## Step 4: Install the Twingate client

1. Download the Twingate client for Windows from https://www.twingate.com/download
2. Install it on the VM.
3. Sign in with a user account that belongs to the Twingate test network.
4. The device should appear in the Twingate Admin Console under **Devices** with **Trust Status: Untrusted**.

**If the device doesn't appear in Twingate:** The device must connect to at least one Twingate resource to appear. In the Admin Console, create a dummy resource (e.g. a private IP) and assign it to a group that includes the test user. Then, on the VM, open the Twingate client and attempt to connect to the resource.

## Testing multiple providers simultaneously

If you want to test multiple providers in a single run, install all relevant agents on the same VM. The bridge queries all enabled providers in parallel and applies the `trust.mode` logic (`any` or `all`) across them.

The serial number must match across all providers you want the bridge to cross-reference. Since they all pull the BIOS serial via WMI (or equivalent), they should all report the same value — but verify in each provider's UI to be sure.
