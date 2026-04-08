# apps/streamlit/ui/theme/tokens.py
from __future__ import annotations

TOKENS = {
    # Base (dark)
    "bg": "#0B1020",
    "bg2": "#0F172A",
    "text": "#E5E7EB",
    "muted_text": "rgba(229,231,235,0.72)",

    # Light cards on dark
    "card_bg": "rgba(255,255,255,0.94)",
    "card_border": "rgba(15, 23, 42, 0.10)",
    "card_shadow": "0 10px 24px rgba(0,0,0,0.22)",
    "card_text": "#0B1020",
    "card_muted": "rgba(15,23,42,0.60)",

    # Accent
    "primary": "#7C5CFF",
    "secondary": "#00D4FF",

    # Status colors (importance-based)
    "ok": "#16A34A",         # 완료/정상
    "warn": "#F59E0B",       # 지연/주의
    "danger": "#EF4444",     # 미조치/위험
    "info": "#3B82F6",       # 일반/정보
    "critical": "#A855F7",   # 중요(시정지시/비상/화재 등)
}

# 막대차트 기본 팔레트(화려하지만 “깔끔한” 톤)
# 상위 N개는 진하게, 나머지는 톤다운으로(차트에서 중요도 차등에 사용)
CHART_PALETTE = [
    "#7C5CFF", "#00D4FF", "#EF4444", "#F59E0B", "#16A34A",
    "#3B82F6", "#A855F7", "#22C55E", "#FB7185", "#60A5FA",
]
