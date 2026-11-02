from __future__ import annotations

import json
import shutil
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import FFMPEG_BIN
from app.models import (
    ClipReference, EditPlan, ProfessionalSyncBeat, ProfessionalSyncPlan,
    ProjectPaths, ProjectSettings, ScriptBlock, VisualRun,
)
from app.services.assembler import assemble_project
from app.services.ffmpeg import run_command


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix='drsaygon_scene_audio_v6_'))
    try:
        root = temp / 'project'
        paths = ProjectPaths(
            root=root, footage=root/'01_FILMAGENS', information=root/'02_INFORMACOES',
            analysis=root/'03_ANALISE', narration=root/'04_NARRACAO', result=root/'05_RESULTADO',
            publication=root/'06_PUBLICACAO', temp=root/'_TEMP', logs=root/'_LOGS',
        )
        for value in paths.model_dump().values():
            Path(value).mkdir(parents=True, exist_ok=True)
        source = paths.footage / 'cena.mp4'
        run_command([
            FFMPEG_BIN, '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=4',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '192k', '-shortest', str(source),
        ])
        voice = paths.narration / 'bloco_001.wav'
        run_command([
            FFMPEG_BIN, '-y', '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=4',
            '-c:a', 'pcm_s16le', str(voice),
        ])
        settings = ProjectSettings(
            name='Cena audio', slug='x', created_at='2026-01-01', model='offline',
            resolution='1920x1080', fps=30, audio_mode='narration', create_master=False,
            create_delivery=True, delivery_profile='youtube_1080p', desired_minutes=1,
        )
        block = ScriptBlock(
            number=1, title='Teste', narration='Uma fala continua para testar audio por cena.',
            estimated_seconds=4,
            clips=[ClipReference(filename='cena.mp4', start_seconds=0, end_seconds=4, reason='teste')],
        )
        plan = EditPlan(project_title='Teste', objective='audio por cena', target_duration_seconds=4, blocks=[block])
        beats = [
            ProfessionalSyncBeat(
                beat_id='b1', block_number=1, beat_number=1, text='a', output_start_seconds=0,
                output_end_seconds=2, required_observation='a', event_id='e1', filename='cena.mp4',
                source_start_seconds=0, source_end_seconds=2, match_confidence=.9, verification='semantic',
                audio_strategy='ambient',
            ),
            ProfessionalSyncBeat(
                beat_id='b2', block_number=1, beat_number=2, text='b', output_start_seconds=2,
                output_end_seconds=4, required_observation='b', event_id='e2', filename='cena.mp4',
                source_start_seconds=2, source_end_seconds=4, match_confidence=.9, verification='semantic',
                audio_strategy='feature',
            ),
        ]
        runs = [
            VisualRun(
                run_id='r1', block_number=1, output_start_seconds=0, output_end_seconds=2,
                filename='cena.mp4', source_start_seconds=0, source_end_seconds=2,
                beat_ids=['b1'], event_ids=['e1'], verification='semantic', confidence=.9,
                audio_strategy='ambient',
            ),
            VisualRun(
                run_id='r2', block_number=1, output_start_seconds=2, output_end_seconds=4,
                filename='cena.mp4', source_start_seconds=2, source_end_seconds=4,
                beat_ids=['b2'], event_ids=['e2'], verification='semantic', confidence=.9,
                audio_strategy='feature',
            ),
        ]
        sync = ProfessionalSyncPlan(total_duration_seconds=4, beats=beats, visual_runs=runs, ready=True)
        out = assemble_project(paths, settings, plan, sync, lambda _: None, lambda *_: None)
        if not out.exists():
            raise RuntimeError('Saida nao foi criada')
        report = json.loads((paths.result / 'RELATORIO_DE_EDICAO.json').read_text(encoding='utf-8'))
        strategies = [r['audio_strategy'] for r in report['blocks'][0]['visual_runs']]
        if strategies != ['ambient', 'feature']:
            raise RuntimeError(f'Override por cena ignorado em modo narracao: {strategies}')
        print('[OK] Audio por cena v6: narration respeita ambient/feature e feature aciona ducking.')
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
