@echo off
REM Treasure Scanner — Windows one-click installer / launcher.
REM Run this file by double-clicking it, or from a cmd/PowerShell prompt.
REM First run: creates a virtual environment, installs everything, downloads
REM Chromium for the stealth browser, then asks you to fill in .env.
REM Subsequent runs: just start the scanner.

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ============================================
echo   Treasure Scanner — setup ^& start
echo ============================================
echo.

REM ---- 1. Python check ---------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [FOUT] Python staat niet in je PATH.
    echo Installeer Python 3.11 of nieuwer via https://www.python.org/downloads/
    echo Vink tijdens installatie "Add Python to PATH" aan.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%V in ('python --version 2^>^&1') do set "PYVER=%%V"
echo Python:           %PYVER%

REM ---- 2. Virtual environment -------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo Maak virtual environment in .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [FOUT] Kon .venv niet aanmaken.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
echo Virtualenv:       %CD%\.venv

REM ---- 3. Dependencies ---------------------------------------------------
echo.
echo Installeer/update dependencies ^(eerste keer duurt 2-5 minuten^) ...
python -m pip install --quiet --disable-pip-version-check --upgrade pip
python -m pip install --quiet --disable-pip-version-check -e .
if errorlevel 1 (
    echo [FOUT] pip install mislukt. Scroll omhoog voor de details.
    pause
    exit /b 1
)

REM ---- 4. Patchright Chromium -------------------------------------------
echo Installeer Chromium voor stealth browser ^(skip als al aanwezig^) ...
python -m patchright install chromium
if errorlevel 1 (
    echo [WAARSCHUWING] Chromium install mislukte. Zonder Chromium werkt
    echo Catawiki en optionele stealth-mode niet, maar de rest wel.
    echo Je kan dit later opnieuw proberen met:  python -m patchright install chromium
)

REM ---- 5. .env file ------------------------------------------------------
if not exist ".env" (
    echo.
    echo .env nog niet aanwezig — kopie van .env.example wordt gemaakt.
    copy /Y ".env.example" ".env" >nul
    echo.
    echo ============================================
    echo  VUL EERST .env IN ^(in deze map^):
    echo    - TELEGRAM_BOT_TOKEN  ^(@BotFather in Telegram^)
    echo    - TELEGRAM_CHAT_ID    ^(@userinfobot in Telegram^)
    echo ============================================
    echo.
    echo Open .env in Kladblok en sla op, draai dan start.bat opnieuw.
    notepad .env
    pause
    exit /b 0
)

REM Block accidental start without configured Telegram.
findstr /b /c:"TELEGRAM_BOT_TOKEN=" .env | findstr /v /c:"TELEGRAM_BOT_TOKEN=$" >nul
if errorlevel 1 (
    echo [FOUT] TELEGRAM_BOT_TOKEN is leeg in .env.
    echo Vul de token in en start opnieuw.
    notepad .env
    pause
    exit /b 1
)

REM ---- 6. Data directory -------------------------------------------------
if not exist data mkdir data

REM ---- 7. Start ----------------------------------------------------------
echo.
echo ============================================
echo  Starten ...
echo  Dashboard: http://localhost:8765
echo  Stoppen:   Ctrl+C in dit venster
echo ============================================
echo.

treasure-scanner

echo.
echo Scanner gestopt.
pause
