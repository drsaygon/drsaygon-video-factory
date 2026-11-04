from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.config as config
from app.services.voice_client import normalize_bilingual_tech_text, apply_pronunciations
from app.services.continuity import auto_heal_scene_duration_mismatch, _tighten_narration_audio
from app.services.ffmpeg import render_punch_in_composite_clip, probe_media, FFMPEG_BIN
from app.services.gemini import ApexGeminiService
import subprocess


def test_bilingual_tech_tts() -> None:
    print("[TEST] 1. Bilingual Technical TTS Normalization...")
    samples = [
        ("O DirectX Shader Compiler otimiza o pipeline de ray tracing na GPU com C++.",
         ["Dairéct Éks", "xêider", "páip-láin", "rêi trêissin", "Gê-Pê-Ú", "Cê mais mais"]),
        ("Verifique o frame rate no viewport e o throughput do runtime com 60 FPS.",
         ["frêim rêit", "víu-pórt", "tru-pút", "rân-táim"]),
        ("O deploy do modelo no autopilot com embeddings e zero-shot foi concluído.",
         ["deplói", "óto-páilót", "embédins", "zíro xót"]),
    ]
    for text, expected_tokens in samples:
        normalized = normalize_bilingual_tech_text(text)
        for token in expected_tokens:
            assert token.lower() in normalized.lower(), f"Esperava '{token}' em: {normalized}"

    # Test apply_pronunciations with PT-BR numbers
    res = apply_pronunciations("DirectX 3.8 na GPU com 60 frame rate", {})
    assert "Dairéct Éks" in res or "dairéct éks" in res.lower()
    assert "sessenta" in res
    assert "frêim rêit" in res
    print("   [OK] Dicionario fonetico e code-switching bilingue aprovados!")


def test_auto_heal_stage1_handle_expansion() -> None:
    print("[TEST] 2. Auto-Healer - Estagio 1: Smart Handle Expansion...")
    # Footage total: 15s. Clip selected: 1.0s to 9.41s (8.41s duration). Wanted: 9.33s (deficit: 0.92s).
    res = auto_heal_scene_duration_mismatch(
        scene_id="b001",
        take_id="r004",
        clip_duration=8.41,
        required_duration=9.33,
        source_video_duration=15.0,
        clip_in_point=1.0,
        clip_out_point=9.41,
    )
    assert res["healed"] is True
    assert res["strategy"] == "handle_expansion"
    assert res["effective_clip_duration"] >= 9.33 - 0.01
    assert res["out_point"] > 9.41
    print(f"   [OK] Handle expansion expandiu tomada para {res['effective_clip_duration']:.2f}s!")


