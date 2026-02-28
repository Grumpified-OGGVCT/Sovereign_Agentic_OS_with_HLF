@echo off
setlocal
title Sovereign Agentic OS Boot Menu
color 0B

echo ===========================================
echo        Sovereign Agentic OS Boot Menu
echo              v2.0.0 (MCP Enabled)
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

echo 1. Quick Start All (Backend + GUI + MCP Server)
echo 2. Launch Taskbar Manager Only (Silent Background Mode)
echo 3. Start MCP Server Only (For Antigravity IDE Integration)
echo 4. Exit Boot Menu
echo.
set /p choice="Select an OS boot mode (1-4): "

if "%choice%"=="1" goto mode1
if "%choice%"=="2" goto mode2
if "%choice%"=="3" goto mode3
if "%choice%"=="4" goto mode4
goto end

:mode1
echo [BOOT] Initializing Sovereign System Tray Manager...
echo [BOOT] The Taskbar Manager will Auto-launch backends, GUI, and MCP Server.
start /B uv run pythonw gui\tray_manager.py --auto-launch >nul 2>&1
echo -----------------------------------------------------------------
echo Look for the crown icon in your Windows Taskbar tray (bottom right).
echo The Sovereign Command Center GUI will open in your browser shortly.
echo MCP Server is running for Antigravity IDE integration.
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
echo [BOOT] Starting MCP Server for Antigravity IDE integration...
start /B uv run python mcp\sovereign_mcp_server.py >nul 2>&1
echo -----------------------------------------------------------------
echo MCP Server is now running in the background.
echo Antigravity can connect via the sovereign-os MCP server entry.
echo Tools available: check_health, dispatch_intent, run_dream_cycle,
echo   get_hat_findings, list_align_rules, get_system_state,
echo   query_memory, get_dream_history
echo -----------------------------------------------------------------
pause
exit

:mode4
:end
echo Exiting Boot Menu.
exit /b 0
