@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Projektumgebung fehlt. Bitte die Einrichtung in README.md ausfuehren.
    pause
    exit /b 1
)
if exist ".venv\Scripts\lapis-control.exe" (
    start "" ".venv\Scripts\lapis-control.exe" %*
    exit /b
)
".venv\Scripts\python.exe" -m lapis_control.gui %*
if errorlevel 1 pause
