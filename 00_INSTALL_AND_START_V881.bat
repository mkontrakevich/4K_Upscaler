@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0INSTALL_V881.ps1"
if errorlevel 1 goto :FAILED
exit /b 0
:FAILED
echo ERROR. V8.8.1 installation did not complete.
pause
exit /b 1
