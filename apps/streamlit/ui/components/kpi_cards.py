# apps/streamlit/ui/components/kpi_cards.py
from __future__ import annotations

import streamlit as st

try:
    from apps.streamlit.ui.theme.tokens import TOKENS  # type: ignore
except Exception:
    TOKENS = {
        "ok": "#16A34A",
        "warn": "#F59E0B",
        "danger": "#EF4444",
        "info": "#3B82F6",
        "critical": "#A855F7",
        "primary": "#7C5CFF",    # 지도점검 (선명한 보라)
        "secondary": "#00D4FF",  # 동향보고 (선명한 하늘색)
    }

# =========================================================
# Color helpers
# =========================================================
def _tone_color(tone: str) -> str:
    return TOKENS.get(tone, TOKENS.get("info", "#3B82F6"))

def _group_accent(group: str | None) -> str:
    """좌측 바의 컬러: '동향'은 하늘색, '점검'은 보라색"""
    g = (group or "").strip().lower()
    if g in ("trend", "accident", "사고"):
        return TOKENS.get("secondary", "#00D4FF")
    if g in ("safety_map", "safetymap", "점검", "지도"):
        return TOKENS.get("primary", "#7C5CFF")
    return "#E2E8F0"

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    try:
        c = hex_color.lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    except:
        return f"rgba(255,255,255,{alpha})"

def _soft_bg(accent: str) -> str:
    """✅ 배경: 가독성을 위해 투명도를 0.06으로 매우 낮게 조정 (숫자 번짐 방지)"""
    return (
        "linear-gradient(90deg, "
        f"{_hex_to_rgba(accent, 0.06)} 0%, "
        "rgba(255,255,255,1.0) 100%)"
    )

def _render(html: str) -> None:
    st.markdown(html, unsafe_allow_html=True)

# =========================================================
# Public API
# =========================================================
def kpi_card(
    label: str,
    value,
    delta=None,
    *,
    tone: str = "info",
    chip: str | None = None,
    group: str | None = None,
) -> None:
    """
    KPI Card 리팩토링:
    - 좌측 6px 강조 바 (컬러풀한 구분)
    - 흰색 바탕 배경 (숫자 가독성 극대화)
    - 진한 텍스트 색상 적용
    """
    accent = _group_accent(group)
    tone_color = _tone_color(tone)
    bg = _soft_bg(accent)

    # 칩(Chip) 디자인: 배경은 연하게, 글씨는 진하게
    chip_html = f"<span style='background:{_hex_to_rgba(accent, 0.15)}; color:{accent}; padding:1px 8px; border-radius:4px; font-weight:700; font-size:0.75rem;'>{chip}</span>" if chip else ""

    # 델타(Delta) 디자인: 수치 변화 강조
    if delta is not None:
        delta_html = f"<span style='color:{tone_color}; font-weight:700; margin-left:auto; font-size:0.9rem;'>Δ {delta}</span>"
    else:
        delta_html = "<span style='opacity:0;'>Δ -</span>"

    # ✅ 핵심: border-left 6px와 진한 숫자 색상(#1E293B) 적용
    html = f"""
    <div class="dtro-card kpi-card-v2" style="
        background: {bg} !important; 
        border-left: 6px solid {accent} !important; 
        border: 1px solid #F1F5F9;
        border-radius: 8px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        margin-bottom: 12px;
    ">
        <div class="kpi-body" style="padding: 16px 20px;">
            <div class="kpi-title" style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
                <span style="color: #64748B; font-size: 0.9rem; font-weight: 600;">{label}</span>
                {chip_html}
                {delta_html}
            </div>
            <div style="color: #1E293B !important; font-size: 2.2rem; font-weight: 800; line-height: 1;">
                {value}
            </div>
        </div>
    </div>
    """
    _render(html)