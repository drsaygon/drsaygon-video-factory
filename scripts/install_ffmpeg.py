from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
DEST_DIR = TOOLS_DIR / "ffmpeg"
BIN_DIR = DEST_DIR / "bin"
FFMPEG_EXE = BIN_DIR / "ffmpeg.exe"
FFPROBE_EXE = BIN_DIR / "ffprobe.exe"
PRIMARY_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
GITHUB_API = "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest"
USER_AGENT = "DrSaygon-Video-Factory/6.0.2"


def request_url(url: str):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
        timeout=90,
    )


def github_fallback_url() -> str:
    with request_url(GITHUB_API) as response:
        data = json.load(response)
    for asset in data.get("assets", []):
        name = str(asset.get("name", "")).lower()
        if name.endswith("-essentials_build.zip"):
            return str(asset["browser_download_url"])
    raise RuntimeError("o release mais recente do GitHub nao possui o ZIP Essentials")


def download(url: str, destination: Path) -> None:
    with request_url(url) as response, destination.open("wb") as target:
        total = int(response.headers.get("Content-Length", "0") or 0)
        downloaded = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)
            downloaded += len(chunk)
            if total:
                percent = min(100, int(downloaded * 100 / total))
                print(f"\rBaixando FFmpeg: {percent:3d}%", end="", flush=True)
            else:
                print(
                    f"\rBaixando FFmpeg: {downloaded / (1024 * 1024):.1f} MB",
                    end="",
                    flush=True,
                )
    print()


def install() -> int:
    if FFMPEG_EXE.exists() and FFPROBE_EXE.exists():
        print(f"FFmpeg portatil ja instalado em: {BIN_DIR}")
        return 0

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    print("O FFmpeg sera instalado somente dentro da pasta do programa.")
    print("Nenhuma configuracao global do Windows sera alterada.")

    with tempfile.TemporaryDirectory(prefix="drsaygon_ffmpeg_") as temp_value:
        temp_dir = Path(temp_value)
        archive = temp_dir / "ffmpeg.zip"
        extracted = temp_dir / "extraido"

        custom_url = os.getenv("DRSAYGON_FFMPEG_URL", "").strip()
        urls: list[str] = [custom_url] if custom_url else [PRIMARY_URL]
        errors: list[str] = []

        if not custom_url:
            try:
                urls.append(github_fallback_url())
            except Exception as error:
                errors.append(f"GitHub API: {error}")

        downloaded = False
        for index, url in enumerate(urls, start=1):
            try:
                print(f"Fonte {index}: {url}")
                download(url, archive)
                downloaded = True
                break
            except Exception as error:
                errors.append(f"{url}: {error}")
                archive.unlink(missing_ok=True)
                print("Esta fonte falhou. Tentando a proxima...")

        if not downloaded:
            print("[ERRO] Nao foi possivel baixar o FFmpeg.")
            for error in errors:
                print(f"  - {error}")
            print("Verifique sua internet, antivirus ou firewall e execute INSTALAR.bat novamente.")
            return 1

        try:
            print("Extraindo FFmpeg...")
            with zipfile.ZipFile(archive) as package:
                package.extractall(extracted)
        except Exception as error:
            print(f"[ERRO] O arquivo do FFmpeg nao pode ser extraido: {error}")
            return 1

        candidates = list(extracted.rglob("ffmpeg.exe"))
        if not candidates:
            print("[ERRO] ffmpeg.exe nao foi encontrado dentro do pacote baixado.")
            return 1

        source_root = candidates[0].parent.parent
        if DEST_DIR.exists():
            shutil.rmtree(DEST_DIR)
        shutil.copytree(source_root, DEST_DIR)

    if not FFMPEG_EXE.exists() or not FFPROBE_EXE.exists():
        print("[ERRO] A instalacao portatil terminou sem ffmpeg.exe ou ffprobe.exe.")
        return 1

    print(f"[OK] FFmpeg portatil instalado em: {BIN_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(install())
