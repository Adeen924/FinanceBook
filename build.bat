@echo off
setlocal enabledelayedexpansion
title FinanceBook -- Build

echo.
echo ====================================================
echo   FinanceBook -- building distributable
echo ====================================================
echo.

REM Activate venv and make sure PyInstaller is installed
call .venv\Scripts\activate.bat
pip install --prefer-binary -q pyinstaller
if errorlevel 1 ( echo ERROR: pip failed & pause & exit /b 1 )

REM Read the version marker (drives the asset filenames)
set /p VER=<app\VERSION
echo Building FinanceBook v%VER%
echo.

REM ── 1. Freeze the THIN launcher (bundles Python + all deps, not app code) ──
echo Running PyInstaller -- takes 3-5 min on first run...
echo.
pyinstaller FinanceBook.spec --noconfirm
if errorlevel 1 ( echo ERROR: PyInstaller failed & pause & exit /b 1 )

REM ── 2. Drop the plain app/ folder next to the launcher ─────────────────────
echo Copying app\ into the build...
if exist "dist\FinanceBook\app" rmdir /S /Q "dist\FinanceBook\app"
robocopy "app" "dist\FinanceBook\app" /E /NFL /NDL /NJH /NJS /NP >NUL
if %ERRORLEVEL% GEQ 8 ( echo ERROR: copying app\ failed & pause & exit /b 1 )

REM ── 3. Package the two release assets ──────────────────────────────────────
echo Zipping...
if not exist dist mkdir dist
if exist "dist\app-%VER%.zip" del "dist\app-%VER%.zip"
if exist "dist\FinanceBook-%VER%.zip" del "dist\FinanceBook-%VER%.zip"

REM app payload (one-click updates) -- contents of app\ at the zip root
powershell -NoProfile -Command "Compress-Archive -Path 'app\*' -DestinationPath 'dist\app-%VER%.zip'"
if errorlevel 1 ( echo ERROR: app payload zip failed & pause & exit /b 1 )

REM full download (first installs + runtime-change fallback)
powershell -NoProfile -Command "Compress-Archive -Path 'dist\FinanceBook\*' -DestinationPath 'dist\FinanceBook-%VER%.zip'"
if errorlevel 1 ( echo ERROR: full zip failed & pause & exit /b 1 )

echo.
echo ====================================================
echo   DONE!  (v%VER%)
echo   dist\FinanceBook-%VER%.zip   ^<- full download / first install
echo   dist\app-%VER%.zip           ^<- one-click update payload
echo ====================================================
echo.
echo Tip: normally you don't build by hand -- push a tag (git tag v%VER%
echo      ^&^& git push origin v%VER%) and GitHub Actions builds + releases.
echo.
pause
