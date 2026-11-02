@echo off
setlocal
cd /d "%~dp0"
title Diagnostico - DrSaygon Video Factory 6.0.2
if not exist ".venv\Scripts\python.exe" (
  echo [ERRO] Ambiente principal nao encontrado. Execute INSTALAR.bat.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\doctor.py
set EXITCODE=%ERRORLEVEL%
echo.
pause
exit /b %EXITCODE%
