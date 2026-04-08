# report/renderers/pdf_reportlab.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.logger import get_logger
from report.renderers.font_utils import register_korean_font

logger = get_logger(__name__)


# =========================================================
# Template loader
# =========================================================
def _load_template(template_path: Path) -> Dict[str, Any]:
    if not template_path.exists():
        raise FileNotFoundError(f"report template not found: {template_path}")
    return json.loads(template_path.read_text(encoding="utf-8"))


# =========================================================
# Text formatter
# =========================================================
def _format_text_for_pdf(text: Any) -> str:
    """
    PDF용 텍스트 정리:
    - None 안전 처리
    - 특수문자 escape (& < >)
    - 줄바꿈 처리
    - bullet(- ) 가독성 개선
    - ✅ 날짜 문자열 후처리(T00:00:00 / 00:00:00 제거)
    """
    if text is None:
        return ""

    # dict/list 같은 값도 들어올 수 있으니 안전하게 문자열화
    t = str(text)
    if not t:
        return ""

    # ✅ 날짜/시간 문자열 후처리(렌더러 최종 방어선)
    t = t.replace("T00:00:00", "")
    t = t.replace(" 00:00:00", "")

    # reportlab Paragraph용 escape
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 줄바꿈 정리
    t = t.replace("\r\n", "\n").replace("\r", "\n")

    # bullet 처리
    lines = t.split("\n")
    out: List[str] = []
    for line in lines:
        line = (line or "").strip()
        if not line:
            out.append("")  # 빈 줄 유지
            continue
        if line.startswith("- "):
            out.append(f"• {line[2:]}")
        else:
            out.append(line)

    return "<br/>".join(out)


def _get_font_name() -> str:
    try:
        return register_korean_font()
    except Exception as e:
        logger.warning(f"[PDF] Korean font register failed. fallback=Helvetica err={e}")
        return "Helvetica"


# =========================================================
# ✅ table payload 감지 + 렌더링
# =========================================================
def _is_table_payload(val: Any) -> bool:
    """
    report_data field 값이 아래 형태면 표 렌더링으로 처리:
    {
      "__table__": {
        "columns": ["A","B","C"],
        "rows": [[...], [...]],
        "style": "heat" | "plain",
        "note": "optional"
      }
    }
    """
    if not isinstance(val, dict):
        return False
    t = val.get("__table__")
    if not isinstance(t, dict):
        return False
    cols = t.get("columns")
    rows = t.get("rows")
    return isinstance(cols, list) and isinstance(rows, list)


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _heat_color(v: float, vmin: float, vmax: float) -> colors.Color:
    """
    히트맵 느낌: 값이 클수록 진하게(단계형).
    """
    if vmax <= vmin:
        return colors.whitesmoke

    r = (v - vmin) / (vmax - vmin)
    r = max(0.0, min(1.0, r))

    step = int(r * 4.999)

    palette = [
        colors.whitesmoke,
        colors.HexColor("#E8F0FF"),
        colors.HexColor("#CFE0FF"),
        colors.HexColor("#AFC8FF"),
        colors.HexColor("#7FA6FF"),
    ]
    return palette[step]


def _build_table_flowable(
    table_payload: Dict[str, Any],
    *,
    base_style: ParagraphStyle,
    font_name: str,
    content_width_mm: float,
) -> Tuple[Table, Optional[str]]:
    """
    표(Table) Flowable 생성.
    - style="heat"이면 숫자 셀 배경을 단계적으로 칠함.
    """
    t = table_payload.get("__table__", {}) or {}
    columns: List[str] = t.get("columns", []) or []
    rows: List[List[Any]] = t.get("rows", []) or []
    style = (t.get("style") or "plain").lower()
    note = t.get("note") or None

    # header 포함 Table 데이터 구성
    data: List[List[Any]] = []
    header = [Paragraph(f"<b>{_format_text_for_pdf(c)}</b>", base_style) for c in columns]
    data.append(header)

    for r in rows:
        rr: List[Any] = []
        for cell in (r or []):
            rr.append(Paragraph(_format_text_for_pdf("" if cell is None else cell), base_style))
        data.append(rr)

    # 열 너비: 첫 열은 라벨이 많아 넓게
    col_cnt = max(1, len(columns))
    total_mm = content_width_mm
    if col_cnt == 1:
        widths = [total_mm * mm]
    else:
        first = min(60.0, total_mm * 0.35)
        rest = (total_mm - first) / (col_cnt - 1)
        widths = [first * mm] + [rest * mm] * (col_cnt - 1)

    table = Table(data, colWidths=widths)

    ts = [
        ("BOX", (0, 0), (-1, -1), 0.6, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9.8),
    ]

    # heat 스타일: 숫자 셀 배경색 지정
    if style == "heat" and rows and len(columns) > 1:
        vals: List[float] = []
        for r in rows:
            for j in range(1, len(r or [])):  # 첫 열은 라벨로 가정
                fv = _safe_float((r or [])[j])
                if fv is not None:
                    vals.append(fv)

        if vals:
            vmin, vmax = min(vals), max(vals)
            for i, r in enumerate(rows, start=1):  # header가 0행이므로 start=1
                for j in range(1, len(r or [])):
                    fv = _safe_float((r or [])[j])
                    if fv is None:
                        continue
                    ts.append(("BACKGROUND", (j, i), (j, i), _heat_color(fv, vmin, vmax)))

    table.setStyle(TableStyle(ts))
    return table, note


