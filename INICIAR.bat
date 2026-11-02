@echo off
setlocal
cd /d "%~dp0"
title DrSaygon Video Factory 6.0.2
if not exist ".venv\Scripts\python.exe" (
  echo [ERRO] Execute INSTALAR.bat primeiro.
  pause
  exit /b 1
)
if not exist ".env" (
  echo [AVISO] Arquivo .env ausente. Novas analises com Gemini exigirao CONFIGURAR_CHAVE.bat.
)
echo ================================================================
echo DRSAYGON VIDEO FACTORY 6.0.2
echo Conteudo primeiro ^| audio flexivel ^| continuidade ^| Master + entrega
echo ================================================================
echo O navegador abrira automaticamente.
echo O motor de voz local e opcional para projetos de audio original.
echo Mantenha esta janela aberta enquanto o servidor estiver em uso.
echo ================================================================
echo.
".venv\Scripts\python.exe" scripts\launcher.py