def test_auto_heal_stage2_smart_audio_pacing() -> None:
    print("[TEST] 3. Auto-Healer - Estagio 2: Smart Audio Pacing (atempo)...")
    # Generate a dummy wav to test in-place audio tightening
    import wave, struct, math
    tmp_audio = ROOT / "projetos" / "test_stage2_narration.wav"
    tmp_audio.parent.mkdir(parents=True, exist_ok=True)
    rate = 24000
    with wave.open(str(tmp_audio), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        # 3 seconds of sound
        tone = [int(32767 * 0.4 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate * 3)]
        wf.writeframes(struct.pack("<" + "h" * len(tone), *tone))

    try:
        # File exhausted at 2.70s, wanted is 3.0s (deficit ~11%, ratio ~1.11x <= 1.15)
        res = auto_heal_scene_duration_mismatch(
            scene_id="b001",
            take_id="r004",
            clip_duration=2.70,
            required_duration=3.00,
            audio_path=tmp_audio,
            source_video_duration=2.70,
            clip_in_point=0.0,
            clip_out_point=2.70,
        )
        assert res["healed"] is True
        assert res["strategy"].startswith("smart_audio_pacing")
        assert res["effective_audio_duration"] <= 2.75
        print(f"   [OK] Smart Audio Pacing ({res['strategy']}) compactou audio para {res['effective_audio_duration']:.2f}s!")
    finally:
        tmp_audio.unlink(missing_ok=True)


def test_auto_heal_stage3_dynamic_punch_in() -> None:
    print("[TEST] 4. Auto-Healer - Estagio 3: Dynamic Punch-In Jump-Cut...")
    # Footage exhausted at 8.41s, required duration 12.0s (speedup would exceed 15%)
    res = auto_heal_scene_duration_mismatch(
        scene_id="b001",
        take_id="r004",
        clip_duration=8.41,
        required_duration=12.00,
        source_video_duration=8.41,
        clip_in_point=0.0,
        clip_out_point=8.41,
    )
    assert res["healed"] is True
    assert res["strategy"] == "composite_punch_in_broll"
    assert res.get("requires_dynamic_punch_in") is True
    assert res["primary_take_duration"] == 8.41
    print(f"   [OK] Dynamic Punch-In Jump-Cut acionado sem congelar frames!")


def test_render_composite_punch_in() -> None:
    print("[TEST] 5. Renderizacao Real do Jump-Cut Punch-In (FFmpeg)...")
    tmp_dir = ROOT / "projetos" / "test_punch_in_render"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    src_video = tmp_dir / "src_test.mp4"
    dst_video = tmp_dir / "dst_punch.mp4"

    # Create 3s source video
    cmd = [
        FFMPEG_BIN, "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=24",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3",
        "-c:v", "libx264", "-c:a", "aac", str(src_video),
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    try:
        # We need wanted_duration=3.6s from a 3.0s available video
        render_punch_in_composite_clip(
            source=src_video,
            destination=dst_video,
            start=0.0,
            available_duration=3.0,
            wanted_duration=3.6,
            width=640,
            height=360,
            fps=24,
            format_mode="youtube_16_9",
            punch_in_scale=1.18,
        )
        assert dst_video.exists(), "dst_punch.mp4 nao foi gerado"
        dur = probe_media(dst_video).duration
        assert abs(dur - 3.6) <= 0.25, f"Duracao renderizada inesperada: {dur:.2f}s (esperado 3.6s)"
        print(f"   [OK] Clip composto renderizado com sucesso: {dur:.2f}s com Jump-Cut Punch-In 118%!")
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_gemini_38_and_prompts() -> None:
    print("[TEST] 6. Configuracao Gemini 3.8+ e Prompts Dual-Field...")
    assert config.GEMINI_MODEL_PRIMARY == "gemini-3.8-flash"
    assert "gemini-3.8-flash" in config.GEMINI_FALLBACK_MODELS
    assert hasattr(ApexGeminiService, "generate_structured_json")

    criar_roteiro = (ROOT / "prompts/criar_roteiro.txt").read_text(encoding="utf-8")
    alinhar_narracao = (ROOT / "prompts/alinhar_narracao.txt").read_text(encoding="utf-8")

    for prompt_text, name in [(criar_roteiro, "criar_roteiro.txt"), (alinhar_narracao, "alinhar_narracao.txt")]:
        assert "ORÇAMENTO RÍGIDO DE SÍLABAS" in prompt_text, f"Regra de sílabas ausente em {name}"
        assert "floor(D * 2.3)" in prompt_text, f"Fórmula floor(D * 2.3) ausente em {name}"
        assert "CAMPO DUPLO PARA TERMOS EM INGLÊS" in prompt_text, f"Regra campo duplo ausente em {name}"
        assert "tts_phonetic_text" in prompt_text, f"tts_phonetic_text ausente em {name}"

    print("   [OK] Gemini 3.8+ e prompts com regras foneticas e de orcamento confirmados!")


def main() -> int:
    print("=" * 76)
    print("DRSAYGON VIDEO FACTORY - SUITE DE TESTES APEX AUTO-HEALER & TTS BILÍNGUE")
    print("=" * 76)
    test_bilingual_tech_tts()
    test_auto_heal_stage1_handle_expansion()
    test_auto_heal_stage2_smart_audio_pacing()
    test_auto_heal_stage3_dynamic_punch_in()
    test_render_composite_punch_in()
    test_gemini_38_and_prompts()
    print("=" * 76)
    print("[OK] TODOS OS 6 TESTES DE AUTO-CURA E FONETICA PASSARAM COM SUCESSO!")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())

