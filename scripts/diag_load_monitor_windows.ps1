param(
    [string]$Csv = "$env:TEMP\diag_win.csv",
    [int]$Duration = 120,
    [double]$Interval = 1.0
)

# Windows host load diagnostics: samples CPU% / memory / temperature (best effort) / boot_time, and detects a reboot.
# Purpose (owner, 2026-09-09): investigate "high load causes shutdown / power loss". Run this script in a second window
# while NMRForge runs a SMILE reconstruction (or another heavy load); if the machine powers off, the script records
# to CSV before the shutdown; after a reboot the script has not finished, but /proc is not comparable - boot_time is newer, which identifies the reboot.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\diag_load_monitor_windows.ps1 `
#       -Csv "$env:TEMP\diag_win.csv" -Duration 180

$ErrorActionPreference = "SilentlyContinue"
$t0 = Get-Date
$rows = New-Object System.Collections.Generic.List[string]

$null = (Get-Counter "\Processor(_Total)\% Processor Time" -SampleInterval 1 -MaxSamples 1)

while (((Get-Date) - $t0).TotalSeconds -lt $Duration) {
    $t = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    try { $cpu = [math]::Round((Get-Counter "\Processor(_Total)\% Processor Time" -SampleInterval 1 -MaxSamples 1).CounterSamples.CookedValue, 1) }
    catch { $cpu = -1 }
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        $memUsedGB = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1MB, 2)
        $memAvailGB = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
        $boot = $os.LastBootUpTime.ToString("yyyy-MM-dd HH:mm:ss")
    } catch { $memUsedGB = -1; $memAvailGB = -1; $boot = "" }
    $tempC = -1
    try {
        $tz = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature
        if ($tz) { $tempC = [math]::Round(($tz.CurrentTemperature / 10.0) - 273.15, 1) }
    } catch { $tempC = -1 }
    $rows.Add("$t,$cpu,$memUsedGB,$memAvailGB,$tempC,$boot")
    Start-Sleep -Milliseconds ([int]($Interval * 1000))
}

$header = "t,cpu_pct,mem_used_gb,mem_avail_gb,temp_c,boot_time"
Set-Content -LiteralPath $Csv -Value ($header + "`n" + ($rows -join "`n")) -Encoding UTF8
Write-Host "done wrote=$Csv samples=$($rows.Count)"
