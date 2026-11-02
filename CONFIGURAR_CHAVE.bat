@echo off
setlocal
cd /d "%~dp0"
title Configurar chave Gemini - DrSaygon Video Factory
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\configurar_chave.ps1"
echo.
pause
