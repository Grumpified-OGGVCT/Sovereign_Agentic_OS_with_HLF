@echo off
setlocal
echo =========================================
echo Sovereign Agentic OS Setup Wizard
echo =========================================
echo.

echo Checking for uv (Python package manager)...
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] uv not found!
    echo Please install uv from: https://docs.astral.sh/uv/getting-started/installation
    echo OR run this command in a new PowerShell as Administrator:
    echo powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    pause
    exit /b 1
)
echo [OK] uv is installed.

echo.
echo Checking for Docker (Required for Redis and Dapr)...
where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Docker not found!
    echo Please install Docker Desktop, start it, and try again.
    pause
    exit /b 1
)
echo [OK] Docker is installed.

echo.
echo Syncing project dependencies...
call uv sync --all-extras

echo.
echo Updating requirements.txt based on current state...
call uv pip compile pyproject.toml -o requirements.txt

echo.
echo Pulling minimum Docker images required for the OS...
docker pull redis:7-alpine

echo.
echo =========================================
echo Installation Complete!
echo You can now use run.bat to start the system.
echo =========================================
pause
