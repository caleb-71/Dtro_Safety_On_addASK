# rag/prompting/renderer.py
"""
Prompt Renderer (DTRO-Safety-On)

역할
- templates/*.md 프롬프트 파일을 읽어와
- {placeholders} 를 값으로 치환하여 최종 프롬프트 문자열을 만든다.

주의
- 템플릿은 코드가 아니라 "리소스"다.
- templates 폴더는 패키지 X (폴더)
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from core.config import get_settings
from core.logger import get_logger

logger = get_logger(__name__)


def load_template(template_name: str) -> str:
    """
    rag/prompting/templates/{template_name} 파일 읽기
    예: qa_general_v1.md
    """
    settings = get_settings()
    base = Path(__file__).parent / "templates"
    path = base / template_name

    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")

    text = path.read_text(encoding="utf-8")
    logger.info(f"[Prompt] template loaded: {template_name} chars={len(text)}")
    return text


def render_template(template_text: str, values: Dict[str, str]) -> str:
    """
    {key} 형태 placeholder 치환
    - 값이 없으면 빈 문자열로 처리
    """
    out = template_text
    for k, v in values.items():
        out = out.replace("{" + k + "}", v if v is not None else "")
    return out


def render_from_file(template_name: str, values: Dict[str, str]) -> str:
    """
    파일 로드 + 치환
    """
    tmpl = load_template(template_name)
    return render_template(tmpl, values)
