param(
  [string]$Adb = $(if ($env:ANDROID_RUNNER_ADB) { $env:ANDROID_RUNNER_ADB } else { "E:\edge download\scrcpy-win64-v4.1\adb.exe" }),
  [string]$ExpectedPhoneIp = ""
)

$ErrorActionPreference = "Continue"
if (-not (Test-Path -LiteralPath $Adb)) { Write-Error "ADB not found: $Adb"; exit 2 }

Write-Output "=== ADB ==="
& $Adb version
& $Adb devices -l
Write-Output "=== mDNS services ==="
& $Adb mdns services
Write-Output "=== active IPv4 interfaces ==="
try {
  Get-NetIPConfiguration -ErrorAction Stop | Where-Object { $_.IPv4Address } | ForEach-Object {
    $ip = $_.IPv4Address.IPAddress
    $prefix = $_.IPv4Address.PrefixLength
    [pscustomobject]@{ Interface = $_.InterfaceAlias; IPv4 = $ip; Prefix = $prefix; Gateway = $_.IPv4DefaultGateway.NextHop }
  } | Format-Table -AutoSize
} catch { Write-Output "NetIPConfiguration unavailable; falling back to ipconfig"; ipconfig | Select-String -Pattern 'adapter|IPv4 Address|Subnet Mask|Default Gateway' }
Write-Output "=== known tunnel processes/interfaces ==="
Get-Process | Where-Object { $_.ProcessName -match 'mihomo|clash|v2ray|warp|tailscale|zerotier' } | Select-Object ProcessName,Id
try { Get-NetAdapter -IncludeHidden -ErrorAction Stop | Where-Object { $_.InterfaceDescription -match 'Meta Tunnel|Mihomo|Clash|TUN' } | Select-Object Name,InterfaceDescription,Status,AdminStatus } catch { Write-Output "NetAdapter details unavailable (administrator privileges may be required)" }

if ($ExpectedPhoneIp) {
  Write-Output "=== reachability: $ExpectedPhoneIp ==="
  Test-NetConnection $ExpectedPhoneIp -Port 43025 -InformationLevel Detailed |
    Select-Object RemoteAddress,RemotePort,SourceAddress,TcpTestSucceeded
}
