@echo off
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "ACTIVE_ROOT="

if exist "%~dp0.venv\Scripts\python.exe" if exist "%~dp0v8_safe_appearance.py" set "ACTIVE_ROOT=%~dp0"

if not defined ACTIVE_ROOT (
  for /f "usebackq delims=" %%I in (`powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$p=Get-ChildItem -LiteralPath 'D:\Work' -Filter python.exe -File -Recurse -ErrorAction SilentlyContinue ^| Where-Object { $_.FullName -match '\\\\.venv\\Scripts\\python\\.exe$' -and (Test-Path -LiteralPath (Join-Path $_.Directory.Parent.Parent.FullName 'v8_safe_appearance.py')) } ^| Select-Object -First 1; if($p){$p.Directory.Parent.Parent.FullName}"`) do set "ACTIVE_ROOT=%%I\"
)

if not defined ACTIVE_ROOT goto :NO_ACTIVE_INSTALL

echo Active MG 4K install: %ACTIVE_ROOT%
set "PYC=%ACTIVE_ROOT%.venv\Scripts\python.exe"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest 'https://raw.githubusercontent.com/mkontrakevich/4K_Upscaler/main/cloudflare_bridge.py' -OutFile '%ACTIVE_ROOT%cloudflare_bridge.py'"
if errorlevel 1 goto :DOWNLOAD_FAILED

title MG 4K Cloudflare Bridge
"%PYC%" "%ACTIVE_ROOT%cloudflare_bridge.py"
if errorlevel 1 pause
exit /b %ERRORLEVEL%

:NO_ACTIVE_INSTALL
echo ERROR. Active MG 4K V8.8.1 installation with .venv was not found under D:\Work.
echo Run 00_INSTALL_AND_START_V881.bat once, then start this bridge again.
pause
exit /b 1

:DOWNLOAD_FAILED
echo ERROR. cloudflare_bridge.py could not be updated from GitHub.
pause
exit /b 2
