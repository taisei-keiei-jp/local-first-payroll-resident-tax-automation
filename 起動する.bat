@echo off
setlocal
cd /d "%~dp0"
set "DEMO_PYTHON="
py -3.14 -c "import sys" >nul 2>&1
if not errorlevel 1 set "DEMO_PYTHON=py -3.14"
if not defined DEMO_PYTHON (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "DEMO_PYTHON=python"
)
if not defined DEMO_PYTHON (
  echo Python 3.14 or python was not found.
  pause
  exit /b 1
)
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:8520/"
%DEMO_PYTHON% -m streamlit run app.py --server.address 127.0.0.1 --server.port 8520 --server.headless true --server.fileWatcherType none
endlocal
