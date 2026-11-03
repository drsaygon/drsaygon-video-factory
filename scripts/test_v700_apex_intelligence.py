from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import ClipReference, PhoneticBudget, ScriptBlock, VisualAnchor
from app.services.draft_qa import audit_and_repair_scene_sync
from app.services.ffmpeg import apply_snap_to_cut, extract_physical_media_map
from app.services.professional_sync import calculate_sync_precision_score, resolve_elastic_sync
from app.services.voice_profile import calculate_phonetic_budget, estimate_spoken_duration_sec


def test_physical_media_map_and_cuts():
    # Test deterministic fallback on nonexistent or synthetic file
    res = extract_physical_media_map("non_existent_mock.mp4")
    assert "safe_cut_timestamps" in res
    assert "cut_points" in res
    assert "silence_windows" in res
    assert len(res["safe_cut_timestamps"]) > 0

    # Test magnetic snap-to-cut
    safe_cuts = [2.50, 5.00, 7.80, 10.20]
    # Within 0.40s threshold -> should snap
    assert apply_snap_to_cut(2.65, safe_cuts, threshold=0.40) == 2.50
    assert apply_snap_to_cut(7.70, safe_cuts, threshold=0.40) == 7.80
    # Farther than 0.40s threshold -> should not snap
    assert apply_snap_to_cut(3.50, safe_cuts, threshold=0.40) == 3.50
    print("[OK] PhysicalMediaMap & Magnetic Snap-to-Cut test passed.")


def test_phonetic_budget_and_spoken_duration():
    duration = 10.0
    budget = calculate_phonetic_budget(duration, speaking_rate_wps=2.65, pause_overhead_ratio=0.12)
    # T_efetivo = 10 * 0.88 = 8.8
    # W_alvo = round(8.8 * 2.65) = 23
    assert budget["effective_speech_sec"] == 8.8
    assert budget["target_words"] == 23
    assert budget["min_words"] == max(1, math.floor(23 * 0.88))
    assert budget["max_words"] == math.ceil(23 * 1.08)
    assert budget["target_syllables"] == round(23 * 2.35)

    # Spoken duration calculation
    text = "Aqui temos uma frase de teste, com pausas adequadas. E outra sentença final!"
    dur = estimate_spoken_duration_sec(text, speaking_rate_wps=2.65)
    # 12 words / 2.65 ~= 4.528 + 1 comma (0.18) + 1 period (0.35) + 1 exclamation (0.35) = ~5.408
    assert dur > 5.0
    print("[OK] PhoneticBudget & Spoken Duration test passed.")


def test_elastic_sync_4_levels():
    anchor = VisualAnchor(timestamp_sec=2.0, trigger_phrase="comutação", action="mostrar comutação")

    # Level 1: Native (|Delta D| <= 0.12s)
    s1 = resolve_elastic_sync(video_duration=5.0, audio_duration=5.08, visual_anchor=anchor)
    assert s1["strategy"] == "native"
    assert s1["sync_precision_score"] == 100.0
    assert s1["needs_script_repair"] is False

    # Level 2: Pause Shaping (0.12s < |Delta D| <= 0.45s)
    s2 = resolve_elastic_sync(video_duration=5.0, audio_duration=5.30, visual_anchor=anchor)
    assert s2["strategy"] == "pause_shaping"
    assert s2["sync_precision_score"] == 98.0
    assert s2["needs_script_repair"] is False

    # Level 3: Elastic Trim / Micro-Tempo (0.45s < |Delta D| <= 1.20s)
    s3 = resolve_elastic_sync(video_duration=5.0, audio_duration=5.85, visual_anchor=anchor)
    assert s3["strategy"] in ("elastic_trim", "micro_tempo")
    assert 0.96 <= s3["tempo_factor"] <= 1.05
    assert s3["sync_precision_score"] >= 80.0
    assert s3["needs_script_repair"] is False

    # Level 4: AI Rewrite Trigger (|Delta D| > 1.20s)
    s4 = resolve_elastic_sync(video_duration=3.0, audio_duration=6.50, visual_anchor=anchor)
    assert s4["strategy"] == "ai_rewrite"
    assert s4["needs_script_repair"] is True
    assert s4["sync_precision_score"] < 80.0

    # Precision score penalty checks
    assert calculate_sync_precision_score(0.0, "native") == 100.0
    assert calculate_sync_precision_score(0.0, "pause_shaping") == 98.0
    assert calculate_sync_precision_score(0.0, "elastic_trim") == 95.0
    assert calculate_sync_precision_score(0.0, "micro_tempo") == 92.0
    assert calculate_sync_precision_score(0.0, "native", anchoring_failed=True) == 85.0
    print("[OK] 4-Level Elastic Sync Engine & Precision Score test passed.")


def test_surgical_script_auto_repair():
    anchor = VisualAnchor(timestamp_sec=1.5, trigger_phrase="desconecto", action="desconectar cabo")
    block = ScriptBlock(
        number=1,
        title="Teste de Desconexão",
        narration="Eu realmente desconecto o cabo agora com muito cuidado e atenção observando todos os detalhes aqui.",
        estimated_seconds=3,
        clips=[ClipReference(filename="tomada.mp4", start_seconds=0, end_seconds=3, reason="teste")],
        visual_anchor=anchor,
    )
    repaired, modified, reason = audit_and_repair_scene_sync(None, block)
    assert repaired is True
    assert "Auto-reparo cirúrgico" in reason
    words = modified.narration.split()
    assert len(words) <= 9
    assert "desconecto" in modified.narration.lower()
    assert modified.sync_precision_score >= 85.0

    compliant_block = ScriptBlock(
        number=2,
        title="Cena Conforme",
        narration="Aqui desconecto o cabo com segurança total.",
        estimated_seconds=3,
        clips=[ClipReference(filename="tomada.mp4", start_seconds=0, end_seconds=3, reason="teste")],
        visual_anchor=anchor,
        sync_precision_score=98.0,
    )
    c_repaired, _, c_reason = audit_and_repair_scene_sync(None, compliant_block)
    assert c_repaired is False
    assert "em conformidade" in c_reason
    print("[OK] Surgical Scene Script Auto-Repair test passed.")


def main():
    print("=" * 76)
    print("DRSAYGON VIDEO FACTORY 7.0.0 - APEX AUTONOMOUS INTELLIGENCE TEST SUITE")
    print("=" * 76)
    test_physical_media_map_and_cuts()
    test_phonetic_budget_and_spoken_duration()
    test_elastic_sync_4_levels()
    test_surgical_script_auto_repair()
    print("=" * 76)
    print("[OK] TODOS OS TESTES DO MOTOR APEX v7.0.0 PASSARAM COM SUCESSO!")
    print("=" * 76)


if __name__ == "__main__":
    main()
