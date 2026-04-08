# apps/streamlit/pages/03_사고_상세_및_보고서.py
from __future__ import annotations

from apps.streamlit._bootstrap import ensure_project_root_on_syspath
ensure_project_root_on_syspath()

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st
from core.config import get_settings
from core.logger import get_logger

# [신규 추가] 예쁜 옷(CSS)과 타이틀 함수를 가져옵니다!
from apps.streamlit.ui.theme.layout import apply_layout, page_container, hero

# Plotly(컬러풀 차트용)
import plotly.express as px

from core.config import get_settings
from core.logger import get_logger
from shared.io.csv_loader import load_csv

from backend.mappers.trend_mapper import map_trend_row_to_incident_data
from backend.mappers.safety_map_mapper import map_safety_map_row_to_safety_map_data
from backend.services.report_service import (
    ReportService,
    build_similar_context_from_trend_df,
    build_similar_context_from_smap_df,
)

# ✅ 분석/코호트/대시보드 요약 서비스
from backend.services.dashboard_service import (
    ensure_trend_standard_columns,
    ensure_smap_standard_columns,
    compute_kpis,
    weekly_trend,
    pivot_heatmap,
    make_analysis_section,
    build_trend_cohort_df,
    build_smap_cohort_df,
)

logger = get_logger(__name__)
settings = get_settings()

# --- 화면 세팅 영역 ---
st.set_page_config(page_title="사고/점검 상세 및 보고서", layout="wide")

# [신규 추가] 화면 전체에 예쁜 옷(CSS)을 입힙니다!
apply_layout()
page_container()

# [수정] 다시 깔끔한 hero 함수를 사용합니다.
hero(
    "🧾 03. 사고/점검 상세 및 보고서",
    "trend(사고) / safety_map(점검) 선택 → 필터/목록 → 1건 상세 → 분석(차트/피벗) → PDF 보고서"
)

# =========================================================
# 공통 유틸
# =========================================================
def _to_dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def _pick_first_existing(*paths: Path) -> Optional[Path]:
    for p in paths:
        if p.exists():
            return p
    return None


@st.cache_data(show_spinner=False)
def _load_df(path: Path, mtime: float) -> pd.DataFrame:
    """CSV 로더(공통). mtime을 넣어야 파일이 바뀌면 캐시 갱신됨."""
    res = load_csv(path)
    df = res.df.copy()
    df.columns = [str(c).replace("\n", "").replace("\r", "").strip() for c in df.columns]
    return df


def _json_sanitize(obj: Any) -> Any:
    """st.json / json.dumps에서 Timestamp/NaN 때문에 죽지 않게 변환."""
    if isinstance(obj, dict):
        return {str(k): _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_sanitize(v) for v in obj]

    try:
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        try:
            if pd.isna(obj):
                return None
        except Exception:
            pass
    except Exception:
        pass

    from datetime import datetime as _dt, date as _date

    if isinstance(obj, (_dt, _date)):
        return obj.isoformat()

    return obj


def _safe_st_json(obj: Any) -> None:
    st.json(_json_sanitize(obj))


def _safe_series(df: pd.DataFrame, col: str, default="") -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)


def _clip_text(s: str, max_chars: int) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if len(t) <= max_chars:
        return t
    return t[:max_chars] + "\n...(truncated)"


def _date_range_defaults(dt_series: pd.Series) -> tuple[date, date]:
    tmp = pd.to_datetime(dt_series, errors="coerce")
    tmp = tmp[tmp.notna()]
    if tmp.empty:
        today = datetime.now().date()
        return today, today
    mn = tmp.min().date()
    mx = tmp.max().date()
    return mn, mx


def _apply_date_filter(df: pd.DataFrame, col: str, d1: date, d2: date) -> pd.DataFrame:
    if col not in df.columns:
        return df
    tmp = df.copy()
    tmp[col] = pd.to_datetime(tmp[col], errors="coerce")
    start = pd.Timestamp(d1)
    end = pd.Timestamp(d2) + pd.Timedelta(days=1)  # inclusive
    return tmp[tmp[col].isna() | ((tmp[col] >= start) & (tmp[col] < end))]


def _download_filtered_csv_button(df: pd.DataFrame, filename: str, label: str) -> None:
    try:
        csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(label=label, data=csv_bytes, file_name=filename, mime="text/csv")
    except Exception as e:
        st.warning(f"CSV 생성 실패: {e}")


