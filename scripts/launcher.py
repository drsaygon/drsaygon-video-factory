from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

VOICE_PY = ROOT / ".venv_voice" / "Scripts" / "python.exe"
MAIN_PY = ROOT / ".venv" / "Scripts" / "python.exe"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAIN_URL = "http://127.0.0.1:8765"
VOICE_URL = "http://127.0.0.1:8766"
voice_process: subprocess.Popen | None = None
voice_log_handle = None


def url_ready(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status < 500
    except Exception:
        return False


def wait_url(url: str, seconds: float, timeout: float = 2.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if url_ready(url, timeout=timeout):
            return True
        time.sleep(0.35)
    return False


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.35)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def cleanup() -> None:
    global voice_process, voice_log_handle
    # Kill only the voice process created by THIS launcher. A pre-existing
    # service is deliberately left untouched.
    if voice_process and voice_process.poll() is None:
        try:
            voice_process.terminate()
            voice_process.wait(timeout=4)
        except Exception:
            try:
                voice_process.kill()
            except Exception:
                pass
    if voice_log_handle:
        try:
            voice_log_handle.close()
        except Exception:
            pass
        voice_log_handle = None


def fresh_voice_log():
    global voice_log_handle
    current = ROOT / "voice_engine.log"
    previous = ROOT / "voice_engine.previous.log"
    try:
        if current.exists():
            previous.unlink(missing_ok=True)
            current.replace(previous)
    except Exception:
        # Logging must never prevent startup.
        pass
    voice_log_handle = current.open("w", encoding="utf-8")
    voice_log_handle.write(
        f"=== DrSaygon Voice Engine started {datetime.now().astimezone().isoformat()} ===\n"
    )
    voice_log_handle.flush()
    return voice_log_handle


def main() -> int:
    global voice_process
    os.chdir(ROOT)

    if not MAIN_PY.exists():
        print("[ERRO] Ambiente principal ausente. Execute INSTALAR.bat.")
        return 1
    voice_available = VOICE_PY.exists()
    if not voice_available:
        print("[AVISO] Motor de voz local nao instalado. Audio original e narracao manual continuam disponiveis.")

    # Professional duplicate-instance guard. Never launch a second server on
    # the same port; simply reopen the already-running application.
    if url_ready(f"{MAIN_URL}/api/system", timeout=2.0) or url_ready(MAIN_URL, timeout=2.0):
        print("[OK] DrSaygon Video Factory ja esta em execucao na porta 8765.")
        print("Abrindo a instancia existente, sem iniciar processos duplicados.")
        webbrowser.open(MAIN_URL)
        return 0
    if port_in_use(8765):
        print("[ERRO] A porta 8765 esta ocupada por outro processo que nao responde como DrSaygon Video Factory.")
        print("Feche o processo conflitante e execute INICIAR.bat novamente.")
        return 2

    if voice_available:
        if url_ready(f"{VOICE_URL}/health", timeout=2.0):
            print("[OK] Motor de voz local ja esta ativo. Reutilizando a instancia existente.")
        elif port_in_use(8766):
            print("[AVISO] A porta 8766 esta ocupada, mas o motor de voz nao respondeu ao health check.")
            print("O aplicativo principal sera aberto; veja o diagnostico de voz na interface.")
        else:
            print("Iniciando motor local de voz (CPU)...")
            voice_log = fresh_voice_log()
            voice_process = subprocess.Popen(
                [
                    str(VOICE_PY), "-m", "uvicorn", "voice.server:app",
                    "--host", "127.0.0.1", "--port", "8766", "--log-level", "warning",
                ],
                cwd=ROOT,
                stdout=voice_log,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
            if wait_url(f"{VOICE_URL}/health", 25, timeout=2.0):
                print("[OK] Motor de voz local iniciado.")
            else:
                print("[AVISO] O motor de voz nao respondeu. O restante do programa abrira normalmente.")
                print("Veja voice_engine.log ou execute DIAGNOSTICO.bat.")

    atexit.register(cleanup)
    print(f"Abrindo {MAIN_URL}")
    server = subprocess.Popen(
        [str(MAIN_PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8765"],
        cwd=ROOT,
    )
    if wait_url(MAIN_URL, 25, timeout=2.0) or wait_url(f"{MAIN_URL}/api/system", 10, timeout=2.5):
        webbrowser.open(MAIN_URL)
    else:
        print("[ERRO] O servidor principal nao respondeu na porta 8765.")
        cleanup()
        try:
            server.terminate()
        except Exception:
            pass
        return 3

    try:
        return server.wait()
    except KeyboardInterrupt:
        try:
            server.terminate()
        except Exception:
            pass
        return 0
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
