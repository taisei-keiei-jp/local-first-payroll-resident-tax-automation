@echo off
setlocal
cd /d "%~dp0"
echo This deletes only this demo's imported notices, payroll history, issued files, and generated output.
choice /C YN /N /M "Reset demo data? [Y/N]: "
if errorlevel 2 exit /b 1
py -3.14 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3.14 reset_demo_data.py --yes
) else (
  python -c "import sys" >nul 2>&1 || (echo Python 3.14 or python was not found.& pause & exit /b 1)
  python reset_demo_data.py --yes
)
pause
