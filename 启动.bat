@echo off
chcp 65001 > nul
title CST Agent Workbench
cd /d "%~dp0"

echo ============================================
echo   CST-Agent Workbench
echo ============================================
echo   [1] Agent mode  (React + FastAPI, connect CST Studio)
echo   [2] Demo mode   (React + FastAPI, no CST needed)
echo ============================================
echo.
set "PYTHON_CMD=python"
where py > nul 2> nul
if not errorlevel 1 (
    py -3.12 -c "import sys" > nul 2> nul
    if not errorlevel 1 (
        set "PYTHON_CMD=py -3.12"
    )
)
echo Using: %PYTHON_CMD%
echo.
set /p CHOICE=Select [1/2, default 1]:

set "DRY_RUN_FLAG="
if "%CHOICE%"=="2" (
    set "DRY_RUN_FLAG=--dry-run"
)

echo.
echo Starting backend API server...
echo Starting frontend dev server...
echo.
echo   Backend API: http://127.0.0.1:8787
echo   Frontend:    http://127.0.0.1:5173
echo   API Docs:    http://127.0.0.1:8787/docs
echo.

call :check_port 8787 "Backend API"
if errorlevel 1 exit /b 1
call :check_port 5173 "Frontend"
if errorlevel 1 exit /b 1

start "CST Backend" /D "%~dp0" cmd /k "%PYTHON_CMD% -m cst_agent_workbench.web_app --port 8787 %DRY_RUN_FLAG%"
cd /d "%~dp0frontend"
start "CST Frontend" cmd /k "npm.cmd run dev"
cd /d "%~dp0"

echo Servers started in separate windows.
echo Keep those windows open while using the React UI.
echo To stop, press Ctrl+C in each server window or close the windows.
echo.
pause
exit /b 0

:check_port
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %~1 -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
    echo ERROR: %~2 port %~1 is already in use.
    echo Close the old CST Backend/Frontend windows first, then run this launcher again.
    echo.
    pause
    exit /b 1
)
exit /b 0
