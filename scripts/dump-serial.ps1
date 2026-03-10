<#
.SYNOPSIS
    Dumps the Windows BIOS serial number in the exact format used by twingate-device-trust-bridge for device matching.

.DESCRIPTION
    Retrieves the BIOS serial number via WMI and outputs it in normalised form (trimmed and uppercased),
    matching the normalisation applied by the bridge (Python's .strip().upper()).

    Also outputs OS and hostname information to help correlate the VM with provider records.

    Exits with code 0 on success, or 1 if the serial number is empty or a known placeholder value
    that indicates the hypervisor has not assigned a real serial.

.EXAMPLE
    .\dump-serial.ps1

    Run from the project root on the Windows test VM. No admin rights required.

.NOTES
    Compatible with Windows PowerShell 5.1 and PowerShell 7+.
    No external dependencies.
#>

#Requires -Version 5.1

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Serial values that indicate a VM has no real serial assigned
$KnownPlaceholders = @(
    '',
    'None',
    'To Be Filled By O.E.M.',
    'To be filled by O.E.M.',
    'System Serial Number',
    'Default string',
    '0',
    '00000000',
    'N/A',
    'NA',
    'Not Specified'
)

function Get-BiosSerial {
    <#
    .SYNOPSIS
        Returns the raw BIOS serial number string from WMI.
    #>
    try {
        $bios = Get-WmiObject -Class Win32_BIOS -ErrorAction Stop
        return $bios.SerialNumber
    }
    catch {
        Write-Error "Failed to query Win32_BIOS: $_"
        exit 1
    }
}

function Get-OsInfo {
    <#
    .SYNOPSIS
        Returns a hashtable with OS name, version, and architecture.
    #>
    try {
        $os = Get-WmiObject -Class Win32_OperatingSystem -ErrorAction Stop
        return @{
            Caption      = $os.Caption
            BuildNumber  = $os.BuildNumber
            Architecture = $os.OSArchitecture
        }
    }
    catch {
        return @{
            Caption      = 'Unknown'
            BuildNumber  = 'Unknown'
            Architecture = 'Unknown'
        }
    }
}

function Test-IsPlaceholderSerial {
    <#
    .SYNOPSIS
        Returns $true if the serial is empty or a known meaningless placeholder.
    #>
    param(
        [string]$Serial
    )
    $trimmed = $Serial.Trim()
    foreach ($placeholder in $KnownPlaceholders) {
        if ($trimmed -ieq $placeholder) {
            return $true
        }
    }
    return $false
}

# --- Main ---

$rawSerial    = Get-BiosSerial
$normalisedSerial = $rawSerial.Trim().ToUpper()
$osInfo       = Get-OsInfo
$hostname     = $env:COMPUTERNAME

$separator = '=' * 55

Write-Host ''
Write-Host $separator
Write-Host '  Twingate Device Trust Bridge — VM Serial Info'
Write-Host $separator
Write-Host ''
Write-Host ('Hostname     : {0}' -f $hostname)
Write-Host ('OS           : {0} (Build {1})' -f $osInfo.Caption, $osInfo.BuildNumber)
Write-Host ('Architecture : {0}' -f $osInfo.Architecture)
Write-Host ''
Write-Host ('Raw serial number   : {0}' -f $rawSerial)
Write-Host ('Normalised serial   : {0}' -f $normalisedSerial)
Write-Host ''
Write-Host 'This is the value the bridge will use for matching.'
Write-Host 'Compare this against the serial shown in your MDM/EDR provider''s UI.'
Write-Host '(Comparison is case-insensitive — both sides are uppercased before matching.)'
Write-Host ''

if (Test-IsPlaceholderSerial -Serial $rawSerial) {
    Write-Host $separator
    Write-Warning 'The serial number is empty or a known placeholder value.'
    Write-Host ''
    Write-Host 'This typically means the hypervisor has not assigned a real serial to this VM.'
    Write-Host 'The bridge will not be able to match this device. Set a custom serial in your'
    Write-Host 'hypervisor settings before enrolling the VM in a provider:'
    Write-Host ''
    Write-Host '  Hyper-V (PowerShell, run on the host):'
    Write-Host '    Set-VMBios -VMName "YourVMName" -BiosSerialNumber "TEST-SERIAL-001"'
    Write-Host ''
    Write-Host '  VMware (add to .vmx file, VM must be powered off):'
    Write-Host '    serialNumber = "TEST-SERIAL-001"'
    Write-Host ''
    Write-Host '  VirtualBox:'
    Write-Host '    VBoxManage setextradata "YourVMName" \'
    Write-Host '      "VBoxInternal/Devices/pcbios/0/Config/DmiSystemSerial" "TEST-SERIAL-001"'
    Write-Host ''
    Write-Host '  Proxmox VE (add to /etc/pve/qemu-server/{vmid}.conf, VM must be stopped):'
    Write-Host '    smbios1: serial=TEST-SERIAL-001'
    Write-Host '  Or via the Proxmox web UI: Hardware > Add > SMBios Settings'
    Write-Host ''
    Write-Host 'After setting the serial, re-enrol the VM in the provider.'
    Write-Host $separator
    exit 1
}

Write-Host $separator
exit 0
