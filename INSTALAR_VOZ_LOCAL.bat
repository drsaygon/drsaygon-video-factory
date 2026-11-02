@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYEXE="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys" >nul 2>nul && set "PYEXE=py -3.12"
  if not defined PYEXE py -3.11 -c "import sys" >nul 2>nul && set "PYEXE=py -3.11"
  if not defined PYEXE py -3.13 -c "import sys" >nul 2>nul && set "PYEXE=py -3.13"
)
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  echo [ERRO] Python nao encontrado.
  exit /b 1
)
if not exist ".venv_voice\Scripts\python.exe" (
  echo Criando ambiente exclusivo para a voz local...
  %PYEXE% -m venv .venv_voice || exit /b 1
)
echo Atualizando pip do motor de voz...
".venv_voice\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel || exit /b 1
echo Instalando PyTorch CPU-only. A GTX 750 Ti nao e necessaria para este motor...
".venv_voice\Scripts\python.exe" -m pip install --upgrade "torch>=2.5.0" --index-url https://download.pytorch.org/whl/cpu || exit /b 1
echo Instalando Kyutai Pocket TTS 2.1.0...
".venv_voice\Scripts\python.exe" -m pip install -r requirements_voice.txt || exit /b 1
echo Verificando importacao no seu processador...
".venv_voice\Scripts\python.exe" -c "import torch, pocket_tts; print('[OK] PyTorch', torch.__version__, '- Pocket TTS instalado - CPU:', torch.get_num_threads(), 'threads')" || exit /b 1
echo [OK] Motor de voz local instalado.
exit /b 0
