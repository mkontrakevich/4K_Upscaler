@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "PYC=%~dp0.venv\Scripts\python.exe"
if not exist "%PYC%" goto :NOT_INSTALLED
if exist "%PYW%" (
  start "" "%PYW%" "%~dp0web_console_bootstrap.py"
) else (
  start "MG 4K Web Review" "%PYC%" "%~dp0web_console_bootstrap.py"
)
exit /b 0
:NOT_INSTALLED
echo ERROR. Run 00_INSTALL_AND_START_V881.bat first.
pause
exit /b 1
