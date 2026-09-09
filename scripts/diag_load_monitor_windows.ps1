param(
    [string]$Csv = "$env:TEMP\diag_win.csv",
    [int]$Duration = 120,
    [double]$Interval = 1.0
)

# Windows 本机负载诊断:采样 CPU%/内存/温度(尽力)/boot_time,并检测是否重启。
# 用途(用户,2026-09-09):排查「高负载导致关机/断电」。另开一个窗口跑本脚本,
# 同时在 NMRForge 里跑一段 SMILE 重构(或其它高负载);若电脑关机,脚本会在关机前
# 记录到 CSV;重启后脚本未跑完但 /proc 不可比——重启后 boot_time 会变新,可判断。
#
# 用法:
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
