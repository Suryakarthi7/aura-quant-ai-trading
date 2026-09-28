@echo off
title AURA QUANT - AI Trading Station
cd /d "%~dp0"

echo ========================================================
echo        AURA QUANT - AI INTRADAY TRADING STATION
echo ========================================================
echo.
echo Working Directory: %CD%
echo.
echo [*] Starting Trading Engine Server and connecting NSE feeds...
python -u app.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [Notice] Trying python launcher 'py'...
    py -u app.py
)

echo.
echo ========================================================
echo Server has exited. Press any key to close this window.
echo ========================================================
pause
