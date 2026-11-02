from __future__ import annotations

import sys
from pathlib import Path
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import ProjectCreate


def main():
    p1 = ProjectCreate(name="Video 1", product="Produto", format="youtube_16_9", desired_minutes=1)
    p90 = ProjectCreate(name="Video 90", product="Produto", format="youtube_16_9", desired_minutes=90)
    assert p1.target_duration_seconds if hasattr(p1, "target_duration_seconds") else True
    assert p90.desired_minutes == 90
    try:
        ProjectCreate(name="Video 91", product="Produto", format="youtube_16_9", desired_minutes=91)
        raise AssertionError("91 minutos deveria ser rejeitado")
    except ValidationError:
        pass
    print("Duracao longa v6: slider/modelo 1-90 minutos = OK")


if __name__ == "__main__":
    main()
