# report/renderers/pdf_qa_reportlab.py
"""
QA 결과(PDF) 렌더러 - ReportLab 기반 (DTRO-Safety-On)

- 규정 QA 결과(answer, citations, used_context)를 PDF로 저장
- 한글 폰트는 report/renderers/font_utils.register_korean_font()로 통일
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_LEFT

from core.logger import get_logger
from report.renderers.font_utils import register_korean_font

logger = get_logger(__name__)


def _get_font_name() -> str:
    """
    한글 폰트 등록하고 폰트명 반환.
    실패하면 Helvetica로 fallback.
    """
    try:
        return register_korean_font()
    except Exception as e:
        logger.warning(f"[QA PDF] Korean font register failed. fallback=Helvetica err={e}")
        return "Helvetica"


def _escape_for_paragraph(text: str) -> str:
    """
    reportlab Paragraph는 HTML-like markup을 해석하므로
    기본 특수문자를 escape + 줄바꿈 처리
    """
    t = "" if text is None else str(text)
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
    return t


def render_qa_pdf(
    output_path: Path,
    *,
    title: str,
    incident_summary: str,
    question: str,
    answer: str,
    citations_text: str,
    context_text: str,
) -> Path:
    """
    PDF 생성 메인
    - citations_text/context_text는 사람이 읽기 좋은 형태로 문자열로 넣는 것을 권장
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    font_name = _get_font_name()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
    )

    styles = getSampleStyleSheet()

    # 기본 스타일을 "한글 폰트"로 통일해서 새로 정의
    h1 = ParagraphStyle(
        "QA_H1",
        parent=styles["Heading1"],
        fontName=font_name,
        fontSize=16,
        leading=20,
        spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "QA_H2",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=12.5,
        leading=16,
        spaceBefore=10,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "QA_BODY",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10.5,
        leading=14,
        alignment=TA_LEFT,
    )

    def p(text: str) -> Paragraph:
        safe = _escape_for_paragraph(text or "")
        return Paragraph(safe, body)

    story: List[Any] = []

    story.append(Paragraph(_escape_for_paragraph(title), h1))
    story.append(Spacer(1, 6))

    story.append(Paragraph("1) 사고 요약", h2))
    story.append(p(incident_summary))
    story.append(Spacer(1, 10))

    story.append(Paragraph("2) 질문", h2))
    story.append(p(question))
    story.append(Spacer(1, 10))

    story.append(Paragraph("3) AI 답변", h2))
    story.append(p(answer))
    story.append(Spacer(1, 10))

    story.append(Paragraph("4) 근거(Top-K)", h2))
    story.append(p(citations_text or "(없음)"))
    story.append(Spacer(1, 10))

    story.append(PageBreak())
    story.append(Paragraph("5) 사용된 컨텍스트(디버그)", h2))
    story.append(p(context_text or "(없음)"))

    doc.build(story)
    logger.info(f"[QA PDF] saved -> {output_path}")
    return output_path
