@echo off
rem Launches FastUpdater. Only checks that Python 3 (with Tkinter) is installed; does not install anything.
setlocal
cd /d "%~dp0"
set "PYW="

call :find_python
if defined PYW goto run

echo Python 3 with Tkinter was not found on this computer.
echo Install Python 3 from https://www.python.org/downloads/ ^(tick "tcl/tk and IDLE"^) and run this file again.
pause
exit /b 1

:run
start "" "%PYW%" "%~dp0updater.py"
exit /b 0

rem ---- Looks for pythonw.exe with working tkinter; result goes to PYW ----
:find_python
py -3 -c "import tkinter" >nul 2>nul
if not errorlevel 1 (
    for /f "usebackq delims=" %%i in (`py -3 -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"`) do set "PYW=%%i"
    if defined PYW exit /b 0
)
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%d\pythonw.exe" "%%d\python.exe" -c "import tkinter" >nul 2>nul && set "PYW=%%d\pythonw.exe"
exit /b 0
