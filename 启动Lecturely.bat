@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Lecturely Launcher

rem ============================================================
rem  Lecturely portable launcher
rem  Resolves package root from this script's own location, so
rem  the whole folder can be moved/copied anywhere and still run.
rem ============================================================
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

rem ---- portable environment (offline, local models, local data) ----
set "HF_HOME=%ROOT%\models\hf"
set "HF_HUB_OFFLINE=1"
set "HF_HUB_DISABLE_XET=1"
set "OLLAMA_MODELS=%ROOT%\ollama\models"
set "OLLAMA_HOST=127.0.0.1:11434"
set "LECTURELY_DATA_DIR=%ROOT%\data"
set "LECTURELY_PORT=8765"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo ============================================================
echo   Lecturely  -  Local Realtime Translation ^& Notes
echo ============================================================
echo.
echo   Package root: %ROOT%
echo.

cd /d "%ROOT%"

rem ---- 1. start portable Ollama (if bundled) ----
if exist "%ROOT%\ollama\ollama.exe" (
    echo [1/3] Starting Ollama service ^(AI summary / Q^&A^) ...
    start "LecturelyOllama" /MIN "%ROOT%\ollama\ollama.exe" serve
    timeout /t 3 /nobreak >nul
) else (
    echo [1/3] Ollama not bundled - AI features may be unavailable.
)

rem ---- 2. start backend (separate minimized window, survives this console) ----
echo [2/3] Starting Lecturely backend ...
if not exist "%ROOT%\python\python.exe" (
    echo.
    echo [ERROR] Embedded Python not found: %ROOT%\python\python.exe
    echo         The package may be incomplete. Please re-extract the full archive.
    pause
    exit /b 1
)
start "LecturelyBackend" /MIN "%ROOT%\python\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8765

rem ---- 3. wait for health then open browser ----
echo [3/3] Waiting for the service to be ready ...
set "TRIES=0"
:wait_loop
curl.exe -s -o nul --max-time 2 http://127.0.0.1:8765/api/health >nul 2>&1
if %errorlevel%==0 goto ready
timeout /t 1 /nobreak >nul
set /a TRIES+=1
if %TRIES% geq 60 goto fail
goto wait_loop

:ready
echo.
echo   Service is ready. Opening browser ...
start "" "http://127.0.0.1:8765"
echo.
echo   Lecturely is running in the background.
echo   - You may minimize or close THIS window; the service keeps running.
echo   - To stop everything, run:  停止Lecturely.bat
echo.
echo   If the browser did not open, visit:  http://127.0.0.1:8765
echo ============================================================
timeout /t 8 /nobreak >nul
endlocal
exit /b 0

:fail
echo.
echo   [ERROR] The service did not become ready within 60 seconds.
echo   Please open the minimized "LecturelyBackend" window to view error logs,
echo   or check: %ROOT%\data\logs
echo ============================================================
pause
endlocal
exit /b 1
