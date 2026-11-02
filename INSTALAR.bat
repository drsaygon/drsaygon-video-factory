@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Instalacao - DrSaygon Video Factory 6.0.2
color 0F

echo ================================================================
echo INSTALADOR - DRSAYGON VIDEO FACTORY 6.0.2
echo ================================================================
echo.
set "PYEXE="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys" >nul 2>nul && set "PYEXE=py -3.12"
  if not defined PYEXE py -3.11 -c "import sys" >nul 2>nul && set "PYEXE=py -3.11"
  if not defined PYEXE py -3.13 -c "import sys" >nul 2>nul && set "PYEXE=py -3.13"
)
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  echo [ERRO] Python 3.11/3.12/3.13 nao foi encontrado.
  echo Instale Python para Windows e execute este arquivo novamente.
  pause
  exit /b 1
)
echo [1/7] Python encontrado: %PYEXE%

where ffmpeg >nul 2>nul
if not errorlevel 1 (
  echo [2/7] FFmpeg ja esta disponivel no Windows.
) else if exist "tools\ffmpeg\bin\ffmpeg.exe" (
  echo [2/7] FFmpeg portatil ja esta instalado.
) else (
  echo [2/7] Instalando FFmpeg portatil dentro do programa...
  %PYEXE% "scripts\install_ffmpeg.py" || goto :erro
)

if not exist ".venv\Scripts\python.exe" (
  echo [3/7] Criando ambiente principal...
  %PYEXE% -m venv .venv || goto :erro
) else echo [3/7] Ambiente principal ja existe.

echo [4/7] Instalando/atualizando dependencias principais...
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :erro
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :erro

echo [5/7] Motor de voz local (opcional)...
if exist ".venv_voice\Scripts\python.exe" (
  ".venv_voice\Scripts\python.exe" -c "import torch, pocket_tts" >nul 2>nul
  if not errorlevel 1 (
    echo [OK] Motor de voz local pronto.
  ) else (
    echo [AVISO] Motor de voz incompleto. Reinstalando...
    call INSTALAR_VOZ_LOCAL.bat
    if errorlevel 1 echo [AVISO] Voz local nao instalada. Audio original e narracao manual continuam disponiveis.
  )
) else (
  call INSTALAR_VOZ_LOCAL.bat
  if errorlevel 1 echo [AVISO] Voz local nao instalada. Audio original e narracao manual continuam disponiveis.
)

echo [6/7] Preparando pastas do programa...
if not exist "projetos" mkdir "projetos"
if not exist "voice_profiles" mkdir "voice_profiles"

if not exist ".env" (
  echo [7/7] Configure a chave Gemini para novas analises IA.
  call CONFIGURAR_CHAVE.bat
  if errorlevel 1 echo [AVISO] Chave nao configurada agora. Voce pode configurar depois.
) else echo [7/7] Chave/configuracao existente preservada.

echo.
echo ================================================================
echo INSTALACAO DO NUCLEO CONCLUIDA
echo ================================================================
echo Execute DIAGNOSTICO.bat e depois INICIAR.bat.
echo.
pause
exit /b 0

:erro
echo.
echo [ERRO] A instalacao principal nao foi concluida.
echo Nao apague nada. Copie a mensagem acima para diagnostico.
echo.
pause
exit /b 1
