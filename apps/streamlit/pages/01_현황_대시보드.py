# apps/streamlit/pages/01_현황_대시보드.py
from __future__ import annotations

from apps.streamlit._bootstrap import ensure_project_root_on_syspath
ensure_project_root_on_syspath()

import datetime as dt
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import streamlit as st

from core.logger import get_logger
from shared.io.csv_loader import load_csv

# UI
from apps.streamlit.ui.theme.layout import apply_layout, page_container, end_page, section
from apps.streamlit.ui.components.kpi_cards import kpi_card
from apps.streamlit.ui.components.charts import bar_chart_card, heatmap_chart_card

# Services (표준화/피벗/리포트)
from backend.services.dashboard_service import (
    ensure_trend_standard_columns,
    ensure_smap_standard_columns,
    compute_kpis,
    weekly_trend,
    pivot_heatmap,
    make_analysis_section,
    generate_dashboard_summary_pdf,
)

logger = get_logger(__name__)

# =========================================================
# Page setup
# =========================================================
st.set_page_config(page_title="현황 요약", layout="wide")
apply_layout()
page_container()

st.markdown(
    """
    <div class="dtro-hero">
      <div class="dtro-hero-title">📊 01. 현황 요약 대시보드</div>
      <div class="dtro-hero-sub">trend(사고/동향) + safety_map(지도/점검) 기반 통합 요약</div>
    </div>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# Small utils (01 화면 전용)
# =========================================================
def _to_dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")

def _pct(n: int, d: int, digits: int = 1) -> str:
    if d <= 0:
        return "-"
    return f"{round((n / d) * 100, digits)}%"

def _safe_series(df: pd.DataFrame, col: str, default=None) -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)

@st.cache_data(show_spinner=False)
def _load_df(path: Path, mtime: float) -> pd.DataFrame:
    res = load_csv(path)
    df = res.df.copy()
    df.columns = [str(c).replace("\n", "").replace("\r", "").strip() for c in df.columns]
    return df

def _pick_first_existing(*paths: Path) -> Optional[Path]:
    for p in paths:
        if p.exists():
            return p
    return None

def _load_dataset_df(norm_path: Path, csv_path: Path, label: str) -> pd.DataFrame:
    chosen = _pick_first_existing(norm_path, csv_path)
    if not chosen:
        return pd.DataFrame()
    df = _load_df(chosen, chosen.stat().st_mtime)
    df.attrs["__source_path__"] = str(chosen)
    df.attrs["__source_label__"] = label
    return df

def _apply_date_range(df: pd.DataFrame, date_col: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    if df.empty or date_col not in df.columns:
        return df
    s = _to_dt(df[date_col])
    mask = (s.dt.date >= start) & (s.dt.date <= end)
    return df[mask].copy()

def _month_range(d: dt.date) -> Tuple[dt.date, dt.date]:
    start = d.replace(day=1)
    if d.month == 12:
        next_month = dt.date(d.year + 1, 1, 1)
    else:
        next_month = dt.date(d.year, d.month + 1, 1)
    end = next_month - dt.timedelta(days=1)
    return start, end

def _prev_month_range(d: dt.date) -> Tuple[dt.date, dt.date]:
    first_this, _ = _month_range(d)
    prev_end = first_this - dt.timedelta(days=1)
    return _month_range(prev_end)

def _year_range(d: dt.date) -> Tuple[dt.date, dt.date]:
    return dt.date(d.year, 1, 1), dt.date(d.year, 12, 31)

def _prev_year_range(d: dt.date) -> Tuple[dt.date, dt.date]:
    return dt.date(d.year - 1, 1, 1), dt.date(d.year - 1, 12, 31)

def _clean_key_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().replace({"nan": "미상", "None": "미상", "": "미상"})

def _repeat_findings_summary(fsdf: pd.DataFrame, *, group_cols, n: int):
    if fsdf.empty:
        return 0, 0, "-", 0
    tmp = fsdf.copy()
    for c in group_cols:
        if c not in tmp.columns:
            tmp[c] = "미상"
        tmp[c] = _clean_key_series(tmp[c])
    g = tmp.groupby(group_cols, dropna=False).size().reset_index(name="cnt")
    rg = g[g["cnt"] >= n].sort_values("cnt", ascending=False)
    group_cnt = int(len(rg))
    if group_cnt == 0:
        return 0, 0, "-", 0
    tmp["_grp_key"] = list(zip(*[tmp[c] for c in group_cols]))
    rg["_grp_key"] = list(zip(*[rg[c] for c in group_cols]))
    keyset = set(rg["_grp_key"].tolist())
    record_cnt = int(tmp["_grp_key"].isin(keyset).sum())
    top_row = rg.iloc[0].to_dict()
    top_label = " / ".join([str(top_row.get(c, "-")) for c in group_cols])
    top_cnt = int(top_row.get("cnt", 0))
    return group_cnt, record_cnt, top_label, top_cnt

def _download_pdf_button(pdf_path: Path, label: str) -> None:
    try:
        st.download_button(
            label=label,
            data=pdf_path.read_bytes(),
            file_name=pdf_path.name,
            mime="application/pdf",
        )
    except Exception as e:
        st.warning(f"PDF 다운로드 준비 실패: {e}")


# =========================================================
# Load data (norm first -> csv fallback) - 이 부분이 삭제되었었습니다!
# =========================================================
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"

trend_norm_path = DATA_DIR / "norm" / "trend" / "trend_norm.csv"
trend_csv_path = DATA_DIR / "csv" / "trend.csv"

smap_norm_path = DATA_DIR / "norm" / "safety_map" / "safety_map_norm.csv"
smap_csv_path = DATA_DIR / "csv" / "safety_map.csv"

with st.spinner("데이터 로딩 중..."):
    trend_df = _load_dataset_df(trend_norm_path, trend_csv_path, "trend")
    smap_df = _load_dataset_df(smap_norm_path, smap_csv_path, "safety_map")

if trend_df.empty and smap_df.empty:
    st.warning("trend / safety_map 데이터가 없습니다. data/norm 또는 data/csv 폴더를 확인하세요.")
    end_page()
    st.stop()

# 표준 컬럼 보장
if not trend_df.empty:
    trend_df = ensure_trend_standard_columns(trend_df)
if not smap_df.empty:
    smap_df = ensure_smap_standard_columns(smap_df)

with st.expander("🔍 데이터 로드 정보(디버그)"):
    st.write(f"- trend source: `{trend_df.attrs.get('__source_path__', '-')}`" if not trend_df.empty else "- trend source: 없음")
    st.write(f"- safety_map source: `{smap_df.attrs.get('__source_path__', '-')}`" if not smap_df.empty else "- safety_map source: 없음")


# =========================================================
# 현황 필터 (기간 + 사고/점검 유형) - 1줄 배치로 공간 최적화
# =========================================================
section("현황 필터")

today = dt.date.today()

def sync_dashboard_dates():
    p = st.session_state.dash01_common_preset
    t = dt.date.today()
    if p == "최근 7일":
        st.session_state.dash01_common_start = t - dt.timedelta(days=6)
        st.session_state.dash01_common_end = t
    elif p == "최근 30일":
        st.session_state.dash01_common_start = t - dt.timedelta(days=29)
        st.session_state.dash01_common_end = t
    elif p == "최근 90일":
        st.session_state.dash01_common_start = t - dt.timedelta(days=89)
        st.session_state.dash01_common_end = t
    elif p == "금월":
        st.session_state.dash01_common_start, st.session_state.dash01_common_end = _month_range(t)
    elif p == "전월":
        st.session_state.dash01_common_start, st.session_state.dash01_common_end = _prev_month_range(t)
    elif p == "금년":
        st.session_state.dash01_common_start, st.session_state.dash01_common_end = _year_range(t)
    elif p == "작년":
        st.session_state.dash01_common_start, st.session_state.dash01_common_end = _prev_year_range(t)

# 5개의 칸으로 나누어 가로 공간을 알차게 활용합니다.
c1, c2, c3, c4, c5 = st.columns([1.1, 1.1, 1.1, 1.3, 1.3])

with c1:
    preset = st.selectbox(
        "기간 선택",
        ["최근 7일", "최근 30일", "최근 90일", "금월", "전월", "금년", "작년", "사용자 지정"],
        index=1,
        key="dash01_common_preset",
        on_change=sync_dashboard_dates
    )

start = st.session_state.get("dash01_common_start", today - dt.timedelta(days=29))
end = st.session_state.get("dash01_common_end", today)

with c2:
    start = st.date_input("시작일", value=start, key="dash01_common_start")
with c3:
    end = st.date_input("종료일", value=end, key="dash01_common_end")

if start > end:
    st.error("시작일이 종료일보다 늦습니다. 날짜를 다시 선택하세요.")
    end_page()
    st.stop()

# 우측 공간에 들어갈 필터 목록 추출 (데이터 내 실제 존재하는 항목만)
trend_opts = ["전체"]
if not trend_df.empty and "incident_type" in trend_df.columns:
    trend_opts += sorted([x for x in trend_df["incident_type"].dropna().astype(str).unique() if x and x != "nan"])

smap_opts = ["전체"]
if not smap_df.empty and "issue_type" in smap_df.columns:
    smap_opts += sorted([x for x in smap_df["issue_type"].dropna().astype(str).unique() if x and x != "nan"])

with c4:
    trend_filter = st.selectbox("사고(trend) 유형", trend_opts, key="dash01_trend_filter")
with c5:
    smap_filter = st.selectbox("점검(safety_map) 지적유형", smap_opts, key="dash01_smap_filter")


# =========================================================
# 전처리 + 기간 및 필터 적용 (내부 기본값 고정)
# =========================================================
overdue_days = 14
repeat_n = 3
repeat_group_cols = ["place_main", "issue_type"]

trend_ready = pd.DataFrame()
smap_ready = pd.DataFrame()

if not trend_df.empty:
    trend_ready = _apply_date_range(trend_df.copy(), "occurred_at", start, end)
    # 추가된 사고유형 필터 적용
    if trend_filter != "전체":
        trend_ready = trend_ready[trend_ready["incident_type"].astype(str) == trend_filter]

if not smap_df.empty:
    smap_ready = _apply_date_range(smap_df.copy(), "checked_at", start, end)
    # 추가된 지적유형 필터 적용
    if smap_filter != "전체":
        smap_ready = smap_ready[smap_ready["issue_type"].astype(str) == smap_filter]

    if "action_completed_at" in smap_ready.columns and "checked_at" in smap_ready.columns:
        today_ts = pd.Timestamp(dt.date.today())
        smap_ready["경과일수"] = (today_ts - pd.to_datetime(smap_ready["checked_at"], errors="coerce")).dt.days
        smap_ready["조치소요일"] = (
            pd.to_datetime(smap_ready["action_completed_at"], errors="coerce")
            - pd.to_datetime(smap_ready["checked_at"], errors="coerce")
        ).dt.days
        smap_ready["지연여부"] = (smap_ready["조치상태"].astype(str) == "미조치") & (smap_ready["경과일수"] >= overdue_days)
    else:
        smap_ready["지연여부"] = False

trend_f = trend_ready.copy()
smap_f = smap_ready.copy()

st.caption(f"✅ 필터 결과: 사고(trend): {len(trend_f)}건 / 점검(safety_map): {len(smap_f)}건")


# =========================================================
# 핵심 KPI
# =========================================================
section("핵심 KPI")

# trend KPI 계산
t_total = len(trend_f)
t_status = _safe_series(trend_f, "조치상태", "미상").astype(str)
t_done = int((t_status == "완료").sum())
t_emergency = int((_safe_series(trend_f, "긴급신고", "").astype(str) == "119").sum())
t_cctv = int(
    _safe_series(trend_f, "cctv", _safe_series(trend_f, "CCTV유무", "")).astype(str).isin(
        ["유", "예", "Y", "YES", "True", "TRUE", "1"]
    ).sum()
)
t_latest = trend_f["occurred_at"].max() if (not trend_f.empty and "occurred_at" in trend_f.columns) else pd.NaT

# safety_map KPI 계산
s_total = len(smap_f)
s_status = _safe_series(smap_f, "조치상태", "미조치").astype(str)
s_done = int((s_status == "완료").sum())
s_open = int((s_status == "미조치").sum())
s_overdue = int(_safe_series(smap_f, "지연여부", False).astype(bool).sum())
s_latest = smap_f["checked_at"].max() if (not smap_f.empty and "checked_at" in smap_f.columns) else pd.NaT

# 반복지적 계산
rep_group_cnt, rep_record_cnt, rep_top_label, rep_top_cnt = _repeat_findings_summary(
    smap_f, group_cols=repeat_group_cols, n=repeat_n
)


# --- 1) 사고/동향 (Trend) 구역 ---
st.markdown(
    """
    <div style="background-color: rgba(0, 212, 255, 0.1); border-left: 4px solid #00D4FF; padding: 8px 16px; border-radius: 6px; margin-bottom: 12px; margin-top: 10px;">
        <strong style="color: #EAF0FF; font-size: 15px; letter-spacing: -0.5px;">📈 사고/동향 (Trend) 현황</strong>
    </div>
    """,
    unsafe_allow_html=True
)

krow1 = st.columns(5)
with krow1[0]:
    kpi_card("사고 건수", t_total, tone="info", chip="trend", group="trend")
with krow1[1]:
    kpi_card("사고 조치완료", t_done, delta=_pct(t_done, t_total), tone="ok", chip="완료", group="trend")
with krow1[2]:
    kpi_card("사고 119 신고", t_emergency, tone="critical", chip="비상", group="trend")
with krow1[3]:
    kpi_card("사고 CCTV 확보", t_cctv, tone="info", chip="증거", group="trend")
with krow1[4]:
    kpi_card("사고 최근일", t_latest.date() if pd.notna(t_latest) else "-", tone="info", chip="최신", group="trend")

st.markdown("<div style='margin-bottom: 18px;'></div>", unsafe_allow_html=True) # 두 구역 사이의 간격


# --- 2) 안전지도/점검 (Safety Map) 구역 ---
st.markdown(
    """
    <div style="background-color: rgba(34, 197, 94, 0.1); border-left: 4px solid #22C55E; padding: 8px 16px; border-radius: 6px; margin-bottom: 12px;">
        <strong style="color: #EAF0FF; font-size: 15px; letter-spacing: -0.5px;">🛠️ 안전지도/점검 (Safety Map) 현황</strong>
    </div>
    """,
    unsafe_allow_html=True
)

krow2 = st.columns(5)
with krow2[0]:
    kpi_card("점검 건수", s_total, tone="info", chip="safety_map", group="safety_map")
with krow2[1]:
    kpi_card("점검 미조치", s_open, delta=_pct(s_open, s_total), tone="danger", chip="리스크", group="safety_map")
with krow2[2]:
    kpi_card("점검 지연", s_overdue, delta=_pct(s_overdue, s_total), tone="warn", chip=f"≥{overdue_days}일", group="safety_map")
with krow2[3]:
    kpi_card(f"반복지적 그룹(≥{repeat_n})", rep_group_cnt, tone="critical", chip="패턴", group="safety_map")
with krow2[4]:
    kpi_card("점검 최근일", s_latest.date() if pd.notna(s_latest) else "-", tone="info", chip="최신", group="safety_map")

# =========================================================
# 요약 차트
# =========================================================
section("요약 차트")

c1, c2 = st.columns(2)
with c1:
    if trend_f.empty:
        st.info("사고(trend) 데이터가 없어 차트를 표시할 수 없습니다.")
    else:
        series = _safe_series(trend_f, "incident_type", _safe_series(trend_f, "사고유형", "미상"))
        bar_chart_card(title="사고유형 TOP 10", series=series.value_counts(), top_n=10)

with c2:
    if smap_f.empty:
        st.info("점검(safety_map) 데이터가 없어 차트를 표시할 수 없습니다.")
    else:
        series = _safe_series(smap_f, "issue_type", _safe_series(smap_f, "지적유형", "미상"))
        bar_chart_card(title="지적유형 TOP 10", series=series.value_counts(), top_n=10)

c3, c4 = st.columns(2)
with c3:
    if trend_f.empty:
        st.info("사고(trend) 데이터가 없어 차트를 표시할 수 없습니다.")
    else:
        series = _safe_series(trend_f, "line", _safe_series(trend_f, "호선", "미상"))
        bar_chart_card(title="호선별 사고 건수", series=series.value_counts())

with c4:
    if smap_f.empty:
        st.info("점검(safety_map) 데이터가 없어 차트를 표시할 수 없습니다.")
    else:
        series = _safe_series(smap_f, "action_type", _safe_series(smap_f, "조치구분", "미상"))
        bar_chart_card(title="조치구분 분포", series=series.value_counts())


# =========================================================
# 히트맵(요약 피벗)
# =========================================================
section("히트맵(요약 피벗)")

h1, h2 = st.columns(2)
with h1:
    if trend_f.empty:
        st.info("trend 데이터가 없어 히트맵을 표시할 수 없습니다.")
    else:
        pivot_t = pivot_heatmap(trend_f, row_dim="station", col_dim="incident_type", top_n=12)
        heatmap_chart_card(title="trend 히트맵", pivot_df=pivot_t, subtitle="역 × 사고유형 (상위 12)", height=360)

with h2:
    if smap_f.empty:
        st.info("safety_map 데이터가 없어 히트맵을 표시할 수 없습니다.")
    else:
        pivot_s = pivot_heatmap(smap_f, row_dim="target_dept", col_dim="issue_type", top_n=12)
        heatmap_chart_card(title="safety_map 히트맵", pivot_df=pivot_s, subtitle="부서 × 지적유형 (상위 12)", height=360)


# =========================================================
# 대시보드 요약 PDF
# =========================================================
section("📄 대시보드 요약 PDF (방법2)")

st.caption("01 화면의 현재 필터 결과를 기반으로, KPI/주간추세/피벗(히트맵 테이블) 요약을 PDF로 생성합니다.")

pdf_col1, pdf_col2, pdf_col3 = st.columns([1.2, 1.2, 2.6])
with pdf_col1:
    make_trend_pdf = st.button("📄 trend 요약 PDF 생성", type="primary", disabled=trend_f.empty)
with pdf_col2:
    make_smap_pdf = st.button("📄 safety_map 요약 PDF 생성", type="primary", disabled=smap_f.empty)
with pdf_col3:
    st.info("TIP: 02 단건 보고서와 달리, 01은 ‘필터 결과 전체 현황’을 요약합니다.")

if make_trend_pdf:
    try:
        with st.spinner("trend 요약 PDF 생성 중..."):
            kpis = compute_kpis(trend_f, "occurred_at", top_dims=["station", "place_main", "incident_type"])
            weekly = weekly_trend(trend_f, "occurred_at")
            pivot = pivot_heatmap(trend_f, row_dim="station", col_dim="incident_type", top_n=15)
            analysis = make_analysis_section(kpis, weekly, pivot, pivot_label="역×사고유형")
            filter_title = f"trend | 기간={start}~{end} | (01 현황 필터 결과 기반)"
            pdf_path = generate_dashboard_summary_pdf(
                output_filename=None,
                filter_title=filter_title,
                analysis_section=analysis,
            )

        st.success("trend 요약 PDF 생성 완료!")
        st.write(f"저장 위치: {pdf_path}")
        _download_pdf_button(pdf_path, "⬇️ trend 요약 PDF 다운로드")

    except Exception as e:
        st.error(f"trend 요약 PDF 생성 실패: {e}")
        logger.exception(e)

if make_smap_pdf:
    try:
        with st.spinner("safety_map 요약 PDF 생성 중..."):
            kpis = compute_kpis(smap_f, "checked_at", top_dims=["target_dept", "issue_type", "check_category"])
            weekly = weekly_trend(smap_f, "checked_at")
            pivot = pivot_heatmap(smap_f, row_dim="target_dept", col_dim="issue_type", top_n=15)
            analysis = make_analysis_section(kpis, weekly, pivot, pivot_label="부서×지적유형")
            filter_title = f"safety_map | 기간={start}~{end} | (01 현황 필터 결과 기반)"
            pdf_path = generate_dashboard_summary_pdf(
                output_filename=None,
                filter_title=filter_title,
                analysis_section=analysis,
            )

        st.success("safety_map 요약 PDF 생성 완료!")
        st.write(f"저장 위치: {pdf_path}")
        _download_pdf_button(pdf_path, "⬇️ safety_map 요약 PDF 다운로드")

    except Exception as e:
        st.error(f"safety_map 요약 PDF 생성 실패: {e}")
        logger.exception(e)

# =========================================================
# 안내
# =========================================================
section("상세 분석 안내")

st.markdown(
    """
- **01 페이지는 ‘요약’ 화면**입니다. (사고 + 점검 핵심지표를 한눈에)
- 상세 차트/원본/다운로드/반복지적 드릴다운은 **02_데이터_대시보드**에서 확인하세요.
- 사고 상세 및 PDF 보고서는 **03_사고_상세_및_보고서** 페이지에서 수행합니다.
"""
)

st.caption(f"🔎 반복지적 TOP: {rep_top_label} (반복 {rep_top_cnt}회) / 반복 레코드: {rep_record_cnt}건")
end_page()