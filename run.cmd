@echo off
setlocal

call "%~dp0connect.cmd" %*
exit /b %errorlevel%
