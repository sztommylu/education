# Run in an Administrator PowerShell on the Windows host.
param(
    [Parameter(Mandatory=$true)][string]$WslAddress,
    [Parameter(Mandatory=$true)][string]$ListenAddress,
    [ValidateRange(1,65535)][int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
foreach ($address in @($WslAddress, $ListenAddress)) {
    $parsed = [System.Net.IPAddress]::Parse($address)
    if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw 'IPv4 addresses are required.'
    }
}
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Please run this script in an Administrator PowerShell window.'
}
& netsh interface portproxy add v4tov4 "listenaddress=$ListenAddress" "listenport=$Port" "connectaddress=$WslAddress" "connectport=$Port"
if ($LASTEXITCODE -ne 0) { throw 'Could not configure WSL port forwarding.' }
$ruleName = "Education-OCR-LAN-$Port"
$existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($existing) { $existing | Remove-NetFirewallRule }
New-NetFirewallRule -Name $ruleName -DisplayName "Education OCR LAN $Port" -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $ListenAddress -LocalPort $Port -RemoteAddress LocalSubnet | Out-Null
Write-Host "OCR LAN URL: http://${ListenAddress}:$Port"
Write-Host 'If the WSL IP changes after a restart, run this script again with the new address.'
