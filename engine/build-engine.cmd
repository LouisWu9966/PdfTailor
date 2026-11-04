@echo off
rem ------------------------------------------------------------------
rem  Builds the PdfTailor engine (Python) into  ..\publish\engine\
rem  Needs: Python 3.11+ on PATH (py launcher or python.exe)
rem  Optional: Tesseract-OCR installed (copied next to the engine)
rem  Log: engine\build-engine-log.txt
rem ------------------------------------------------------------------
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
set "LOG=%~dp0build-engine-log.txt"
set "OUT=%~dp0..\publish\engine"
echo [%date% %time%] build start > "%LOG%"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo ERROR: Python 3 not found. Install Python 3.11+ and tick "Add to PATH".
  echo ERROR: python not found >> "%LOG%"
  exit /b 1
)

if not exist ".venv-build\Scripts\python.exe" (
  echo Creating build venv...
  %PY% -m venv .venv-build >> "%LOG%" 2>&1 || goto :fail
)
set "VPY=%~dp0.venv-build\Scripts\python.exe"

echo Installing packages (first run takes a few minutes)...
"%VPY%" -m pip install --upgrade pip >> "%LOG%" 2>&1
"%VPY%" -m pip install -r requirements.txt pyinstaller >> "%LOG%" 2>&1 || goto :fail

echo Running tests...
"%VPY%" -m pytest tests -q -x -W ignore::DeprecationWarning >> "%LOG%" 2>&1
if errorlevel 1 (
  echo WARNING: some tests failed, see build-engine-log.txt. Building anyway.
)

echo Building engine...
"%VPY%" -m PyInstaller --noconfirm --clean --onedir --console ^
  --name pdftailor-engine ^
  --distpath "%~dp0dist" --workpath "%~dp0build" ^
  --collect-all pypdfium2 --collect-all pypdfium2_raw ^
  --collect-data reportlab --collect-submodules reportlab ^
  --collect-submodules fontTools ^
  --collect-data docx --collect-submodules docx --collect-submodules lxml ^
  run_engine.py >> "%LOG%" 2>&1 || goto :fail

if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%" >nul 2>&1
xcopy /e /i /q /y "dist\pdftailor-engine" "%OUT%" >> "%LOG%" 2>&1 || goto :fail

rem ---- bundle Tesseract if it is installed --------------------------
rem  Priority: vendor\tesseract (pre-staged offline copy, see vendor\README.md)
rem            > installed Tesseract-OCR (Program Files / LocalAppData)
set "TESS="
if exist "%~dp0..\vendor\tesseract\tesseract.exe" set "TESS=%~dp0..\vendor\tesseract"
if not defined TESS if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESS=%ProgramFiles%\Tesseract-OCR"
if not defined TESS if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" set "TESS=%LOCALAPPDATA%\Programs\Tesseract-OCR"
if defined TESS (
  echo Copying Tesseract from "%TESS%"...
  xcopy /e /i /q /y "%TESS%" "%OUT%\tesseract" >> "%LOG%" 2>&1
  if not exist "%OUT%\tesseract\tessdata\chi_tra.traineddata" (
    echo WARNING: chi_tra.traineddata missing - Traditional Chinese OCR will not work.
    echo          Download it from github.com/tesseract-ocr/tessdata_best into "%OUT%\tesseract\tessdata".
  )
) else (
  echo WARNING: Tesseract-OCR not found. Scanned pages cannot be read until it is installed
  echo          ^(UB Mannheim build, with Chinese Traditional^) or copied to "%OUT%\tesseract".
  echo          For firewalled machines: stage it once under "vendor\tesseract\" instead - see vendor\README.md.
)

echo.
echo Engine built: "%OUT%\pdftailor-engine.exe"
echo [%date% %time%] build ok >> "%LOG%"
exit /b 0

:fail
echo.
echo BUILD FAILED - see "%LOG%"
echo [%date% %time%] build failed >> "%LOG%"
exit /b 1
