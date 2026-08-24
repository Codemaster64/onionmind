@echo off
setlocal
title Onionmind portable readiness
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0onionmind-bootstrap.ps1" %*
set "ONIONMIND_BOOTSTRAP_EXIT=%ERRORLEVEL%"
echo.
if not "%ONIONMIND_BOOTSTRAP_EXIT%"=="0" echo Onionmind readiness exited with code %ONIONMIND_BOOTSTRAP_EXIT%.
echo Press any key to close this window.
pause >nul
exit /b %ONIONMIND_BOOTSTRAP_EXIT%
