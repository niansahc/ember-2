@echo off
setlocal enabledelayedexpansion
cd /d %~dp0

echo.
echo  Ember-2 Launcher
echo  =================
echo.

:: -----------------------------------------------------------
:: 1. Docker Desktop — start if not running
:: -----------------------------------------------------------
echo [1/4] Checking Docker...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo       Docker is not running. Starting Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" 2>nul
    if %errorlevel% neq 0 (
        echo.
        echo  ERROR: Could not find Docker Desktop.
        echo  Install it from https://www.docker.com/products/docker-desktop
        echo  Then run this script again.
        echo.
        pause
        exit /b 1
    )
    set DOCKER_READY=0
    for /l %%i in (1,1,30) do (
        if !DOCKER_READY! equ 0 (
            timeout /t 3 /nobreak >nul
            docker info >nul 2>&1
            if !errorlevel! equ 0 (
                set DOCKER_READY=1
                echo       Docker is ready.
            ) else (
                echo       Waiting for Docker... %%i/30
            )
        )
    )
    if !DOCKER_READY! equ 0 (
        echo.
        echo  ERROR: Docker did not start after 90 seconds.
        echo  Open Docker Desktop manually, wait for it to finish loading,
        echo  then run this script again.
        echo.
        pause
        exit /b 1
    )
) else (
    echo       Docker is running.
)

:: -----------------------------------------------------------
:: 2. SearXNG via Docker Compose
:: -----------------------------------------------------------
echo [2/4] Starting SearXNG...
docker compose up -d 2>nul
if %errorlevel% neq 0 (
    echo       WARNING: docker compose failed. Web search may not work.
    echo       Continuing anyway...
) else (
    echo       SearXNG started.
)

:: -----------------------------------------------------------
:: 3. Start the API
:: -----------------------------------------------------------
echo [3/4] Starting Ember API...

if not exist .venv\Scripts\activate.bat (
    echo.
    echo  ERROR: Python virtual environment not found at .venv\
    echo  Run the Ember installer or create it manually:
    echo    python -m venv .venv
    echo    .venv\Scripts\activate
    echo    pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

call .venv\Scripts\activate

set EMBER_HOST=0.0.0.0
for /f "tokens=1,* delims==" %%a in ('findstr /b "EMBER_HOST" .env 2^>nul') do set EMBER_HOST=%%b

start "Ember-2 API" /min cmd /c "cd /d %~dp0 && call .venv\Scripts\activate && python -m uvicorn src.api.main:app --host %EMBER_HOST% --port 8000"

echo       API starting in background...

:: -----------------------------------------------------------
:: 4. Health check polling
:: -----------------------------------------------------------
echo [4/4] Waiting for API health check...

set HEALTHY=0
for /l %%i in (1,1,20) do (
    if !HEALTHY! equ 0 (
        timeout /t 3 /nobreak >nul
        curl -s -o nul -w "%%{http_code}" http://localhost:8000/api/health 2>nul | findstr "200" >nul 2>&1
        if !errorlevel! equ 0 (
            set HEALTHY=1
            echo       API is healthy.
        ) else (
            echo       Polling... %%i/20
        )
    )
)

if !HEALTHY! equ 0 (
    echo.
    echo  ERROR: API did not respond after 60 seconds.
    echo.
    echo  Troubleshooting:
    echo    1. Check the "Ember-2 API" window for error messages
    echo    2. Make sure port 8000 is not in use: netstat -ano ^| findstr :8000
    echo    3. Try starting manually: .venv\Scripts\activate ^&^& uvicorn src.api.main:app --host 0.0.0.0 --port 8000
    echo    4. Check .env exists and PRIVATE_VAULT_PATH is set
    echo.
    pause
    exit /b 1
)

:: -----------------------------------------------------------
:: 5. Open browser
:: -----------------------------------------------------------
echo.
echo  Ember is ready. Opening browser...
echo  http://localhost:8000
echo.
start http://localhost:8000

endlocal
