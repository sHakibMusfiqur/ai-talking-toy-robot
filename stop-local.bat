@echo off
setlocal EnableExtensions
title AI Talking Toy Robot - local stop

cd /d "%~dp0"

set "ENV_FILE=.env"
set "BASE_FILE=infra\docker-compose.yml"
set "UI_FILE=infra\docker-compose.local-ui.yml"

echo ============================================
echo  AI Talking Toy Robot - stop local services
echo  Project root: %CD%
echo ============================================
echo.

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker CLI not found on PATH.
    goto :fail
)

docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker daemon is not running. Start Docker Desktop first.
    goto :fail
)

if not exist "%ENV_FILE%" (
    echo [ERROR] Missing %ENV_FILE% - cannot resolve the Compose configuration.
    goto :fail
)

echo Stopping containers (mongo, mosquitto, django, ai-service, bridge)...
docker compose --env-file %ENV_FILE% -f %BASE_FILE% -f %UI_FILE% stop
if errorlevel 1 (
    echo [ERROR] "docker compose stop" failed.
    echo         Status: docker compose --env-file %ENV_FILE% -f %BASE_FILE% -f %UI_FILE% ps
    goto :fail
)

echo.
echo Stopped. Persistent data volumes (mongo_data, mosquitto_data) were NOT touched.
echo Start again with: start-local.bat
exit /b 0

:fail
echo.
echo [FAILED] See message above.
pause
exit /b 1
