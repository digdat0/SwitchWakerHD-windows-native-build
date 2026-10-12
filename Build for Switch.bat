@echo off
rem SwitchWakerHD: opens the "Build for Switch" window (switch_builder.py).
rem Needs Python 3.8 or newer from python.org (with tcl/tk, the default); "python3" from the Microsoft Store
rem shortcut is not enough.
setlocal
cd /d "%~dp0"
set SCRIPT=switch_builder.py

rem the Python launcher (python.org installs it): the newest Python 3 that has tkinter
py -3 -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
    start "" pyw -3 "%SCRIPT%"
    exit /b 0
)
rem else python on PATH (the Microsoft Store shortcut fails this check)
python -c "import sys, tkinter; sys.exit(sys.version_info < (3, 8))" >nul 2>&1
if not errorlevel 1 (
    start "" pythonw "%SCRIPT%"
    exit /b 0
)
echo.
echo  Python 3 was not found (or it has no tkinter).
echo  Install it from https://www.python.org/downloads/ (keep "tcl/tk" and "py launcher" ticked),
echo  then start "Build for Switch.bat" again.
echo.
pause
exit /b 1
