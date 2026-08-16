@echo off
setlocal

if defined VPS_SSH_LAUNCHER_POWERSHELL (
  set "POWERSHELL_EXE=%VPS_SSH_LAUNCHER_POWERSHELL%"
) else (
  if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" (
    set "POWERSHELL_EXE=%ProgramFiles%\PowerShell\7\pwsh.exe"
  )
  if not defined POWERSHELL_EXE if exist "C:\Program Files\PowerShell\7\pwsh.exe" (
    set "POWERSHELL_EXE=C:\Program Files\PowerShell\7\pwsh.exe"
  )
  if not defined POWERSHELL_EXE (
    for /f "delims=" %%I in ('where pwsh.exe 2^>nul') do if not defined POWERSHELL_EXE set "POWERSHELL_EXE=%%I"
  )
  if not defined POWERSHELL_EXE (
    echo ERROR: PowerShell 7 ^(pwsh.exe^) is required. 1>&2
    echo Install PowerShell 7 or set VPS_SSH_LAUNCHER_POWERSHELL to an approved launcher. 1>&2
    exit /b 2
  )
)

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0connect.ps1" %*
exit /b %errorlevel%
