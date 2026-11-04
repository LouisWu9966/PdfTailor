@echo off
setlocal
cd /d "%~dp0"

rem  NOTE: keep this file pure ASCII. cmd.exe mis-parses batch files that contain
rem  multi-byte characters. Chinese text belongs in README.md / HANDOFF.md.

rem  dotnet/MSBuild localize their own console output to the OS UI language
rem  (zh-TW here). That text is UTF-8 when redirected to a file, but cmd's
rem  "type" reads it back using the active codepage, which is not UTF-8 by
rem  default -> garbled output. Force English diagnostics (ASCII-safe, and
rem  easier to search/paste) and switch the console codepage to UTF-8 as a
rem  backstop for anything else that slips through.
set "DOTNET_CLI_UI_LANGUAGE=en"
set "VSLANG=1033"
chcp 65001 >nul

set "APP=app\PdfTailor\PdfTailor.csproj"
set "OUTDIR=publish"
set "LOG=%~dp0release-log.txt"
set "STEP=%TEMP%\pt-release-step.txt"
set "INTERACTIVE="
set "CMD=%~1"

if not "%CMD%"=="" goto :resolve

set "INTERACTIVE=1"
echo.
echo   PdfTailor
echo   ------------------------------------------------
echo     1  app       compile the WPF shell (Release)
echo     2  engine    build the Python engine into publish\engine
echo     3  publish   engine + single-file PdfTailor.exe into publish\
echo     4  test      run the engine tests
echo     5  run       start the app from source (engine from source too)
echo     6  clean     remove bin / obj / build folders and publish\
echo.
set /p "CMD=Choose 1-6 (or type the name): "
echo.

:resolve
if "%CMD%"=="1" set "CMD=app"
if "%CMD%"=="2" set "CMD=engine"
if "%CMD%"=="3" set "CMD=publish"
if "%CMD%"=="4" set "CMD=test"
if "%CMD%"=="5" set "CMD=run"
if "%CMD%"=="6" set "CMD=clean"

echo PdfTailor release log > "%LOG%"
echo command=%CMD%  %DATE% %TIME% >> "%LOG%"

if /i "%CMD%"=="app"     goto :run_app
if /i "%CMD%"=="engine"  goto :run_engine
if /i "%CMD%"=="publish" goto :run_publish
if /i "%CMD%"=="test"    goto :run_test
if /i "%CMD%"=="run"     goto :run_run
if /i "%CMD%"=="clean"   goto :run_clean
echo [ERROR] unknown command: %CMD%
echo Usage: release.cmd [app ^| engine ^| publish ^| test ^| run ^| clean]
goto :fail

:run_app
call :need_dotnet || goto :fail
call :step_app || goto :fail
goto :done

:run_engine
call :step_engine || goto :fail
goto :done

:run_publish
call :need_dotnet || goto :fail
call :step_engine || goto :fail
call :step_publish || goto :fail
goto :done

:run_test
call :step_test || goto :fail
goto :done

:run_run
call :need_dotnet || goto :fail
echo Starting PdfTailor from source (the engine is started from engine\ with Python)...
dotnet run --project "%APP%" -c Debug
goto :done

:run_clean
echo The following will be deleted:
echo   app\PdfTailor\bin  app\PdfTailor\obj  engine\build  engine\dist  %OUTDIR%
set /p "CONFIRM=Delete them? (y/N): "
if /i not "%CONFIRM%"=="y" echo Cancelled.& goto :fail
if exist "app\PdfTailor\bin" rd /s /q "app\PdfTailor\bin"
if exist "app\PdfTailor\obj" rd /s /q "app\PdfTailor\obj"
if exist "engine\build" rd /s /q "engine\build"
if exist "engine\dist" rd /s /q "engine\dist"
if exist "%OUTDIR%" rd /s /q "%OUTDIR%"
echo Cleaned.
goto :done


:need_dotnet
where dotnet > nul 2>&1
if errorlevel 1 (
  echo [ERROR] dotnet not found on PATH. Install .NET SDK 9.
  exit /b 1
)
exit /b 0

:step_app
echo.
echo ================ BUILD APP (Release) ================
echo. >> "%LOG%"
echo ===== BUILD APP ===== >> "%LOG%"
dotnet build "%APP%" -c Release --nologo > "%STEP%" 2>&1
set "RC=%ERRORLEVEL%"
type "%STEP%"
type "%STEP%" >> "%LOG%"
exit /b %RC%

:step_engine
echo.
echo ================ BUILD ENGINE ================
echo   (first run creates engine\.venv-build and downloads packages)
echo. >> "%LOG%"
echo ===== BUILD ENGINE ===== >> "%LOG%"
call engine\build-engine.cmd
set "RC=%ERRORLEVEL%"
if exist "engine\build-engine-log.txt" type "engine\build-engine-log.txt" >> "%LOG%"
exit /b %RC%

:step_publish
echo.
echo ================ PUBLISH APP ================
echo. >> "%LOG%"
echo ===== PUBLISH APP ===== >> "%LOG%"
dotnet publish "%APP%" -c Release -r win-x64 --self-contained true -o "%OUTDIR%" --nologo > "%STEP%" 2>&1
set "RC=%ERRORLEVEL%"
type "%STEP%"
type "%STEP%" >> "%LOG%"
if not "%RC%"=="0" exit /b %RC%
if not exist "%OUTDIR%\PdfTailor.exe" (
  echo [ERROR] publish reported success but "%OUTDIR%\PdfTailor.exe" is missing.
  exit /b 1
)
if not exist "%OUTDIR%\engine\pdftailor-engine.exe" (
  echo [ERROR] "%OUTDIR%\engine\pdftailor-engine.exe" is missing - run: release.cmd engine
  exit /b 1
)
exit /b 0

:step_test
echo.
echo ================ ENGINE TESTS ================
if not exist "engine\.venv-build\Scripts\python.exe" (
  echo Build venv missing - building the engine once first...
  call engine\build-engine.cmd || exit /b 1
)
"engine\.venv-build\Scripts\python.exe" -m pytest engine\tests -q -W ignore::DeprecationWarning > "%STEP%" 2>&1
set "RC=%ERRORLEVEL%"
type "%STEP%"
type "%STEP%" >> "%LOG%"
exit /b %RC%


:done
echo.
echo ------------------------------------------------
echo   OK: %CMD%
if /i "%CMD%"=="publish" echo   EXE: %~dp0%OUTDIR%\PdfTailor.exe
echo   log: %LOG%
echo ------------------------------------------------
if defined INTERACTIVE pause
exit /b 0

:fail
echo.
echo ------------------------------------------------
echo   FAILED: %CMD%
echo   full output: %LOG%
echo ------------------------------------------------
if defined INTERACTIVE pause
exit /b 1
