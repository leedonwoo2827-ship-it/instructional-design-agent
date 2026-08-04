@echo off
setlocal
cd /d "%~dp0"

REM ==========================================================================
REM  이론강의 영상 - 실행
REM
REM  교수설계 콘솔(FastAPI)을 띄운다. 영상 렌더는 이 서버가 엔진(.venv)을
REM  별 프로세스로 불러서 하므로, 이 창을 닫아도 렌더는 계속된다.
REM
REM  이 파일은 CP949 로 저장해야 한다(cmd 가 콘솔 코드페이지로 읽는다).
REM ==========================================================================

if not exist ".venv-app\Scripts\python.exe" (
  echo   [setup] 콘솔 가상환경이 없습니다. setup.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo   [setup] 엔진 가상환경이 없습니다. setup.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)

set "PORT=8701"
if not "%IDA_PORT%"=="" set "PORT=%IDA_PORT%"

echo.
echo   교수설계 가이드 에이전트   http://localhost:%PORT%
echo   이 창을 닫으면 서버가 멈춥니다. 렌더 중이면 렌더는 계속됩니다.
echo.

set "IDA_OPEN_BROWSER=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
".venv-app\Scripts\python.exe" -m uvicorn server:app --host 127.0.0.1 --port %PORT%
endlocal
