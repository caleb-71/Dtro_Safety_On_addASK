# apps/streamlit/_bootstrap.py
from __future__ import annotations

import sys
from pathlib import Path

# .../Dtro_Safety_On/apps/streamlit/_bootstrap.py -> parents[2] = 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def ensure_project_root_on_syspath() -> Path:
    """
    Streamlit에서 페이지를 어디서 실행하든,
    core/backend/apps import가 되도록 프로젝트 루트를 sys.path에 주입.
    """
    root_str = str(PROJECT_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return PROJECT_ROOT
