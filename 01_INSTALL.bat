@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
where py.exe >nul 2>nul
if errorlevel 1 goto :NO_PYTHON
if not exist "%VENV_PY%" (
  echo [1/8] Creating Python environment...
  py -3 -m venv "%~dp0.venv"
  if errorlevel 1 goto :FAILED
)
echo [2/8] Updating pip...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto :FAILED
echo [3/8] Installing dependencies...
"%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto :FAILED
echo [4/8] Verifying build identity and two-mode isolation...
"%VENV_PY%" "%~dp0version_guard.py"
if errorlevel 1 goto :FAILED
"%VENV_PY%" "%~dp0source_queue_isolation_self_test.py"
if errorlevel 1 goto :FAILED
"%VENV_PY%" "%~dp0source_folder_picker_self_test.py"
if errorlevel 1 goto :FAILED
"%VENV_PY%" "%~dp0sibling_donor_recovery_self_test.py"
if errorlevel 1 goto :FAILED
echo [5/8] Verifying SAFE local upscale with zero API requests...
"%VENV_PY%" "%~dp0safe_local_upscale.py" --self-test
if errorlevel 1 goto :FAILED
echo [6/8] Verifying strict camera, whole-scene and material gates...
"%VENV_PY%" "%~dp0v8_safe_appearance.py" --validator-self-test
if errorlevel 1 goto :FAILED
"%VENV_PY%" "%~dp0material_identity_self_test.py"
if errorlevel 1 goto :FAILED
"%VENV_PY%" "%~dp0canvas_integrity_self_test.py"
if errorlevel 1 goto :FAILED
"%VENV_PY%" "%~dp0source_sky_recovery_self_test.py"
if errorlevel 1 goto :FAILED
"%VENV_PY%" "%~dp0dual_mode_self_test.py"
if errorlevel 1 goto :FAILED
"%VENV_PY%" "%~dp0path_relocation_self_test.py"
if errorlevel 1 goto :FAILED
"%VENV_PY%" "%~dp0retry_budget_self_test.py"
if errorlevel 1 goto :FAILED
echo [7/8] Verifying local web console and explicit generation control...
"%VENV_PY%" "%~dp0web_review_server.py" --self-test
if errorlevel 1 goto :FAILED
"%VENV_PY%" "%~dp0viewer_inspection_self_test.py"
if errorlevel 1 goto :FAILED
echo [8/8] Verifying self-healing web bootstrap and fallback ports...
"%VENV_PY%" "%~dp0web_console_bootstrap.py" --self-test
if errorlevel 1 goto :FAILED
echo.
echo V8.8.1 SCENE STABILITY PROFILE VERIFIED. NO PAID API REQUEST WAS MADE.
if "%NO_PAUSE%"=="0" pause
exit /b 0
:NO_PYTHON
echo ERROR: Python 3 was not found. Install Python and enable Add Python to PATH.
:FAILED
echo.
echo Installation did not complete.
if "%NO_PAUSE%"=="0" pause
exit /b 1
