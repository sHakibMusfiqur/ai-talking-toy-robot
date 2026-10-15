@echo off
setlocal EnableExtensions
title AI Talking Toy Robot - local launcher

rem Resolve the project root from this script's own location (spaces safe).
cd /d "%~dp0"

set "ENV_FILE=.env"
set "BASE_FILE=infra\docker-compose.yml"
set "UI_FILE=infra\docker-compose.local-ui.yml"
set "HEALTH_URL=http://127.0.0.1:8000/health"
set "STATIC_URL_PROBE=http://127.0.0.1:8000/static/css/app.css"
set "APP_URL=http://127.0.0.1:8000/dashboard/"
set "DOCKER_WAIT_SECONDS=60"
set "READY_TIMEOUT_SECONDS=180"

echo ============================================
echo  AI Talking Toy Robot - local launcher
echo  Project root: %CD%
echo ============================================
echo.

rem --- Docker CLI -----------------------------------------------------------
where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker CLI not found on PATH.
    echo         Install Docker Desktop, then run start-local.bat again.
    goto :fail
)

rem --- Docker daemon --------------------------------------------------------
echo Waiting for the Docker daemon (up to %DOCKER_WAIT_SECONDS%s)...
set /a "_waited=0"
:docker_wait
docker info >nul 2>&1
if not errorlevel 1 goto :docker_ready
if %_waited% GEQ %DOCKER_WAIT_SECONDS% (
    echo [ERROR] Docker daemon not ready after %DOCKER_WAIT_SECONDS%s.
    echo         Start Docker Desktop and wait until it finishes starting,
    echo         then run start-local.bat again.
    goto :fail
)
powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul
set /a "_waited+=2"
goto :docker_wait

:docker_ready
echo Docker daemon ready.

rem --- Configuration files (existence only; contents never printed) --------
if not exist "%ENV_FILE%" (
    echo [ERROR] Missing %ENV_FILE% - required configuration file.
    echo         One-time step: copy .env.example to .env and fill in the
    echo         required values, then run start-local.bat again.
    echo         This script never prints file contents or secrets.
    goto :fail
)
if not exist "%BASE_FILE%" (
    echo [ERROR] Missing %BASE_FILE%
    goto :fail
)
if not exist "%UI_FILE%" (
    echo [ERROR] Missing %UI_FILE%
    goto :fail
)

rem --- Start the stack (idempotent: re-runs never duplicate containers) ----
echo Starting services: mongo, mosquitto, django, ai-service...
docker compose --env-file %ENV_FILE% -f %BASE_FILE% -f %UI_FILE% up -d --build
if errorlevel 1 (
    echo [ERROR] "docker compose up" failed.
    echo         Status: docker compose --env-file %ENV_FILE% -f %BASE_FILE% -f %UI_FILE% ps
    echo         Logs:   docker compose --env-file %ENV_FILE% -f %BASE_FILE% -f %UI_FILE% logs --tail=60 django
    goto :fail
)

rem --- Readiness: real checks, bounded wait -------------------------------
echo Waiting for readiness (up to %READY_TIMEOUT_SECONDS%s)...
set /a "_waited=0"
:ready_loop
call :all_ready
if not errorlevel 1 goto :ready
if %_waited% GEQ %READY_TIMEOUT_SECONDS% goto :ready_timeout
powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul
set /a "_waited+=2"
goto :ready_loop

:ready_timeout
echo [ERROR] Services did not become ready within %READY_TIMEOUT_SECONDS%s.
echo Current status:
call :print_status
echo.
echo Inspect logs with:
echo   docker compose --env-file %ENV_FILE% -f %BASE_FILE% -f %UI_FILE% ps
echo   docker compose --env-file %ENV_FILE% -f %BASE_FILE% -f %UI_FILE% logs --tail=60 django
echo   docker compose --env-file %ENV_FILE% -f %BASE_FILE% -f %UI_FILE% logs --tail=60 ai-service
goto :fail

:ready
echo.
echo All services are ready:
call :print_status
echo.
echo Application URL: %APP_URL%
echo Stop everything with: stop-local.bat
echo.
start "" "%APP_URL%"
exit /b 0

rem =========================== subroutines ================================

:all_ready
call :health_of mongo
if /i not "%_H%"=="healthy" exit /b 1
call :health_of mosquitto
if /i not "%_H%"=="healthy" exit /b 1
call :health_of ai-service
if /i not "%_H%"=="healthy" exit /b 1
call :http_ok
exit /b %ERRORLEVEL%

:health_of
set "_H="
set "_CID="
for /f "delims=" %%i in ('docker compose --env-file %ENV_FILE% -f %BASE_FILE% -f %UI_FILE% ps -q %~1 2^>nul') do set "_CID=%%i"
if not defined _CID exit /b 0
for /f "delims=" %%j in ('docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" %_CID% 2^>nul') do set "_H=%%j"
exit /b 0

:http_ok
powershell -NoProfile -Command "try { $a = Invoke-WebRequest -UseBasicParsing -Uri '%HEALTH_URL%' -TimeoutSec 3 -ErrorAction Stop; $b = Invoke-WebRequest -UseBasicParsing -Uri '%STATIC_URL_PROBE%' -TimeoutSec 3 -ErrorAction Stop; if ($a.StatusCode -eq 200 -and $b.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
exit /b %ERRORLEVEL%

:print_status
call :health_of mongo
echo   mongo: %_H%
call :health_of mosquitto
echo   mosquitto: %_H%
call :health_of ai-service
echo   ai-service: %_H%
call :http_ok
if errorlevel 1 (echo   django /health + static: NOT READY) else (echo   django /health + static: OK)
exit /b 0

:fail
echo.
echo [FAILED] Launcher did not finish - see messages above.
pause
exit /b 1
