@echo off
rem CampusNetAssistant - install the system-level guard (needs admin).
rem ASCII only: Chinese text inside .cmd breaks under some code pages.
chcp 65001 >nul
cd /d "%~dp0.."

set "LOGDIR=%LOCALAPPDATA%\CampusNetAssistant"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set "LOG=%LOGDIR%\install-launch.log"
echo [%date% %time%] ==== install-boot ==== >> "%LOG%"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] not elevated, requesting UAC >> "%LOG%"
    echo Requesting administrator rights, please click Yes...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

echo [%date% %time%] elevated, running installer >> "%LOG%"
set PYTHONIOENCODING=utf-8
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m campusnet --install-boot
) else (
    python -m campusnet --install-boot
)
echo [%date% %time%] installer finished rc=%errorlevel% >> "%LOG%"
echo.
pause
