# What the "Training App" desktop shortcut runs: starts the app if it isn't
# running yet, waits until it answers, then opens it in the browser.
Set-Location $PSScriptRoot
$Port = if ($env:PORT) { $env:PORT } else { "8001" }
$Url = "http://localhost:$Port"
$running = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
  Where-Object { $_.CommandLine -match "update\.ps1" }
if (-not $running) {
  Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", "`"$PSScriptRoot\update.ps1`"" -WindowStyle Hidden
}
# The first start can take a minute (it checks GitHub for updates first).
for ($i = 0; $i -lt 120; $i++) {
  try { Invoke-WebRequest "$Url/health" -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 1 }
}
Start-Process $Url
