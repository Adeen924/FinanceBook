@echo off
title FinanceBook
echo.
echo  ============================================
echo   FinanceBook -- Personal Finance Tracker
echo  ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found.
    echo  Install Python 3.10+ from https://python.org
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit /b
)

if not exist ".venv\Scripts\activate.bat" (
    echo  Creating virtual environment...
    python -m venv .venv
    echo  Done.
    echo.
)

call .venv\Scripts\activate.bat

echo  Checking dependencies...
pip install --prefer-binary -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo  Dependency install failed. Retrying with verbose output...
    pip install --prefer-binary -r requirements.txt
    pause
    exit /b
)

echo  Starting FinanceBook...
echo.
REM App code now lives in app\ -- run it directly. Python puts app\ on sys.path
REM as the script dir, so its internal imports (ui, sheets, ...) resolve, and the
REM running-from-source DB lives next to app\main.py (not frozen => not %APPDATA%).
python app\main.py

if errorlevel 1 (
    echo.
    echo  FinanceBook exited with an error. See above for details.
    pause
)
