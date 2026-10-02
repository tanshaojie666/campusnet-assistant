@echo off
rem CampusNetAssistant - uninstall the system-level guard (needs admin).
chcp 65001 >nul
cd /d "%~dp0.."

set "LOGDIR=%LOCALAPPDATA%\CampusNetAssistant"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set "LOG=%LOGDIR%\install-launch.log"
echo [%date% %time%] ==== uninstall-boot ==== >> "%LOG%"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights, please click Yes...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

set PYTHONIOENCODING=utf-8
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m campusnet --uninstall-boot
) else (
    python -m campusnet --uninstall-boot
)
echo [%date% %time%] uninstaller finished rc=%errorlevel% >> "%LOG%"
echo.
pause
