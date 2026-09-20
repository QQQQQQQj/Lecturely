@echo off
chcp 65001 >nul
setlocal
title Lecturely Stop

rem ============================================================
rem  Lecturely portable stopper
rem  - Kills the backend listening on port 8765.
rem  - Kills the portable Ollama on port 11434 ONLY if its
rem    executable lives inside this package (never a system Ollama).
rem ============================================================
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo ============================================================
echo   Stopping Lecturely ...
echo ============================================================
echo.

echo [1/2] Stopping backend (port 8765) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host ('  stopped backend PID ' + $c.OwningProcess) } else { Write-Host '  backend not running' }"

echo [2/2] Stopping portable Ollama (port 11434) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = '%ROOT%'; $c = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; if ($proc -and $proc.Path -and ($proc.Path -like \"$root\ollama\*\")) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue; Write-Host ('  stopped portable Ollama PID ' + $proc.Id) } else { Write-Host '  port 11434 belongs to a system Ollama - left running' } } else { Write-Host '  Ollama not running' }"

echo.
echo   Done. You can close this window.
echo ============================================================
timeout /t 3 /nobreak >nul
endlocal
