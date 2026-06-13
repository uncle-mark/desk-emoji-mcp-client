@echo off
setlocal

cd /d "%~dp0"
set "VENV_DIR=.venv"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON_CMD=python"
    ) else (
        echo Error: Python 3 was not found. Please install Python 3 and try again.
        pause
        exit /b 1
    )
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating virtual environment in %VENV_DIR% ...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"

echo Upgrading pip ...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip.
    pause
    exit /b 1
)

echo Installing dependencies ...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

echo Checking tkinter ...
python -c "import tkinter"
if errorlevel 1 (
    echo tkinter is not available in this Python installation.
    echo Please install a Python distribution that includes tkinter.
    pause
    exit /b 1
)

echo Starting Desk-Emoji MCP Client ...
python app.py

endlocal