# =========================================================
# UI 공통: 강조 박스 스타일
# =========================================================
def _box(title: str, body: str = ""):
    st.markdown(
        f"""
        <div style="
            border: 2px solid #4B8BFF;
            border-radius: 10px;
            padding: 10px 12px;
            background: #F6FAFF;
            margin-bottom: 10px;">
            <div style="font-weight:700; font-size:16px; margin-bottom:6px;">{title}</div>
            <div style="font-size:13px;">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# 리소스 캐시
# =========================================================
@st.cache_resource
def get_report_service() -> ReportService:
    return ReportService()


svc = get_report_service()


# =========================================================
# 데이터 로드 (trend / safety_map)
# =========================================================
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"

trend_norm_path = DATA_DIR / "norm" / "trend" / "trend_norm.csv"
trend_csv_path = DATA_DIR / "csv" / "trend.csv"
smap_norm_path = DATA_DIR / "norm" / "safety_map" / "safety_map_norm.csv"
smap_csv_path = DATA_DIR / "csv" / "safety_map.csv"

chosen_trend = _pick_first_existing(trend_norm_path, trend_csv_path)
chosen_smap = _pick_first_existing(smap_norm_path, smap_csv_path)

with st.expander("📌 데이터 경로(디버그)"):
    st.write(f"- trend: `{chosen_trend}`" if chosen_trend else "- trend: (없음)")
    st.write(f"- safety_map: `{chosen_smap}`" if chosen_smap else "- safety_map: (없음)")

trend_df = pd.DataFrame()
smap_df = pd.DataFrame()

if chosen_trend:
    trend_df = _load_df(chosen_trend, chosen_trend.stat().st_mtime)
    trend_df = ensure_trend_standard_columns(trend_df)

if chosen_smap:
    smap_df = _load_df(chosen_smap, chosen_smap.stat().st_mtime)
    smap_df = ensure_smap_standard_columns(smap_df)

if trend_df.empty and smap_df.empty:
    st.error("trend / safety_map 데이터가 없습니다. data/norm 또는 data/csv 폴더를 확인하세요.")
    st.stop()


# =========================================================
# 1) 데이터셋 선택
# =========================================================
st.subheader("1) 데이터셋 선택")

dataset_options: List[str] = []
if not trend_df.empty:
    dataset_options.append("trend")
if not smap_df.empty:
    dataset_options.append("safety_map")

dataset_label = {"trend": "trend(사고)", "safety_map": "safety_map(점검)"}

selected_dataset = st.radio(
    "분류",
    options=dataset_options,
    format_func=lambda x: dataset_label.get(x, x),
    horizontal=True,
)

st.divider()


# =========================================================
# TREND (사고)
# =========================================================
if selected_dataset == "trend":
    _box("📌 trend(사고) 필터", "필터 결과 기반 KPI/주간추세/피벗 분석 및 PDF 보고서를 생성합니다.")

    # 날짜 범위 필터
    dmin, dmax = _date_range_defaults(trend_df["occurred_at"] if "occurred_at" in trend_df.columns else pd.Series([]))
    c1, c2, c3, c4, c5 = st.columns([1.4, 1.4, 1.2, 1.2, 1.8])

    with c1:
        date_from = st.date_input("발생일(From)", value=dmin)
    with c2:
        date_to = st.date_input("발생일(To)", value=dmax)
    with c3:
        station_opts = ["(전체)"] + sorted(_safe_series(trend_df, "station", "").dropna().astype(str).unique().tolist())
        station = st.selectbox("station", station_opts)
    with c4:
        itype_opts = ["(전체)"] + sorted(_safe_series(trend_df, "incident_type", "").dropna().astype(str).unique().tolist())
        incident_type = st.selectbox("incident_type", itype_opts)
    with c5:
        status_opts = ["(전체)"] + sorted(_safe_series(trend_df, "조치상태", "").dropna().astype(str).unique().tolist())
        status = st.selectbox("조치상태", status_opts)

    k1, k2 = st.columns([2, 2])
    with k1:
        place_kw = st.text_input("place_main 키워드(포함 검색)", value="")
    with k2:
        q_text = st.text_input("추가 키워드(요약/원인/조치 등)", value="")

    fdf = trend_df.copy()
    fdf = _apply_date_filter(fdf, "occurred_at", date_from, date_to)

    if station != "(전체)" and "station" in fdf.columns:
        fdf = fdf[fdf["station"].astype(str) == station]
    if incident_type != "(전체)" and "incident_type" in fdf.columns:
        fdf = fdf[fdf["incident_type"].astype(str) == incident_type]
    if status != "(전체)" and "조치상태" in fdf.columns:
        fdf = fdf[fdf["조치상태"].astype(str) == status]

    if place_kw.strip() and "place_main" in fdf.columns:
        fdf = fdf[fdf["place_main"].astype(str).str.contains(place_kw.strip(), case=False, na=False)]

    if q_text.strip():
        q = q_text.strip()
        search_cols = [
            c for c in [
                "station", "line", "place_main", "incident_type", "__summary__",
                "사고원인", "cause", "상세원인", "cause_detail",
                "조치", "actions_taken", "기타", "misc"
            ] if c in fdf.columns
        ]
        if search_cols:
            mask = pd.Series(False, index=fdf.index)
            for c in search_cols:
                mask = mask | fdf[c].astype(str).str.contains(q, case=False, na=False)
            fdf = fdf[mask]

    st.caption(f"필터 결과: {len(fdf)}건 / 전체: {len(trend_df)}건")
    _download_filtered_csv_button(fdf, "trend_filtered.csv", "⬇️ (trend) 필터 결과 CSV 다운로드")

    # 목록
    list_cols = [c for c in ["row_id", "occurred_at", "occurred_time", "line", "station", "place_main", "incident_type", "조치상태", "report_type"] if c in fdf.columns]
    view = fdf[list_cols].copy()
    if "occurred_at" in view.columns:
        view["occurred_at"] = pd.to_datetime(view["occurred_at"], errors="coerce").dt.date
    st.dataframe(view.reset_index(drop=True), use_container_width=True, height=320)

    if fdf.empty:
        st.info("필터 조건에 해당하는 사고가 없습니다.")
        st.stop()

    st.subheader("2) 1건 선택")
    options = fdf["row_id"].astype(str).tolist()
    selected_row_id = st.selectbox("선택 row_id", options=options, index=0)

    sel = fdf[fdf["row_id"].astype(str) == str(selected_row_id)]
    row = sel.iloc[0].to_dict() if not sel.empty else {}
    incident_data = map_trend_row_to_incident_data(row)

    # ✅ 화면(02) 분석 탭은 '필터 결과(fdf) 기반'
    kpis = compute_kpis(fdf, "occurred_at", top_dims=["station", "place_main", "incident_type"])
    weekly = weekly_trend(fdf, "occurred_at")
    pivot = pivot_heatmap(fdf, row_dim="station", col_dim="incident_type", top_n=15)
    analysis_section = make_analysis_section(kpis, weekly, pivot, pivot_label="역×사고유형")

    tab_detail, tab_analysis, tab_pdf = st.tabs(["🧾 상세", "📊 분석(차트/피벗)", "📄 PDF 보고서"])

    with tab_detail:
        _box("✅ 사고 개요(incident_data)", "표준 incident_data 기준으로 표시합니다.")
        _safe_st_json({k: incident_data.get(k) for k in ["report_id", "incident_datetime", "category", "line", "station", "detail_location", "accident_type", "severity", "current_status"]})

        st.markdown("### ✅ 사고 내용")
        _safe_st_json({k: incident_data.get(k) for k in ["summary", "timeline", "actions_taken", "weather", "cctv", "related_train", "reporter"]})

        with st.expander("🧩 extra 전체(디버그)"):
            _safe_st_json(incident_data.get("extra", {}) or {})

    with tab_analysis:
        _box("📌 필터 결과 기반 KPI", "KPI/추세/피벗은 필터된 결과(fdf) 기준입니다.")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("총 건수", int(kpis.get("total_cnt", 0)))
        with c2:
            st.metric("월평균(기간 내)", f"{kpis.get('monthly_avg', 0):.2f}")
        with c3:
            st.metric("최근 30일", int(kpis.get("last30_cnt", 0)))
        with c4:
            st.metric("직전 30일", int(kpis.get("prev30_cnt", 0)))

        st.caption(f"증감률(growth): {kpis.get('growth', 0.0):+.2f}  | 기준일(max): {kpis.get('max_dt','-')}")
        if kpis.get("note"):
            st.info(kpis["note"])

        # 월/주 추세
        st.markdown("### 📈 월별 / 주간 추세(컬러풀)")
        tmp = fdf.copy()
        tmp["occurred_at"] = pd.to_datetime(tmp["occurred_at"], errors="coerce")
        t2 = tmp[tmp["occurred_at"].notna()].copy()

        if not t2.empty:
            t2["yyyymm"] = t2["occurred_at"].dt.to_period("M").astype(str)
            m = t2.groupby("yyyymm").size().reset_index(name="count")
            fig_m = px.line(m, x="yyyymm", y="count", markers=True, title="월별 발생 추세")
            st.plotly_chart(fig_m, use_container_width=True)

            w = weekly.reset_index()
            w.columns = ["week", "count"]
            fig_w = px.line(w, x="week", y="count", markers=True, title="주간(Week) 발생 추세")
            st.plotly_chart(fig_w, use_container_width=True)
        else:
            st.info("유효한 발생일자가 없어 추세 차트를 그릴 수 없습니다.")

        st.markdown("### 📊 Top 분류(컬러풀)")
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            if "incident_type" in fdf.columns:
                vc = fdf["incident_type"].astype(str).value_counts().head(15).reset_index()
                vc.columns = ["incident_type", "count"]
                st.plotly_chart(px.bar(vc, x="incident_type", y="count", title="사고유형 Top 15"), use_container_width=True)
        with cc2:
            if "station" in fdf.columns:
                vc = fdf["station"].astype(str).value_counts().head(15).reset_index()
                vc.columns = ["station", "count"]
                st.plotly_chart(px.bar(vc, x="station", y="count", title="역 Top 15"), use_container_width=True)
        with cc3:
            if "조치상태" in fdf.columns:
                vc = fdf["조치상태"].astype(str).value_counts().head(10).reset_index()
                vc.columns = ["조치상태", "count"]
                st.plotly_chart(px.bar(vc, x="조치상태", y="count", title="조치상태 분포"), use_container_width=True)

        st.markdown("### 🔥 피벗(역 × 사고유형) 히트맵")
        if pivot is not None and not pivot.empty:
            fig_h = px.imshow(
                pivot.values,
                x=[str(c) for c in pivot.columns],
                y=[str(i) for i in pivot.index],
                text_auto=True,
                aspect="auto",
                title="역 × 사고유형 (건수)",
            )
            st.plotly_chart(fig_h, use_container_width=True)
            st.dataframe(pivot.style.background_gradient(axis=None), use_container_width=True)
        else:
            st.info("피벗을 만들 수 없습니다(데이터/컬럼 부족).")

    with tab_pdf:
        _box("📄 PDF 보고서 생성", "trend는 LLM 기반 보고서 + (6번 통계 섹션 강제 주입)으로 생성합니다.")
        st.caption("02에서는 RAG(규정검색)를 사용하지 않습니다. reg_context='' (필요시 03에서 수행)")

        # ✅ 6번 통계: 코호트 기반으로 만들지 여부(선택 1건 기준)
        use_cohort_for_report = st.checkbox(
            "보고서 6번(통계/추세/히트맵)을 '선택 1건 유사 코호트' 기준으로 생성",
            value=True,
            key="trend_use_cohort_pdf6",
        )
        cohort_cols = st.columns([1, 1, 1, 2])
        with cohort_cols[0]:
            cohort_months = st.number_input("코호트 최근 N개월(0=전체)", min_value=0, max_value=60, value=12, step=1, key="trend_cohort_months")
        with cohort_cols[1]:
            cohort_use_station = st.checkbox("역 포함", value=True, key="trend_cohort_station")
        with cohort_cols[2]:
            cohort_use_place = st.checkbox("장소 포함(좁아질 수 있음)", value=False, key="trend_cohort_place")
        with cohort_cols[3]:
            st.caption("권장: 사고유형 + 역(기본). 장소까지 포함하면 0건이 될 수 있습니다.")

        use_similar = st.checkbox("유사사고 분석 요약 포함(trend_norm 기반)", value=True)
        sim_cols = st.columns([1, 1, 2])
        with sim_cols[0]:
            sim_months = st.number_input("유사요약 최근 N개월(0=전체)", min_value=0, max_value=60, value=12, step=1, key="trend_sim_months")
        with sim_cols[1]:
            sim_max_cases = st.number_input("대표사례 최대", min_value=3, max_value=50, value=15, step=1, key="trend_sim_max")
        with sim_cols[2]:
            st.caption("유사사고 분석은 ‘재발 패턴 근거’를 추가해 보고서 품질을 올립니다.")

        make_btn = st.button("📄 (trend) PDF 보고서 생성", type="primary")

        def _make_similar_cache_key(rid: str, months: Optional[int], max_cases: int, use_sim: bool) -> str:
            return f"sim::trend::{rid}::{use_sim}::{months}::{max_cases}"

        def _get_similar_context(incident: Dict[str, Any], rid: str, months: Optional[int], max_cases: int) -> str:
            ck = _make_similar_cache_key(rid, months, max_cases, True)
            if ck in st.session_state:
                return st.session_state.get(ck, "")

            sc = ""
            try:
                norm_path = trend_norm_path
                if norm_path.exists():
                    norm_df = _load_df(norm_path, norm_path.stat().st_mtime)
                    norm_df = ensure_trend_standard_columns(norm_df)
                    sc = build_similar_context_from_trend_df(
                        trend_df=norm_df,
                        incident_data=incident,
                        months=months,
                        max_cases=int(max_cases),
                    )
                else:
                    sc = build_similar_context_from_trend_df(
                        trend_df=trend_df,
                        incident_data=incident,
                        months=months,
                        max_cases=int(max_cases),
                    )
            except Exception as e:
                logger.exception(e)
                sc = ""

            st.session_state[ck] = sc
            return sc

        if make_btn:
            months_opt = None if int(sim_months) == 0 else int(sim_months)
            similar_context = ""

            if use_similar:
                with st.spinner("유사사고 분석 요약 생성 중..."):
                    similar_context = _get_similar_context(
                        incident=incident_data,
                        rid=str(selected_row_id),
                        months=months_opt,
                        max_cases=int(sim_max_cases),
                    )

            # ✅ PDF에 들어갈 6번 통계 섹션 결정
            analysis_for_pdf = analysis_section  # 기본: 필터 결과 기반
            if use_cohort_for_report:
                with st.spinner("선택 1건 유사 코호트 통계 산출 중..."):
                    m = None if int(cohort_months) == 0 else int(cohort_months)
                    cohort_df = build_trend_cohort_df(
                        trend_df=trend_df,  # 전체 원본에서 코호트 뽑는 게 안정적
                        selected_row_id=str(selected_row_id),
                        months=m,
                        use_type=True,
                        use_station=bool(cohort_use_station),
                        use_place=bool(cohort_use_place),
                    )

                    if cohort_df is None or cohort_df.empty:
                        st.warning("코호트 조건으로 유사 건이 없어, 보고서 6번은 현재 필터 기반 통계를 사용합니다.")
                    else:
                        k_c = compute_kpis(cohort_df, "occurred_at", top_dims=["station", "place_main", "incident_type"])
                        w_c = weekly_trend(cohort_df, "occurred_at")
                        p_c = pivot_heatmap(cohort_df, row_dim="station", col_dim="incident_type", top_n=15)
                        analysis_for_pdf = make_analysis_section(k_c, w_c, p_c, pivot_label="(코호트) 역×사고유형")

            with st.spinner("PDF 생성 중... (Ollama LLM → PDF 렌더링)"):
                try:
                    # ✅ ReportService가 analysis_section을 받아 6번 섹션에 주입한다는 전제
                    result = svc.generate_report_pdf(
                        incident_data=incident_data,
                        reg_context="",
                        similar_context=similar_context if use_similar else "",
                        analysis_section=analysis_for_pdf,  # ✅ 핵심
                        output_filename=None,
                    )

                    st.success("PDF 생성 완료!")
                    st.write(f"저장 위치: {result.pdf_path}")

                    pdf_bytes = result.pdf_path.read_bytes()
                    st.download_button(
                        label="⬇️ PDF 다운로드",
                        data=pdf_bytes,
                        file_name=result.pdf_path.name,
                        mime="application/pdf",
                    )

                    with st.expander("🧠 similar_context(디버그)"):
                        st.text(_clip_text(similar_context, 2500) if similar_context else "(없음)")

                    with st.expander("📌 PDF에 넣은 6번 통계 섹션(디버그)"):
                        _safe_st_json(analysis_for_pdf)

                except Exception as e:
                    st.error(f"PDF 생성 실패: {e}")
                    logger.exception(e)


# =========================================================
# SAFETY_MAP (점검)
# =========================================================
elif selected_dataset == "safety_map":
    _box("📌 safety_map(점검) 필터", "필터 결과 기반 KPI/주간추세/피벗 분석 및 PDF 보고서를 생성합니다.")

    dmin, dmax = _date_range_defaults(smap_df["checked_at"] if "checked_at" in smap_df.columns else pd.Series([]))
    c1, c2, c3, c4, c5 = st.columns([1.4, 1.4, 1.2, 1.2, 1.8])

    with c1:
        date_from = st.date_input("점검일(From)", value=dmin, key="smap_date_from")
    with c2:
        date_to = st.date_input("점검일(To)", value=dmax, key="smap_date_to")
    with c3:
        cat_opts = ["(전체)"] + sorted(_safe_series(smap_df, "check_category", "").dropna().astype(str).unique().tolist())
        check_category = st.selectbox("check_category", cat_opts)
    with c4:
        dept_opts = ["(전체)"] + sorted(_safe_series(smap_df, "target_dept", "").dropna().astype(str).unique().tolist())
        target_dept = st.selectbox("target_dept", dept_opts)
    with c5:
        status_opts = ["(전체)"] + sorted(_safe_series(smap_df, "조치상태", "").dropna().astype(str).unique().tolist())
        status = st.selectbox("조치상태", status_opts, key="smap_status")

    k1, k2, k3 = st.columns([2, 2, 2])
    with k1:
        title_kw = st.text_input("title 키워드(포함 검색)", value="")
    with k2:
        issue_opts = ["(전체)"] + sorted(_safe_series(smap_df, "issue_type", "").dropna().astype(str).unique().tolist())
        issue_type = st.selectbox("issue_type", issue_opts)
    with k3:
        q_text = st.text_input("추가 키워드(조치결과/장소 등)", value="", key="smap_qtext")

    fdf = smap_df.copy()
    fdf = _apply_date_filter(fdf, "checked_at", date_from, date_to)

    if check_category != "(전체)" and "check_category" in fdf.columns:
        fdf = fdf[fdf["check_category"].astype(str) == check_category]
    if target_dept != "(전체)" and "target_dept" in fdf.columns:
        fdf = fdf[fdf["target_dept"].astype(str) == target_dept]
    if issue_type != "(전체)" and "issue_type" in fdf.columns:
        fdf = fdf[fdf["issue_type"].astype(str) == issue_type]
    if status != "(전체)" and "조치상태" in fdf.columns:
        fdf = fdf[fdf["조치상태"].astype(str) == status]

    if title_kw.strip() and "title" in fdf.columns:
        fdf = fdf[fdf["title"].astype(str).str.contains(title_kw.strip(), case=False, na=False)]

    if q_text.strip():
        q = q_text.strip()
        search_cols = [c for c in ["title", "place_main", "place_detail", "action_result", "inspector", "issue_type", "action_type"] if c in fdf.columns]
        if search_cols:
            mask = pd.Series(False, index=fdf.index)
            for c in search_cols:
                mask = mask | fdf[c].astype(str).str.contains(q, case=False, na=False)
            fdf = fdf[mask]

    st.caption(f"필터 결과: {len(fdf)}건 / 전체: {len(smap_df)}건")
    _download_filtered_csv_button(fdf, "safety_map_filtered.csv", "⬇️ (safety_map) 필터 결과 CSV 다운로드")

    list_cols = [c for c in ["row_id", "checked_at", "check_category", "check_type", "title", "target_dept", "issue_type", "action_type", "place_main", "action_completed_at", "조치상태"] if c in fdf.columns]
    view = fdf[list_cols].copy()
    if "checked_at" in view.columns:
        view["checked_at"] = pd.to_datetime(view["checked_at"], errors="coerce").dt.date
    if "action_completed_at" in view.columns:
        view["action_completed_at"] = pd.to_datetime(view["action_completed_at"], errors="coerce").dt.date
    st.dataframe(view.reset_index(drop=True), use_container_width=True, height=320)

    if fdf.empty:
        st.info("필터 조건에 해당하는 점검기록이 없습니다.")
        st.stop()

    st.subheader("2) 1건 선택")
    options = fdf["row_id"].astype(str).tolist()
    selected_row_id = st.selectbox("선택 row_id", options=options, index=0, key="smap_row_select")

    sel = fdf[fdf["row_id"].astype(str) == str(selected_row_id)]
    row = sel.iloc[0].to_dict() if not sel.empty else {}

    safety_map_data = map_safety_map_row_to_safety_map_data(row)

    # ✅ 화면(02) 분석 탭은 '필터 결과(fdf) 기반'
    kpis = compute_kpis(fdf, "checked_at", top_dims=["target_dept", "issue_type", "check_category"])
    weekly = weekly_trend(fdf, "checked_at")
    pivot = pivot_heatmap(fdf, row_dim="target_dept", col_dim="issue_type", top_n=15)
    analysis_section = make_analysis_section(kpis, weekly, pivot, pivot_label="부서×지적유형")

    tab_detail, tab_analysis, tab_pdf = st.tabs(["🧾 상세", "📊 분석(차트/피벗)", "📄 PDF 보고서(점검)"])

    with tab_detail:
        _box("✅ 점검 개요", "선택한 1건 점검 데이터를 표준화한 결과입니다.")
        major = {k: safety_map_data.get(k, "") for k in ["row_id", "checked_at", "check_category", "check_type", "title", "target_dept", "issue_type", "action_type", "place_main", "place_detail", "action_status", "action_completed_at", "inspector"]}
        _safe_st_json(major)

        st.markdown("### ✅ 조치 결과(내용)")
        _safe_st_json({"action_result": safety_map_data.get("action_result", "")})

        with st.expander("🧩 extra 전체(디버그)"):
            _safe_st_json(safety_map_data.get("extra", {}) or {})

    with tab_analysis:
        _box("📌 필터 결과 기반 KPI", "KPI/추세/피벗은 필터된 결과(fdf) 기준입니다.")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("총 건수", int(kpis.get("total_cnt", 0)))
        with c2:
            st.metric("월평균(기간 내)", f"{kpis.get('monthly_avg', 0):.2f}")
        with c3:
            st.metric("최근 30일", int(kpis.get("last30_cnt", 0)))
        with c4:
            st.metric("직전 30일", int(kpis.get("prev30_cnt", 0)))

        st.caption(f"증감률(growth): {kpis.get('growth', 0.0):+.2f}  | 기준일(max): {kpis.get('max_dt','-')}")
        if kpis.get("note"):
            st.info(kpis["note"])

        st.markdown("### 📈 월별 / 주간 추세(컬러풀)")
        tmp = fdf.copy()
        tmp["checked_at"] = pd.to_datetime(tmp["checked_at"], errors="coerce")
        t2 = tmp[tmp["checked_at"].notna()].copy()

        if not t2.empty:
            t2["yyyymm"] = t2["checked_at"].dt.to_period("M").astype(str)
            m = t2.groupby("yyyymm").size().reset_index(name="count")
            st.plotly_chart(px.line(m, x="yyyymm", y="count", markers=True, title="월별 점검 추세"), use_container_width=True)

            w = weekly.reset_index()
            w.columns = ["week", "count"]
            st.plotly_chart(px.line(w, x="week", y="count", markers=True, title="주간(Week) 점검 추세"), use_container_width=True)
        else:
            st.info("유효한 점검일자가 없어 추세 차트를 그릴 수 없습니다.")

        st.markdown("### 📊 Top 분류(컬러풀)")
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            if "issue_type" in fdf.columns:
                vc = fdf["issue_type"].astype(str).value_counts().head(15).reset_index()
                vc.columns = ["issue_type", "count"]
                st.plotly_chart(px.bar(vc, x="issue_type", y="count", title="지적유형 Top 15"), use_container_width=True)
        with cc2:
            if "target_dept" in fdf.columns:
                vc = fdf["target_dept"].astype(str).value_counts().head(15).reset_index()
                vc.columns = ["target_dept", "count"]
                st.plotly_chart(px.bar(vc, x="target_dept", y="count", title="대상부서 Top 15"), use_container_width=True)
        with cc3:
            if "조치상태" in fdf.columns:
                vc = fdf["조치상태"].astype(str).value_counts().head(10).reset_index()
                vc.columns = ["조치상태", "count"]
                st.plotly_chart(px.bar(vc, x="조치상태", y="count", title="조치상태 분포"), use_container_width=True)

        st.markdown("### 🔥 피벗(부서 × 지적유형) 히트맵")
        if pivot is not None and not pivot.empty:
            fig_h = px.imshow(
                pivot.values,
                x=[str(c) for c in pivot.columns],
                y=[str(i) for i in pivot.index],
                text_auto=True,
                aspect="auto",
                title="부서 × 지적유형 (건수)",
            )
            st.plotly_chart(fig_h, use_container_width=True)
            st.dataframe(pivot.style.background_gradient(axis=None), use_container_width=True)
        else:
            st.info("피벗을 만들 수 없습니다(데이터/컬럼 부족).")

    with tab_pdf:
        _box("📄 safety_map PDF 보고서 생성", "점검도 LLM 기반 보고서 + (6번 통계 섹션 강제 주입)으로 생성합니다.")
        st.caption("02에서는 RAG(규정검색)를 사용하지 않습니다. reg_context='' (필요시 03에서 수행)")

        # ✅ 6번 통계: 코호트 기반으로 만들지 여부(선택 1건 기준)
        use_cohort_for_report = st.checkbox(
            "보고서 6번(통계/추세/히트맵)을 '선택 1건 유사 코호트' 기준으로 생성",
            value=True,
            key="smap_use_cohort_pdf6",
        )
        cohort_cols = st.columns([1, 1, 1, 2])
        with cohort_cols[0]:
            cohort_months = st.number_input("코호트 최근 N개월(0=전체)", min_value=0, max_value=60, value=12, step=1, key="smap_cohort_months")
        with cohort_cols[1]:
            cohort_use_dept = st.checkbox("부서 포함", value=True, key="smap_cohort_dept")
        with cohort_cols[2]:
            cohort_use_place = st.checkbox("장소 포함(좁아질 수 있음)", value=False, key="smap_cohort_place")
        with cohort_cols[3]:
            st.caption("권장: 지적유형 + 부서(기본). 장소까지 포함하면 0건이 될 수 있습니다.")

        use_similar = st.checkbox("유사점검 분석 요약 포함(safety_map_norm 기반)", value=True)
        sim_cols = st.columns([1, 1, 2])
        with sim_cols[0]:
            sim_months = st.number_input("유사요약 최근 N개월(0=전체)", min_value=0, max_value=60, value=12, step=1, key="smap_months")
        with sim_cols[1]:
            sim_max_cases = st.number_input("대표사례 최대", min_value=3, max_value=50, value=15, step=1, key="smap_max")
        with sim_cols[2]:
            st.caption("유사점검 분석은 ‘반복 패턴 근거’를 추가해 보고서 품질을 올립니다.")

        make_btn = st.button("📄 (safety_map) PDF 보고서 생성", type="primary")

        def _make_similar_cache_key(rid: str, months: Optional[int], max_cases: int, use_sim: bool) -> str:
            return f"sim::smap::{rid}::{use_sim}::{months}::{max_cases}"

        def _get_similar_context_smap(smap_data: Dict[str, Any], rid: str, months: Optional[int], max_cases: int) -> str:
            ck = _make_similar_cache_key(rid, months, max_cases, True)
            if ck in st.session_state:
                return st.session_state.get(ck, "")

            sc = ""
            try:
                norm_path = smap_norm_path
                if norm_path.exists():
                    norm_df = _load_df(norm_path, norm_path.stat().st_mtime)
                    norm_df = ensure_smap_standard_columns(norm_df)
                    sc = build_similar_context_from_smap_df(
                        smap_df=norm_df,
                        smap_data=smap_data,
                        months=months,
                        max_cases=int(max_cases),
                    )
                else:
                    sc = build_similar_context_from_smap_df(
                        smap_df=smap_df,
                        smap_data=smap_data,
                        months=months,
                        max_cases=int(max_cases),
                    )
            except Exception as e:
                logger.exception(e)
                sc = ""

            st.session_state[ck] = sc
            return sc

        if make_btn:
            months_opt = None if int(sim_months) == 0 else int(sim_months)
            similar_context = ""

            if use_similar:
                with st.spinner("유사점검 분석 요약 생성 중..."):
                    similar_context = _get_similar_context_smap(
                        smap_data=safety_map_data,
                        rid=str(selected_row_id),
                        months=months_opt,
                        max_cases=int(sim_max_cases),
                    )

            # ✅ PDF에 들어갈 6번 통계 섹션 결정
            analysis_for_pdf = analysis_section  # 기본: 필터 결과 기반
            if use_cohort_for_report:
                with st.spinner("선택 1건 유사 코호트 통계 산출 중..."):
                    m = None if int(cohort_months) == 0 else int(cohort_months)
                    cohort_df = build_smap_cohort_df(
                        smap_df=smap_df,  # 전체 원본에서 코호트 뽑는 게 안정적
                        selected_row_id=str(selected_row_id),
                        months=m,
                        use_issue=True,
                        use_dept=bool(cohort_use_dept),
                        use_place=bool(cohort_use_place),
                    )

                    if cohort_df is None or cohort_df.empty:
                        st.warning("코호트 조건으로 유사 건이 없어, 보고서 6번은 현재 필터 기반 통계를 사용합니다.")
                    else:
                        k_c = compute_kpis(cohort_df, "checked_at", top_dims=["target_dept", "issue_type", "check_category"])
                        w_c = weekly_trend(cohort_df, "checked_at")
                        p_c = pivot_heatmap(cohort_df, row_dim="target_dept", col_dim="issue_type", top_n=15)
                        analysis_for_pdf = make_analysis_section(k_c, w_c, p_c, pivot_label="(코호트) 부서×지적유형")

            with st.spinner("PDF 생성 중... (Ollama LLM → PDF 렌더링)"):
                try:
                    result = svc.generate_safety_map_pdf(
                        safety_map_data=safety_map_data,
                        reg_context="",
                        similar_context=similar_context if use_similar else "",
                        analysis_section=analysis_for_pdf,  # ✅ 핵심(코호트/필터 선택 반영)
                        output_filename=None,
                    )

                    st.success("PDF 생성 완료!")
                    st.write(f"저장 위치: {result.pdf_path}")

                    pdf_bytes = result.pdf_path.read_bytes()
                    st.download_button(
                        label="⬇️ PDF 다운로드",
                        data=pdf_bytes,
                        file_name=result.pdf_path.name,
                        mime="application/pdf",
                    )

                    with st.expander("🧠 similar_context(디버그)"):
                        st.text(_clip_text(similar_context, 2500) if similar_context else "(없음)")

                    with st.expander("📌 PDF에 넣은 6번 통계 섹션(디버그)"):
                        _safe_st_json(analysis_for_pdf)

                except Exception as e:
                    st.error(f"PDF 생성 실패: {e}")
                    logger.exception(e)

st.divider()
st.subheader("📝 정리")
st.markdown(
    """
- 02 페이지는 **trend / safety_map** 모두 동일한 UX(필터 → 목록 → 상세 → 분석 → PDF)를 제공합니다.
- 화면(02)에서는 Plotly 기반으로 **컬러풀 차트/히트맵**을 표시합니다.
- PDF는 ReportLab 기반 표 렌더링 구조이므로, **피벗은 heat(배경색 단계) 테이블**로 출력합니다.
- 02에서는 **RAG(규정검색)** 를 하지 않고, 규정 근거는 03(규정 QA)에서 담당하는 구조가 가장 안정적입니다.
- ✅ 보고서 6번(통계)은 기본적으로 **선택 1건의 동일/유사 코호트**로 제한하는 옵션을 제공합니다.
"""
)
