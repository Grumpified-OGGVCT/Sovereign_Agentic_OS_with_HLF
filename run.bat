@echo off
setlocal
title Sovereign Agentic OS Boot Menu
color 0B

echo ===========================================
echo        Sovereign Agentic OS Boot Menu
echo                 v1.0.0
echo ===========================================
echo.

:: Check if uv is accessible
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] uv is not installed. 
    echo Please run install.bat first!
    pause
    exit /b 1
)

echo 1. Quick Start All (OS Backend + Command Center + Tray Manager)
echo 2. Launch Taskbar Manager Only (Silent Background Mode)
echo 3. Exit Boot Menu
echo.
set /p choice="Select an OS boot mode (1-3): "

if "%choice%"=="1" goto mode1
if "%choice%"=="2" goto mode2
if "%choice%"=="3" goto mode3
goto end

:mode1
echo [BOOT] Initializing Sovereign System Tray Manager...
echo [BOOT] The Taskbar Manager will Auto-launch backends and the GUI.
start /B uv run pythonw gui\tray_manager.py --auto-launch >nul 2>&1
echo -----------------------------------------------------------------
echo Look for the 👑 icon in your Windows Taskbar tray (bottom right).
echo The Sovereign Command Center GUI will open in your browser shortly.
echo You may safely close this black command window.
echo -----------------------------------------------------------------
pause
exit

:mode2
echo [BOOT] Starting System Tray Manager silently...
start /B uv run pythonw gui\tray_manager.py >nul 2>&1
echo -----------------------------------------------------------------
echo Look for the 👑 icon in your Windows Taskbar tray (bottom right).
echo Right-click the icon to control OS services.
echo You may safely close this black command window.
echo -----------------------------------------------------------------
pause
exit

:mode3
:end
echo Exiting Boot Menu.
exit /b 0
