@echo off
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo.
  echo [error] .venv not found. Please run setup.bat first.
  echo.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"

REM Load .env (eol=# skips comment lines; blank lines skipped)
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
    if not "%%a"=="" set "%%a=%%b"
  )
)

if "%STREAMLIT_SERVER_PORT%"=="" set "STREAMLIT_SERVER_PORT=8701"

echo.
echo [run] Starting Streamlit on http://localhost:%STREAMLIT_SERVER_PORT%
echo [run] Press Ctrl+C to stop.
echo.

streamlit run app.py --server.port %STREAMLIT_SERVER_PORT%
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
  echo.
  echo [error] streamlit exited with code %EXITCODE%. Check that setup.bat finished.
  echo.
)
pause
