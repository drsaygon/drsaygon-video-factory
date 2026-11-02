@echo off
setlocal
cd /d "%~dp0"
title Release Gate v6.0.2 - DrSaygon Video Factory
if not exist ".venv\Scripts\python.exe" (
  echo [ERRO] Execute INSTALAR.bat primeiro.
  pause
  exit /b 1
)
set PY=.venv\Scripts\python.exe

echo ============================================================================== 
echo DRSAYGON VIDEO FACTORY 6.0.2 - RELEASE GATE LOCAL
echo ============================================================================== 
%PY% scripts\release_gate_v602.py
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo [ERRO] Release Gate 6.0.2 falhou. Nao use este build ate revisar a saida acima.
) else (
  echo [OK] VIDEO FACTORY 6.0.2 APROVADO PELO RELEASE GATE COMPLETO.
)
pause
exit /b %RC%
