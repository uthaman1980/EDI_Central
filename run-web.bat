@echo off
setlocal
if exist "%~dp0run-web.local.bat" call "%~dp0run-web.local.bat"
cd /d "%~dp0"
if errorlevel 1 (
    echo Could not open the application folder.
    pause
    exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
    echo Python was not found. Install Python 3.10 or newer and add it to PATH.
    pause
    exit /b 1
)

echo Starting Radius EDI Central...
echo Open http://127.0.0.1:8088/central after the server starts.
echo Press Ctrl+C to stop it.
echo.
python -m niagara_edi --dashboard --observe --config "%~dp0config.json" --state "%~dp0state\orders.sqlite3" --debug
if errorlevel 1 (
    echo.
    echo The application stopped with an error. Check the message above.
    pause
    exit /b 1
)
endlocal
