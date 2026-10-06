# What the "Training App" desktop shortcut runs: starts the app if it isn't
# running yet, waits until it answers, then opens it in its own app window
# (Edge or Chrome without the address bar, like the Supplier Rolodex).
# Once the app has moved online (data\moved_online holds the address), it only opens that.
Set-Location $PSScriptRoot

function Open-AppWindow($Address) {
  foreach ($exe in "Microsoft\Edge\Application\msedge.exe", "Google\Chrome\Application\chrome.exe") {
    foreach ($root in ${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA) {
      $path = if ($root) { Join-Path $root $exe }
      if ($path -and (Test-Path $path)) {
        Start-Process $path -ArgumentList "--app=$Address", "--window-size=1280,900"
        return
      }
    }
  }
  Start-Process $Address   # no Edge or Chrome: a normal browser tab
}

$online = Join-Path $PSScriptRoot "data\moved_online"
if (Test-Path $online) {
  $address = (Get-Content $online | Select-String -Pattern "https://\S+" | Select-Object -First 1).Matches.Value
  if ($address) { Open-AppWindow $address; exit }
}
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
Open-AppWindow $Url
