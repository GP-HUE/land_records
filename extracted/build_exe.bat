@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Build LandRecordSystem.exe
cd /d "%~dp0"

REM ---- Double-click safety ----
REM A plain double-clicked .bat window closes the INSTANT the script
REM exits (success, error, or crash) - which is how build failures used
REM to "disappear" without a trace. Re-launch ourselves in a PERSISTENT
REM console (cmd /k keeps the window open no matter what happens).
if not defined LR_BUILD_WINDOW (
    set "LR_BUILD_WINDOW=1"
    start "" cmd /k ""%~f0" %*
    if errorlevel 1 (
        echo [ERROR] Could not open the persistent build window.
        echo         Open a terminal in this folder and run:  build_exe.bat
        pause
    )
    exit /b
)

echo ============================================================
echo   Build LandRecordSystem  - one-folder build, NO temp files,
echo   NO temp-cleanup popups, fast start
echo ============================================================
echo.

REM ---- 1. Python check ----
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.9+ is required to BUILD the exe.
    echo         Install from https://www.python.org/downloads/
    echo         IMPORTANT: tick "Add Python to PATH".
    pause
    exit /b 1
)
REM Verify Python is 3.9+ (robust exit-code check, no fragile text parsing)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.9 or newer is required to build the executable.
    for /f "tokens=1" %%v in ('python -c "import sys;print(sys.version.split()[0])" 2^>nul') do echo         Detected Python: %%v
    echo         Install from https://www.python.org/downloads/
    echo         IMPORTANT: tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

