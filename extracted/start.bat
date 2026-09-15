@echo off
setlocal EnableDelayedExpansion
title Land Record Digitization System
chcp 65001 >nul

echo ============================================================
echo   Intelligent Land Record Digitization ^& Validation System
echo ============================================================
echo.

cd /d "%~dp0"

REM ---- 1. Check Python ----
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.9+ from:
    echo         https://www.python.org/downloads/
    echo         IMPORTANT: tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

REM ---- 2. Check Tesseract OCR ----
where tesseract >nul 2>nul
if %errorlevel% neq 0 (
    echo [WARNING] Tesseract OCR not found on PATH.
    echo           OCR will not work until it is installed.
    echo           Download from: https://github.com/UB-Mannheim/tesseract/wiki
    echo           During install, select the language data you need (Hindi etc.)
    echo           Default install path: C:\Program Files\Tesseract-OCR
    echo.
    set /p CONT=Continue anyway? (y/n): 
    if /i not "!CONT!"=="y" exit /b 1
    echo.
) else (
    echo [OK] Tesseract OCR found.
)

REM ---- 3. Create virtual environment (first run only) ----
if not exist ".venv" (
    echo [1/3] Creating virtual environment...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

REM ---- 4. Install dependencies ----
echo [2/3] Installing Python dependencies (first run may take a few minutes)...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies. Check your internet connection.
    pause
    exit /b 1
)

REM ---- 5. Point pytesseract at the Tesseract install (common paths) ----
set "TESS_PATH="
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set "TESS_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe"
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESS_PATH=C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
if defined TESS_PATH (
    set "PYTESSERACT_PATH=!TESS_PATH!"
    echo [OK] Tesseract path: !TESS_PATH!
)

REM ---- 6. Start the server ----
echo [3/3] Starting server at http://localhost:8000 ...
echo.
echo   Default administrator login:
echo     Email:    admin@landrec.gov.in
echo     Password: Admin@123
echo.
echo   Press Ctrl+C to stop the server.
echo.
start "" http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn landrec.main:app --host 0.0.0.0 --port 8000

pause
