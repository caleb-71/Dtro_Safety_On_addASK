# apps/streamlit/Home.py
from __future__ import annotations

from apps.streamlit._bootstrap import ensure_project_root_on_syspath
ensure_project_root_on_syspath()

import datetime as dt
from pathlib import Path
import streamlit as st

from core.config import get_settings
from core.logger import get_logger

# UI theme/layout
from apps.streamlit.ui.theme.layout import apply_layout, page_container, end_page, hero, section
from apps.streamlit.ui.components.kpi_cards import kpi_card

logger = get_logger(__name__)
settings = get_settings()

# --------------------------------------------------
# 페이지 설정
# --------------------------------------------------
st.set_page_config(
    page_title="DTRO 스마트 안전 포털",
    layout="wide",
)

# 전역 스타일 및 컨테이너 적용
apply_layout()
page_container()

# 메인 환영 배너 (Hero)
hero(
    "🛡️ DTRO 스마트 안전 포털",
    "대구교통공사 맞춤형 AI 안전 관리 및 규정 QA 시스템에 오신 것을 환영합니다."
)

# --------------------------------------------------
# 1) AI 안전 파트너(AX) 소개
# --------------------------------------------------
st.markdown(
    """
    <div style='margin-bottom: 24px; padding: 24px; background-color: rgba(124, 92, 255, 0.1); border-left: 5px solid #7C5CFF; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);'>
        <h3 style='margin-top: 0; color: #EAF0FF; letter-spacing: -0.5px;'>🤖 관리자님의 든든한 파트너, AX Safety-On</h3>
        <p style='color: #E0E0E0; font-size: 15px; line-height: 1.7; margin-bottom: 0;'>
            현장 관리자님의 업무 부담을 줄이고, 더 안전한 환경을 만들기 위해 아래와 같은 핵심 기능을 제공합니다.<br><br>
            ✔️ <strong>안전 데이터 분석:</strong> 동향 및 점검 데이터를 분석하여 사고 트렌드와 핵심 지표(KPI)를 한눈에 보여줍니다.<br>
            ✔️ <strong>보고서 초안 작성:</strong> 사고 상세 내역을 기반으로 격식 있는 CEO 브리핑 및 현장 점검 결과 보고서를 자동 생성합니다.<br>
            ✔️ <strong>AI 법규 자문:</strong> 방대한 산업안전보건법 및 내부 규정을 AI가 스스로 검색하여 정확하고 빠르게 답변합니다.
        </p>
    </div>
    """,
    unsafe_allow_html=True
)

st.markdown("<div style='margin-bottom: 30px;'></div>", unsafe_allow_html=True)


# --------------------------------------------------
# 2) 퀵 내비게이션 (핵심 메뉴 바로가기)
# --------------------------------------------------
section("🚀 퀵 내비게이션")
st.caption("원하시는 업무를 클릭하여 바로 이동하세요.")

# 큼직한 버튼으로 주요 화면 안내
col1, col2, col3, col4 = st.columns(4)

with col1:
    if st.button("📊 01. 현황 대시보드\n(전체 요약 브리핑)", use_container_width=True):
        st.switch_page("pages/01_현황_대시보드.py")
with col2:
    if st.button("🧾 02. 데이터 대시보드\n(데이터 원본 탐색)", use_container_width=True): # 06에서 02로 수정
        st.switch_page("pages/02_데이터_대시보드.py") # 파일 경로 수정
with col3:
    if st.button("📝 03. 사고 상세 분석\n(보고서 자동 생성)", use_container_width=True): # 02에서 03으로 수정
        st.switch_page("pages/03_사고_상세_및_보고서.py") # 파일 경로 수정
with col4:
    if st.button("📚 04. AI 규정 Q&A\n(안전 법규 챗봇)", use_container_width=True): # 03에서 04로 수정
        st.switch_page("pages/04_규정_QA.py") # 파일 경로 수정

st.markdown("<div style='margin-bottom: 30px;'></div>", unsafe_allow_html=True)


# --------------------------------------------------
# 3) 오늘의 시스템 및 데이터 상태 (초간단 요약)
# --------------------------------------------------
section("📊 오늘의 시스템 업데이트 상태")
st.caption("시스템에 연동된 최신 데이터 업데이트 기준일입니다.")

# 파일 경로
trend_path = settings.paths.data_dir / "csv" / "trend.csv"
smap_path = settings.paths.data_dir / "csv" / "safety_map.csv"
faiss_path = settings.paths.index_dir / settings.index.faiss_index_file

# 파일 존재 여부 및 수정 시간 체크 함수
def get_file_status(path: Path) -> tuple[str, str, str]:
    if path.exists():
        mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
        return "정상 연결", f"{mtime.strftime('%Y-%m-%d %H:%M')}", "ok"
    return "연결 끊김", "데이터 없음", "danger"

t_stat, t_date, t_tone = get_file_status(trend_path)
s_stat, s_date, s_tone = get_file_status(smap_path)
f_stat, f_date, f_tone = get_file_status(faiss_path)

k1, k2, k3 = st.columns(3)
with k1:
    kpi_card("사고 동향(trend) DB", t_date, delta=t_stat, tone=t_tone, chip="CSV")
with k2:
    kpi_card("안전지도(safety_map) DB", s_date, delta=s_stat, tone=s_tone, chip="CSV")
with k3:
    kpi_card("AI 규정 검색 엔진(RAG)", f_date, delta=f_stat, tone=f_tone, chip="Index")

# 페이지 종료 훅
end_page()