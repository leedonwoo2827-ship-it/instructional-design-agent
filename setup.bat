@echo off
setlocal
cd /d "%~dp0"

REM ==========================================================================
REM  이론강의 영상 - 최초 1회 설치
REM
REM  가상환경을 두 개 만든다:
REM    .venv       영상 엔진 (onnxruntime, pywin32, soundfile)
REM    .venv-app   교수설계 콘솔 (fastapi, openai)
REM
REM  섞지 않는 이유: onnxruntime 이 콘솔 쪽 의존성과 충돌하고, PowerPoint COM 은
REM  스레드 제약이 있어 별 프로세스로 돌려야 한다. 콘솔이 엔진을 subprocess 로 부른다.
REM
REM  이 파일은 CP949 로 저장해야 한다(cmd 가 콘솔 코드페이지로 읽는다).
REM ==========================================================================

echo.
echo ============================================
echo  1/5  파이썬 확인
echo ============================================
where python >nul 2>nul
if errorlevel 1 (
  echo   [error] python 이 PATH 에 없습니다. Python 3.12 를 설치하세요.
  echo           https://www.python.org/downloads/  ^("Add Python to PATH" 체크^)
  goto :fail
)
python -c "import sys; sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)"
if errorlevel 1 (
  echo   [error] Python 3.10 이상이 필요합니다.
  goto :fail
)
python -c "import sys; print('  python', '.'.join(map(str,sys.version_info[:3])))"

echo.
echo ============================================
echo  2/5  영상 엔진 가상환경  (.venv)
echo ============================================
if not exist ".venv\Scripts\python.exe" (
  echo   .venv 만드는 중...
  python -m venv .venv
  if errorlevel 1 goto :fail
)
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo   [error] 엔진 의존성 설치 실패.
  goto :fail
)
echo   OK

echo.
echo ============================================
echo  3/5  교수설계 콘솔 가상환경  (.venv-app)
echo ============================================
if not exist ".venv-app\Scripts\python.exe" (
  echo   .venv-app 만드는 중...
  python -m venv .venv-app
  if errorlevel 1 goto :fail
)
".venv-app\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv-app\Scripts\python.exe" -m pip install -e . --quiet
if errorlevel 1 (
  echo   [error] 콘솔 의존성 설치 실패.
  goto :fail
)
if not exist ".env" (
  copy .env.example .env >nul
  echo   .env 를 .env.example 에서 만들었습니다. 산출물 폴더를 확인하세요.
)
echo   OK

echo.
echo ============================================
echo  4/5  밖에 있어야 하는 것
echo ============================================
set "MISS="
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo   [없음] ffmpeg        winget install Gyan.FFmpeg
  set "MISS=1"
) else (
  echo   [있음] ffmpeg
)
if exist "%ProgramFiles%\Microsoft Office\root\Office16\POWERPNT.EXE" (
  echo   [있음] PowerPoint
) else (
  echo   [확인] PowerPoint    슬라이드를 PNG 로 뽑는 데 필요합니다
)
dir /b "vendor\chodangi\assets\onnx\*.onnx" >nul 2>nul
if errorlevel 1 (
  echo   [없음] 음성 모델     vendor\chodangi\assets\ 에 Supertonic 3 자산 필요 ^(약 383MB^)
  set "MISS=1"
) else (
  echo   [있음] 음성 모델
)

echo.
echo ============================================
echo  5/5  엔진 점검
echo ============================================
if defined MISS (
  echo   위의 [없음] 항목을 채운 뒤 다시 실행하세요. 점검은 건너뜁니다.
) else (
  ".venv\Scripts\python.exe" scripts\doctor.py
  if errorlevel 1 (
    echo   [error] 엔진 점검 실패. 위 메시지를 확인하세요.
    goto :fail
  )
)

echo.
echo ============================================
echo  설치 완료.  run.bat 을 실행하세요.
echo ============================================
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