REM ---- 2. Virtual environment + dependencies ----
echo [1/4] Setting up build environment...
if not exist "buildenv" (
    python -m venv buildenv
    if errorlevel 1 (
        echo [ERROR] Could not create virtual environment.
        pause & exit /b 1
    )
)
call buildenv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
REM IMPORTANT: --upgrade forces the NEWEST PyInstaller even if an old one
REM is already in buildenv. Builds made with PyInstaller older than 6.11.1
REM show the "Failed to remove temporary directory" popup on exit, because
REM only 6.11.1+ has the temp-dir cleanup retry loop.
python -m pip install --quiet --upgrade "pyinstaller>=6.11.1"
if errorlevel 1 (
    echo [ERROR] Dependency install failed. Check your internet connection.
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('python -m PyInstaller --version 2^>nul') do echo       PyInstaller version: %%v

REM ---- 3. Tesseract (required at build time so it can be bundled) ----
REM   The exe bundles whichever Tesseract this step finds, so we REQUIRE
REM   version >= 5.5. Many PCs still have the old 5.4.0 installer. The
REM   current official installers are published by the main
REM   tesseract-ocr/tesseract project. If the installed copy is older
REM   than 5.5, the official 5.5.3 installer is downloaded and used.
REM   The version check is done by check_tesseract.py - it prints the
REM   detected version and exits 0 only when the version is >= 5.5.
REM   NOTE for maintainers: keep echo lines in this script free of
REM   parentheses and greater-than signs, and keep command lines free of
REM   parentheses inside blocks - cmd's parser is fragile about all of it.
echo [2/4] Checking Tesseract OCR - need version 5.5 or newer
set "TESSDIR="
set "TESSOK=0"
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set "TESSDIR=C:\Program Files\Tesseract-OCR"
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESSDIR=C:\Program Files (x86)\Tesseract-OCR"
if defined TESSDIR (
    echo       Found Tesseract at: !TESSDIR!
    python check_tesseract.py "!TESSDIR!\tesseract.exe"
    if not errorlevel 1 set "TESSOK=1"
)
if "!TESSOK!"=="1" goto :tess_ok
echo       Too old or not found - installing the official Tesseract 5.5.3
REM Warn early if we are not admin - the silent upgrade below needs it
net session >nul 2>&1
if errorlevel 1 (
    echo       NOTE: Not running as administrator. The silent upgrade
    echo       may be blocked by UAC. If the check below fails, re-run
    echo       this script with "Run as administrator".
)
powershell -NoProfile -Command "Invoke-WebRequest 'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe' -OutFile 'tesseract_setup.exe'"
if exist "tesseract_setup.exe" goto :tess_download_ok
echo       Primary download failed - trying the latest-release fallback
powershell -NoProfile -Command "Invoke-RestMethod 'https://api.github.com/repos/tesseract-ocr/tesseract/releases/latest' | Select-Object -ExpandProperty assets | Where-Object { $_.name -match 'w64-setup' } | Select-Object -First 1 | ForEach-Object { Invoke-WebRequest $_.browser_download_url -OutFile 'tesseract_setup.exe' }"
:tess_download_ok
if not exist "tesseract_setup.exe" (
    echo [ERROR] The Tesseract 5.5.3 installer download failed.
    echo         Check the internet connection, then re-run this script.
    pause
    exit /b 1
)
echo       Running silent install - may ask for admin rights
tesseract_setup.exe /SILENT
timeout /t 25 /nobreak >nul
del tesseract_setup.exe
set "TESSDIR="
set "TESSOK=0"
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set "TESSDIR=C:\Program Files\Tesseract-OCR"
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESSDIR=C:\Program Files (x86)\Tesseract-OCR"
if defined TESSDIR (
    echo       Tesseract after install:
    python check_tesseract.py "!TESSDIR!\tesseract.exe"
    if not errorlevel 1 set "TESSOK=1"
)
echo.
echo       This window stays open - press any key to continue with the exe build
pause >nul
:tess_ok
if not defined TESSDIR (
    echo [ERROR] Tesseract OCR could not be installed automatically.
    echo         Install it manually from https://github.com/UB-Mannheim/tesseract/wiki
    echo         then re-run this script.
    pause
    exit /b 1
)
REM Final safety net: never let an old Tesseract get bundled into the exe
if "!TESSOK!"=="0" (
    echo [ERROR] The Tesseract found here is older than 5.5 - this build needs 5.5 or newer.
    echo         Re-run with ADMINISTRATOR rights, or install 5.5.3 manually
    echo         from https://github.com/UB-Mannheim/tesseract/wiki and re-run.
    pause
    exit /b 1
)
echo       Tesseract OK - bundling from: !TESSDIR!

REM ---- 3b. Language packs ----
REM   The official Windows Tesseract installer ships ONLY the English pack,
REM   but this app OCRs 11 languages. Download the official fast models
REM   (tesseract-ocr/tessdata_fast) into a LOCAL "packs" folder - NOT into
REM   C:\Program Files, which needs admin rights and fails with
REM   "Access to the path is denied" for normal users. After the exe build
REM   finishes, the packs are copied into dist\LandRecordSystem (step 4b).
REM   Already downloaded packs are skipped, so re-runs are quick.
echo [2b/4] Fetching OCR language packs - 10 Indic languages
if not exist "packs" mkdir "packs"
for %%L in (hin ben tam tel guj pan ori kan mal urd) do (
    if not exist "packs\%%L.traineddata" (
        echo       Downloading %%L.traineddata
        powershell -NoProfile -Command "Invoke-WebRequest 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/%%L.traineddata' -OutFile 'packs\%%L.traineddata'"
    )
)
python -c "import os,sys; m=[l for l in ('hin','ben','tam','tel','guj','pan','ori','kan','mal','urd') if not (os.path.isfile(os.path.join('packs',l+'.traineddata')) and os.path.getsize(os.path.join('packs',l+'.traineddata'))>100000)]; print('       Language pack check:', 'ALL 10 OK' if not m else 'MISSING or too small: '+' '.join(m)); sys.exit(1 if m else 0)"
if errorlevel 1 (
    echo [ERROR] Some language packs did not download properly.
    echo         Check the internet connection, then re-run build_exe.bat -
    echo         already downloaded packs are kept, only missing ones are fetched.
    pause
    exit /b 1
)

REM ---- 4. Build the executable (ONE-FOLDER mode) ----
REM   --onedir    : the app is a FOLDER, not a self-extracting file.
REM                 There is NO temp extraction and NO temp cleanup, so
REM                 the "Failed to remove temporary directory" popup is
REM                 impossible, and startup is fast (no 300 MB unpack).
REM                 To send the app to someone: ZIP the dist\LandRecordSystem
REM                 folder and send the zip (they unzip and run the exe).
REM   --noconsole : NO black terminal window when the exe is double-clicked.
REM                 The website opens directly in the browser. All server
REM                 output goes to  dist\LandRecordSystem\data\server.log
echo [3/4] Building executable - one-folder mode, takes a few minutes
REM Remove a leftover single-file build so nobody runs the old exe by mistake
if exist "dist\LandRecordSystem.exe" (
    echo       Removing old single-file dist\LandRecordSystem.exe
    del "dist\LandRecordSystem.exe"
)
python -m PyInstaller --noconfirm --clean --onedir --noconsole --name LandRecordSystem ^
    --add-data "landrec/static;landrec/static" ^
    --add-data "samples;samples" ^
    --add-data "check_tesseract.py;." ^
    --add-data "!TESSDIR!;tesseract" ^
    --hidden-import "uvicorn.logging" ^
    --hidden-import "uvicorn.loops" ^
    --hidden-import "uvicorn.loops.auto" ^
    --hidden-import "uvicorn.protocols" ^
    --hidden-import "uvicorn.protocols.http" ^
    --hidden-import "uvicorn.protocols.http.auto" ^
    --hidden-import "uvicorn.protocols.websockets" ^
    --hidden-import "uvicorn.protocols.websockets.auto" ^
    --hidden-import "uvicorn.lifespan" ^
    --hidden-import "uvicorn.lifespan.on" ^
    --hidden-import "fitz" ^
    --hidden-import "pymupdf" ^
    --collect-submodules "pymupdf" ^
    run.py
if errorlevel 1 (
    echo [ERROR] Build failed. See the messages above.
    pause
    exit /b 1
)

REM ---- 4b. Bundle the language packs into the finished exe folder ----
REM   The installer's Tesseract only ships English, so the 10 Indic packs
REM   are copied into the BUILT folder now. This writes only inside dist\,
REM   so no admin rights are needed - and it overwrites any partial pack
REM   from the earlier failed attempt, if present.
REM   NOTE: PyInstaller 6.x one-folder builds put bundled files under
REM   dist\LandRecordSystem\_internal\  - that is where the app (via
REM   sys._MEIPASS) looks for tesseract and its tessdata. Older PyInstaller
REM   versions used dist\LandRecordSystem\ directly, so handle both.
if exist "dist\LandRecordSystem\_internal\tesseract" (
    if not exist "dist\LandRecordSystem\_internal\tesseract\tessdata" mkdir "dist\LandRecordSystem\_internal\tesseract\tessdata"
    copy /Y "packs\*.traineddata" "dist\LandRecordSystem\_internal\tesseract\tessdata" >nul
) else (
    if not exist "dist\LandRecordSystem\tesseract\tessdata" mkdir "dist\LandRecordSystem\tesseract\tessdata"
    copy /Y "packs\*.traineddata" "dist\LandRecordSystem\tesseract\tessdata" >nul
)
python -c "import os; cands=[r'dist\LandRecordSystem\_internal\tesseract\tessdata', r'dist\LandRecordSystem\tesseract\tessdata']; d=next((c for c in cands if os.path.isdir(c)), None); p=sorted(f for f in os.listdir(d) if f.endswith('.traineddata')) if d else []; print('       Bundled language packs:', ', '.join(p) if p else 'NONE - the tessdata folder was not found!')"

REM ---- 5. Done ----
echo [4/4] Done!
echo.
echo   SUCCESS: dist\LandRecordSystem\  folder has been created.
echo   Run it from:  dist\LandRecordSystem\LandRecordSystem.exe
echo.
echo   To send the app to a teammate:
echo     1. Select the whole  dist\LandRecordSystem  folder
echo     2. Right-click, choose "Compress to ZIP" or use any zip tool
echo     3. Send the zip - they unzip it anywhere and double-click
echo        LandRecordSystem.exe. No Python/Tesseract install needed.
echo.
echo   NOTE 1: Windows SmartScreen may show "Windows protected your PC"
echo           on first run. That is NORMAL for a free, unsigned exe.
echo           Click [More info]  then  [Run anyway].
echo.
echo   NOTE 2: Your database is stored in
echo           dist\LandRecordSystem\data\  - NEVER delete the folder,
echo           deleting it deletes all accounts and records.
echo.
echo   NOTE 3: One-folder build means NO temp files and no temp-cleanup
echo           popups, and much faster startup. Do NOT run old
echo           single-file builds made by older scripts.
echo.
pause
