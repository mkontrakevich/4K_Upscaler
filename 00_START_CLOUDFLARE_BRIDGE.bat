@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYC=%~dp0.venv\Scripts\python.exe"
if not exist "%PYC%" (
  echo ERROR. Run 00_INSTALL_AND_START_V881.bat first.
  pause
  exit /b 1
)
title MG 4K Cloudflare Bridge
"%PYC%" "%~dp0cloudflare_bridge.py"
if errorlevel 1 pause
