@echo off
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" goto NOVENV
call ".venv\Scripts\activate.bat"

rem Self-heal: the venv may predate the FastAPI switch (used to be Streamlit).
rem Install on demand so run.bat works without remembering to re-run setup.bat.
python -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 goto INSTALL
goto START

:INSTALL
echo [setup] Installing server dependencies (first run after update)...
python -m pip install -q --disable-pip-version-check -e .
if errorlevel 1 goto INSTFAIL
goto START

:START
if "%IDA_PORT%"=="" set IDA_PORT=8701
echo.
echo ============================================
echo  Instructional Design Agent
echo  http://localhost:%IDA_PORT%
echo  Press Ctrl+C to stop.
echo ============================================
echo.
rem The browser is opened by the server once it is actually listening.
rem Opening it here raced the boot and showed ERR_CONNECTION_REFUSED.
set IDA_OPEN_BROWSER=1
python -m uvicorn server:app --host 127.0.0.1 --port %IDA_PORT%
if errorlevel 1 goto RUNFAIL
goto END

:NOVENV
echo.
echo [error] .venv not found. Please run setup.bat first.
echo.
pause
exit /b 1

:INSTFAIL
echo.
echo [error] Dependency install failed. Run setup.bat and check the messages.
echo.
pause
exit /b 1

:RUNFAIL
echo.
echo [error] Server exited with an error. See the messages above.
echo.
pause
exit /b 1

:END
pause
