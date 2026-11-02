from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.web_research import _subject_tokens, _distinctive_model_tokens, _search_query


def main():
    tokens = set(_subject_tokens("Nobreak Intelbras XNB 720 120V"))
    assert "intelbras" in tokens
    assert "xnb" in tokens
    assert "720" in tokens, tokens
    models = _distinctive_model_tokens("Nobreak Intelbras XNB 720 120V")
    assert "720" in models and "120v" in models
    assert "dimensões" in _search_query("Intelbras XNB 720", "product_official")
    assert "dimensões" not in _search_query("Passeio no Shopping Independência", "subject_official")
    print("Generic Publication Engine v6.0.0: pesquisa adaptativa + identificadores de modelo = OK")


if __name__ == "__main__":
    main()
