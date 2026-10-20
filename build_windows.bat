@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 exit /b 1
set "RC=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "NO_PAUSE="
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"
if not exist ".build-cache" mkdir ".build-cache"
if not exist ".build-cache" goto failed
set "LOG=%CD%\.build-cache\build_windows.log"
>"%LOG%" echo ExifTool Archaeo Windows build

echo [1/4] Checking 64-bit Python 3.10+...
if exist ".venv-build\Scripts\python.exe" goto check_venv
py -3 -c "import sys,struct; sys.exit(0 if sys.version_info >= (3,10) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
if not errorlevel 1 goto use_py
python -c "import sys,struct; sys.exit(0 if sys.version_info >= (3,10) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
if not errorlevel 1 goto use_python
echo Python was not found. Install 64-bit Python 3.10+ with Tcl/Tk and pip.
echo Official installer: https://www.python.org/downloads/windows/
goto failed

:use_py
py -3 -m venv ".venv-build" >>"%LOG%" 2>&1
if errorlevel 1 goto failed
goto check_venv

:use_python
python -m venv ".venv-build" >>"%LOG%" 2>&1
if errorlevel 1 goto failed

:check_venv
set "PY=%CD%\.venv-build\Scripts\python.exe"
"%PY%" -c "import sys,struct,tkinter; assert sys.version_info >= (3,10) and struct.calcsize('P') == 8; tkinter.Tcl()" >>"%LOG%" 2>&1
if errorlevel 1 goto broken_venv
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo [2/4] Installing build packages in .venv-build...
echo Build log: "%LOG%"
"%PY%" -m pip install --disable-pip-version-check --upgrade pip >>"%LOG%" 2>&1
if errorlevel 1 goto failed
"%PY%" -m pip install --disable-pip-version-check -r requirements-build.txt >>"%LOG%" 2>&1
if errorlevel 1 goto failed
echo [3/4] Preparing ExifTool, running tests and building the portable EXE...
echo Details are being written to the build log above.
"%PY%" -u build_windows.py >>"%LOG%" 2>&1
if errorlevel 1 goto failed
echo [4/4] SUCCESS: dist\ExifToolArchaeo.exe
echo ExifTool is embedded. No separate engine or PATH setting is needed.
echo Self-test report: .build-cache\self-test.json
set "RC=0"
goto done

:broken_venv
echo The build environment is invalid or Tcl/Tk is missing.
echo Repair Python, remove only .venv-build, and rerun this BAT.
goto failed

:failed
echo.
echo BUILD FAILED. No successful build is being reported.
echo Close a running ExifToolArchaeo.exe before rebuilding.
if defined LOG echo See the full error in "%LOG%".
if defined LOG if exist "%LOG%" type "%LOG%"

:done
if not defined NO_PAUSE pause
popd
exit /b %RC%
