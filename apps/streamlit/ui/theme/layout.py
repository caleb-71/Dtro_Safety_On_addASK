# apps/streamlit/ui/theme/layout.py
from __future__ import annotations

from pathlib import Path
import streamlit as st

# TOKENS는 선택(없어도 동작)
try:
    from apps.streamlit.ui.theme.tokens import TOKENS  # type: ignore
except Exception:
    TOKENS = {
        "bg": "#0B1020",
        "text": "#E5E7EB",
        "muted": "rgba(229,231,235,0.72)",
        "card_text": "#0B1020",
    }

# 세션 키(페이지 이동 시에도 CSS를 1회만 안정적으로 적용)
_CSS_APPLIED_KEY = "_dtro_css_applied"


def _read_css() -> str:
    """
    styles.css 내용을 읽어옴.
    - 파일 없으면 빈 문자열 반환
    """
    css_path = Path(__file__).with_name("styles.css")
    if not css_path.exists():
        return ""
    return css_path.read_text(encoding="utf-8")


def _inject_css(css: str):
    """
    CSS를 Streamlit 앱에 주입
    """
    if not css.strip():
        return
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def apply_layout(force: bool = False):
    css = _read_css()
    _inject_css(css)
    st.session_state[_CSS_APPLIED_KEY] = True

    css = _read_css()
    _inject_css(css)

    # 필요 시 추가적인 인라인 CSS를 여기서 더 주입 가능
    # (예: 특정 Streamlit 요소 미세 조정)

    st.session_state[_CSS_APPLIED_KEY] = True


def hero(title: str, subtitle: str = ""):
    """
    페이지 상단 Hero 배너
    """
    st.markdown(
        f"""
        <div class="dtro-hero">
          <div class="dtro-hero-title">{title}</div>
          <div class="dtro-hero-sub">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section(title: str):
    """
    섹션 타이틀(헤더처럼 굵게)
    """
    st.markdown(
        f"<div class='dtro-section-title'>{title}</div>",
        unsafe_allow_html=True,
    )


def page_container():
    """
    (선택) 페이지 컨테이너 시작
    - 현재는 CSS 중심으로 레이아웃을 잡고 있어서 함수는 훅(hook) 역할만 수행
    - 추후 필요하면 여기서 공통 상단 안내, 공통 경고 배너 등 넣어도 됨
    """
    # 예: 공통 알림/환경 표시 등을 넣고 싶다면 여기에
    return


def end_page():
    """
    (선택) 페이지 컨테이너 종료
    - 현재는 별도 마무리 로직 없음
    - 추후 footer / build info 등을 넣을 수 있음
    """
    return
