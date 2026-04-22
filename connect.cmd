@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0connect.ps1" %*
exit /b %errorlevel%
