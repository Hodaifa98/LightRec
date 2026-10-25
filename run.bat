@echo off
rem Create the virtual environment on first run, then start the app.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Setting up LightRec for the first time...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo Could not create a virtual environment. Install Python 3.9+ from python.org and try again.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
)
start "" ".venv\Scripts\pythonw.exe" LightRec.py %*
endlocal
