@echo off
rem Starts the training app on this PC (http://localhost:8001) and keeps it updated.
rem Double-click this file. It runs in the background; closing this window is fine.
cd /d "%~dp0"
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0update.ps1"
echo Training app starting. Open http://localhost:8001 in a minute.
timeout /t 5 >nul