# =========================================================
# Main renderer
# =========================================================
def render_report_pdf(
    output_path: Path,
    template_path: Path,
    report_data: Dict[str, Any],
    title_override: Optional[str] = None,
) -> Path:
    """
    template.sections 기반으로 report_data[sec_key][field_key]를 읽어 PDF 생성.
    - report_data가 일부 누락되어도 예외 없이 진행(빈칸 처리)
    - table payload는 섹션 내에서 별도 표로 출력
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    template = _load_template(Path(template_path))
    font_name = _get_font_name()

    # A4(210mm) 기준, 좌우 18mm 마진 가정
    CONTENT_W_MM = 210 - (18 * 2)
    LABEL_W_MM = 44
    VALUE_W_MM = CONTENT_W_MM - LABEL_W_MM

    styles = getSampleStyleSheet()

    base = ParagraphStyle(
        "Base",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10.5,
        leading=15,
    )
    h1 = ParagraphStyle(
        "H1",
        parent=styles["Heading1"],
        fontName=font_name,
        fontSize=16,
        leading=22,
        spaceAfter=10,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=12.5,
        leading=18,
        spaceBefore=12,
        spaceAfter=6,
    )
    note_style = ParagraphStyle(
        "Note",
        parent=base,
        fontSize=9.2,
        leading=13,
        textColor=colors.grey,
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=template.get("title", "Report"),
    )

    story: List[Any] = []

    title = title_override or template.get("title", "DTRO-Safety-On 사고(동향) 보고서")
    story.append(Paragraph(_format_text_for_pdf(title), h1))
    story.append(Spacer(1, 8))

    sections = template.get("sections", []) or []
    for sec in sections:
        sec_key = sec.get("key")
        sec_title = sec.get("title", sec_key)

        story.append(Paragraph(_format_text_for_pdf(sec_title), h2))

        # ✅ 섹션 데이터는 dict가 아니면 빈 dict로
        sec_data_raw = (report_data or {}).get(sec_key, {}) if sec_key else {}
        sec_data: Dict[str, Any] = sec_data_raw if isinstance(sec_data_raw, dict) else {}

        rows: List[List[Any]] = []
        table_fields: List[Tuple[str, str, Dict[str, Any]]] = []  # (field_key, label, payload)

        fields = sec.get("fields", []) or []
        for f in fields:
            k = f.get("key")
            label = f.get("label", k)
            if not k:
                continue

            val = sec_data.get(k, "")

            label_html = f"<b>{_format_text_for_pdf(label)}</b>"

            # ✅ table payload면 본문 표에는 "(표 형태로 출력)"만 넣고, 아래에서 별도로 표 출력
            if _is_table_payload(val):
                rows.append([Paragraph(label_html, base), Paragraph(_format_text_for_pdf("(표 형태로 출력)"), base)])
                table_fields.append((k, str(label or k), val))
            else:
                rows.append([Paragraph(label_html, base), Paragraph(_format_text_for_pdf(val), base)])

        if rows:
            table = Table(rows, colWidths=[LABEL_W_MM * mm, VALUE_W_MM * mm])
            table.setStyle(
                TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.6, colors.black),
                        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.grey),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ]
                )
            )
            story.append(table)
        else:
            story.append(Paragraph("-", base))

        # ✅ table payload(실제 표)는 섹션 아래에 별도 출력 (한 번 순회로 수집한 값 사용)
        for _, label, payload in table_fields:
            story.append(Spacer(1, 6))
            story.append(Paragraph(_format_text_for_pdf(f"• {label}"), base))
            tbl, note = _build_table_flowable(
                payload,
                base_style=base,
                font_name=font_name,
                content_width_mm=CONTENT_W_MM,
            )
            story.append(Spacer(1, 4))
            story.append(tbl)
            if note:
                story.append(Spacer(1, 3))
                story.append(Paragraph(_format_text_for_pdf(note), note_style))

        story.append(Spacer(1, 10))

    doc.build(story)
    logger.info(f"[PDF] generated: {output_path}")
    return output_path
