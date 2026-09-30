# Windows version of update.sh: runs the training app on this PC and keeps it
# up to date by itself. Every minute it:
#   1. saves lesson edits made on the Manager page (content/) to GitHub,
#   2. picks up any new version of the app from GitHub and restarts it.
# Employee records (data/) never leave this PC. Start it with start.bat.
param([int]$Replaces = 0)   # set when an updated updater takes over from the old one
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
if (-not $env:PORT) { $env:PORT = "8001" }
if (-not $env:HOST) { $env:HOST = "127.0.0.1" }   # this PC only
$Branch = (git rev-parse --abbrev-ref HEAD).Trim()
$Py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$Pid_File = Join-Path $PSScriptRoot "data\app.pid"

# Only one updater at a time.
if ($Replaces) { Wait-Process -Id $Replaces -Timeout 30 -ErrorAction SilentlyContinue }
$me = $PID
$others = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
  Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -match "update\.ps1" }
if ($others) { Write-Output "Updater already running."; exit 0 }
Start-Transcript -Path (Join-Path $PSScriptRoot "update.log") -Append | Out-Null

function Hash($f) { if (Test-Path $f) { (Get-FileHash $f -Algorithm SHA1).Hash } else { "" } }

function Stop-App {
  if (Test-Path $Pid_File) {
    $old = Get-Content $Pid_File -ErrorAction SilentlyContinue
    if ($old) { Stop-Process -Id $old -Force -ErrorAction SilentlyContinue }
    Remove-Item $Pid_File -ErrorAction SilentlyContinue
  }
}

function Start-App {
  Stop-App
  Start-Sleep 1
  New-Item -ItemType Directory -Force data | Out-Null
  $p = Start-Process -FilePath $Py -ArgumentList "-m", "trainer" -WindowStyle Hidden -PassThru `
         -RedirectStandardOutput app.log -RedirectStandardError app.err.log
  Set-Content $Pid_File $p.Id
  Write-Output "Training app running at http://localhost:$($env:PORT) ($(git log --oneline -1))"
}

function App-Running {
  if (-not (Test-Path $Pid_File)) { return $false }
  $id = Get-Content $Pid_File -ErrorAction SilentlyContinue
  return [bool]($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue))
}

function Save-Work {
  if (git status --porcelain content) {
    git add -A content
    git commit -q -m "Work PC: lesson edits saved"
    if ($LASTEXITCODE -eq 0) { Write-Output "Saved lesson edits." }
  }
}

function Sync {
  Save-Work
  git fetch -q origin $Branch
  if ($LASTEXITCODE -ne 0) { return }
  if ((git rev-parse HEAD) -ne (git rev-parse "origin/$Branch")) {
    $req = Hash "requirements.txt"; $script = Hash "update.ps1"
    # Your edits win if the same lesson changed on both sides.
    git merge -q --no-edit -X ours "origin/$Branch"
    if ($LASTEXITCODE -eq 0) {
      if ((Hash "requirements.txt") -ne $req) { & $Py -m pip install -q -r requirements.txt }
      if ((Hash "update.ps1") -ne $script) {
        Write-Output "Updater changed; restarting it."
        Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", "`"$PSCommandPath`"", "-Replaces", $PID -WindowStyle Hidden
        Stop-App; Stop-Transcript | Out-Null; exit 0
      }
      Start-App
    } else {
      git merge --abort 2>$null
      Write-Output "Couldn't merge the update. Send app.log and this message to Claude."
    }
  }
  # Send saved edits to GitHub.
  if (git log --oneline "origin/$Branch..HEAD" 2>$null) {
    git push -q origin "HEAD:$Branch"
    if ($LASTEXITCODE -ne 0) { Write-Output "Couldn't save to GitHub yet; will try again in a minute." }
  }
}

& $Py -m pip install -q -r requirements.txt
Sync
Start-App
while ($true) {
  Start-Sleep 60
  Sync
  if (-not (App-Running)) { Start-App }   # restart the app if it stopped for any reason
}
