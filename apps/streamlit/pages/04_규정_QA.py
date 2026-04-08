# apps/streamlit/pages/04_규정_QA.py
from __future__ import annotations

from apps.streamlit._bootstrap import ensure_project_root_on_syspath

ensure_project_root_on_syspath()

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from core.config import get_settings
from core.logger import get_logger
from shared.io.csv_loader import load_csv

from backend.mappers.trend_mapper import map_trend_row_to_incident_data
from backend.services.qa_service import QAService

# ✅ [신규 추가] Graph-RAG 서비스 임포트
from backend.services.graph_qa_service import GraphQAService

# ✅ QA 결과 PDF 렌더러
from report.renderers.pdf_qa_reportlab import render_qa_pdf

# ✅ 공통 레이아웃/스타일
from apps.streamlit.ui.theme.layout import apply_layout, page_container, end_page, section, hero

from backend.mappers.safety_map_mapper import (
    map_safety_map_row_to_safety_map_data,
    build_safety_map_record_text,
)

logger = get_logger(__name__)
settings = get_settings()


apply_layout()
page_container()

st.set_page_config(page_title="규정 QA & 사고 예측", layout="wide")

apply_layout()
page_container()

# (기존의 hero(...) 부분을 지우고 아래 코드를 넣으세요)
st.markdown(
    """
    <div class="dtro-hero">
      <div class="dtro-hero-title">📚 04. 규정 QA & 사고 예측</div>
      <div class="dtro-hero-sub">법령/지침 위반 가능성을 검토하는 RAG와, 과거 데이터의 인과관계를 추론하는 Graph-RAG를 제공합니다.</div>
    </div>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# 공통 유틸
# =========================================================
def _to_dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def _safe_series(df: pd.DataFrame, col: str, default=None) -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)


def _pick_first_existing(*paths: Path) -> Optional[Path]:
    for p in paths:
        if p.exists():
            return p
    return None


@st.cache_data(show_spinner=False)
def load_df(path: Path, mtime: float) -> pd.DataFrame:
    res = load_csv(path)
    df = res.df.copy()
    df.columns = [str(c).replace("\n", "").replace("\r", "").strip() for c in df.columns]
    df["__row_id__"] = range(len(df))
    if "row_id" not in df.columns:
        df["row_id"] = df["__row_id__"].astype(str)
    else:
        df["row_id"] = df["row_id"].astype(str)
    return df


def _ensure_trend_standard_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "occurred_at" not in out.columns:
        out["occurred_at"] = out.get("발생일자", out.get("일자", pd.NaT))
    out["occurred_at"] = _to_dt(out["occurred_at"])
    if "occurred_time" not in out.columns:
        out["occurred_time"] = out.get("발생시간", out.get("시간", ""))
    if "line" not in out.columns and "호선" in out.columns: out["line"] = out["호선"]
    if "station" not in out.columns and "역명" in out.columns: out["station"] = out["역명"]
    if "incident_type" not in out.columns and "사고유형" in out.columns: out["incident_type"] = out["사고유형"]
    if "cause" not in out.columns and "사고원인" in out.columns: out["cause"] = out["사고원인"]
    if "cause_detail" not in out.columns and "상세원인" in out.columns: out["cause_detail"] = out["상세원인"]
    if "summary" not in out.columns and "사고개황" in out.columns: out["summary"] = out["사고개황"]
    if "actions_taken" not in out.columns and "조치" in out.columns: out["actions_taken"] = out["조치"]
    if "misc" not in out.columns and "기타" in out.columns: out["misc"] = out["기타"]
    if "report_type" not in out.columns and "보고구분" in out.columns: out["report_type"] = out["보고구분"]
    if "cctv" not in out.columns and "CCTV유무" in out.columns: out["cctv"] = out["CCTV유무"]
    if "조치상태" not in out.columns: out["조치상태"] = "미상"
    return out


def _ensure_smap_standard_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "checked_at" not in out.columns: out["checked_at"] = out.get("지도점검일자", pd.NaT)
    out["checked_at"] = _to_dt(out["checked_at"])
    if "action_completed_at" not in out.columns: out["action_completed_at"] = out.get("조치완료일자", pd.NaT)
    out["action_completed_at"] = _to_dt(out["action_completed_at"])

    def _map(dst: str, *srcs: str, default=""):
        if dst in out.columns: return
        for s in srcs:
            if s in out.columns:
                out[dst] = out[s]
                return
        out[dst] = default

    _map("check_category", "지도점검구분")
    _map("check_type", "점검형태")
    _map("title", "제목")
    _map("target_dept", "대상부서")
    _map("action_type", "조치구분")
    _map("issue_type", "지적유형")
    _map("place_main", "장소1")
    _map("place_detail", "장소2")
    _map("action_result", "조치결과내용")
    _map("inspector", "점검자")

    if "조치상태" not in out.columns:
        out["조치상태"] = out["action_completed_at"].apply(lambda x: "완료" if pd.notna(x) else "미조치")
    return out


def _build_safety_map_record_text(row: Dict[str, Any]) -> str:
    keys = [
        ("row_id", "row_id"), ("checked_at", "점검일"), ("check_category", "점검구분"),
        ("check_type", "점검형태"), ("title", "제목"), ("target_dept", "대상부서"),
        ("action_type", "조치구분"), ("issue_type", "지적유형"), ("place_main", "장소1"),
        ("place_detail", "장소2"), ("action_completed_at", "조치완료일"),
        ("inspector", "점검자"), ("action_result", "조치결과내용"),
    ]
    lines = ["[점검기록 1건 요약]"]
    for k, label in keys:
        if k in row:
            v = row.get(k, "")
            if isinstance(v, pd.Timestamp): v = v.date().isoformat()
            lines.append(f"- {label}: {v}")
    return "\n".join(lines).strip()


@st.cache_data(show_spinner=False)
def _build_past_similar_summary_cached(df: pd.DataFrame, place_main: str, issue_type: str, top_n: int = 12) -> str:
    if df.empty: return ""
    place_main = (place_main or "").strip()
    issue_type = (issue_type or "").strip()
    if not place_main and not issue_type: return ""

    tmp = df.copy()
    if "checked_at" in tmp.columns: tmp["checked_at"] = _to_dt(tmp["checked_at"])

    cond = pd.Series([True] * len(tmp), index=tmp.index)
    if place_main and "place_main" in tmp.columns: cond &= (tmp["place_main"].astype(str).str.strip() == place_main)
    if issue_type and "issue_type" in tmp.columns: cond &= (tmp["issue_type"].astype(str).str.strip() == issue_type)

    sim = tmp[cond].copy()
    if sim.empty: return ""
    if "checked_at" in sim.columns: sim = sim.sort_values("checked_at", ascending=False)

    show_cols = [c for c in ["checked_at", "target_dept", "action_type", "action_completed_at", "action_result"] if
                 c in sim.columns]
    sim = sim.head(top_n)

    lines = [f"[과거 유사 기록 요약] (유사조건: 장소1={place_main}, 지적유형={issue_type})",
             f"- 유사 건수: {len(sim)}건(표시 {min(len(sim), top_n)}건)"]
    for i, r in enumerate(sim[show_cols].to_dict(orient="records"), 1):
        items = []
        for k in show_cols:
            v = r.get(k, "")
            if isinstance(v, pd.Timestamp): v = v.date().isoformat()
            items.append(f"{k}={v}")
        lines.append("  [" + str(i) + "] " + " | ".join(items))
    return "\n".join(lines).strip()


# =========================================================
# QAService 및 GraphQAService 캐시
# =========================================================
@st.cache_resource
def get_qa_service() -> QAService:
    return QAService()


# ✅ [신규 추가] Graph-RAG 서비스 캐싱 (매번 불러오지 않도록 최적화)
@st.cache_resource
def get_graph_qa_service() -> GraphQAService:
    return GraphQAService()


qa = get_qa_service()
graph_qa = get_graph_qa_service()


# =========================================================
# Session State 초기화
# =========================================================
def _init_state():
    defaults = {
        "qa_dataset": "trend",
        "qa_result": None,
        "qa_question": "",
        "qa_context_obj": None,
        "qa_pdf_path": None,
        "qa_question_autofill": True,
        "qa_question_last_sig": "",
        "qa_question_text": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _reset_results():
    st.session_state["qa_result"] = None
    st.session_state["qa_pdf_path"] = None


def _toggle_autofill(label: str):
    st.session_state["qa_question_autofill"] = st.toggle(
        label,
        value=bool(st.session_state.get("qa_question_autofill", True)),
        help="OFF로 두면 사용자가 작성한 질의문을 유지합니다. ON이면 선택이 바뀔 때 기본문구로 재작성됩니다.",
    )


def _apply_autofill(sig: str, default_question: str):
    if st.session_state.get("qa_question_autofill", True) and st.session_state.get("qa_question_last_sig") != sig:
        st.session_state["qa_question_text"] = default_question
        st.session_state["qa_question_last_sig"] = sig


_init_state()

# =========================================================
# ✅ [신규 추가] 화면 모드 선택기 (Vector-RAG vs Graph-RAG)
# =========================================================
app_mode = st.radio(
    "🔍 기능 선택",
    ["📚 문서 기반 규정 검토 (Vector RAG)", "🌐 인과관계 사고 예측 (Graph RAG)"],
    horizontal=True
)
st.divider()

# =========================================================
# [모드 1] 기존 문서 기반 규정 검토 (Vector-RAG)
# =========================================================
if "Vector" in app_mode:
    PROJECT_ROOT = Path(__file__).resolve().parents[3]
    DATA_DIR = PROJECT_ROOT / "data"

    trend_norm_path = DATA_DIR / "norm" / "trend" / "trend_norm.csv"
    trend_csv_path = DATA_DIR / "csv" / "trend.csv"

    smap_norm_path = DATA_DIR / "norm" / "safety_map" / "safety_map_norm.csv"
    smap_csv_path = DATA_DIR / "csv" / "safety_map.csv"

    chosen_trend = _pick_first_existing(trend_norm_path, trend_csv_path)
    chosen_smap = _pick_first_existing(smap_norm_path, smap_csv_path)

    trend_df = load_df(chosen_trend, chosen_trend.stat().st_mtime) if chosen_trend else pd.DataFrame()
    smap_df = load_df(chosen_smap, chosen_smap.stat().st_mtime) if chosen_smap else pd.DataFrame()

    if not trend_df.empty: trend_df = _ensure_trend_standard_columns(trend_df)
    if not smap_df.empty: smap_df = _ensure_smap_standard_columns(smap_df)

    with st.expander("📌 데이터 경로(디버그)"):
        st.write(f"- trend: `{chosen_trend}`" if chosen_trend else "- trend: (없음)")
        st.write(f"- safety_map: `{chosen_smap}`" if chosen_smap else "- safety_map: (없음)")

    if trend_df.empty and smap_df.empty:
        st.error("trend / safety_map 데이터가 없습니다. data/norm 또는 data/csv 폴더를 확인하세요.")
        end_page()
        st.stop()

    section("1️⃣ 질의 기준 선택 (데이터셋)")

    dataset_options: List[tuple[str, str]] = []
    if not trend_df.empty: dataset_options.append(("trend", "사고(trend) 기반 규정 질의"))
    if not smap_df.empty: dataset_options.append(("safety_map", "점검(safety_map) 기반 규정 질의"))

    default_dataset = st.session_state.get("qa_dataset", "trend")
    valid_keys = [k for k, _ in dataset_options]
    if default_dataset not in valid_keys: default_dataset = valid_keys[0] if valid_keys else "trend"

    label_map = {k: v for k, v in dataset_options}
    selected = st.radio(
        "질의 기준",
        options=valid_keys,
        format_func=lambda k: label_map.get(k, k),
        horizontal=True,
        index=valid_keys.index(default_dataset) if default_dataset in valid_keys else 0,
        key="qa_dataset",
    )
    st.divider()

    question = ""

    if selected == "trend":
        section("2️⃣ 사고 선택")
        show_cols = [c for c in ["row_id", "occurred_at", "occurred_time", "line", "station", "incident_type", "조치상태",
                                 "report_type"] if c in trend_df.columns]
        show_df = trend_df[show_cols].copy()
        if "occurred_at" in show_df.columns: show_df["occurred_at"] = show_df["occurred_at"].dt.date
        st.dataframe(show_df, use_container_width=True, height=280)


        def _trend_label(r: pd.Series) -> str:
            rid = str(r.get("row_id", ""))
            d = r.get("occurred_at", pd.NaT)
            d_str = d.date().isoformat() if pd.notna(d) else "-"
            return f"[{rid}] {d_str} | {str(r.get('line', '-'))} | {str(r.get('station', '-'))} | {str(r.get('incident_type', '-'))}"


        rid_list = trend_df["row_id"].astype(str).tolist()
        label_map_rid = {rid: _trend_label(trend_df.loc[trend_df["row_id"].astype(str) == rid].iloc[0]) for rid in
                         rid_list}
        row_id = st.selectbox("검토할 사고 선택 (row_id)", options=rid_list, index=0, key="qa_trend_row",
                              format_func=lambda x: label_map_rid.get(str(x), str(x)))

        row = trend_df[trend_df["row_id"].astype(str) == str(row_id)].iloc[0].to_dict()
        incident_data = map_trend_row_to_incident_data(row)
        st.session_state["qa_context_obj"] = incident_data

        section("사고 요약")
        c1, c2, c3 = st.columns(3)
        c1.metric("호선", incident_data.get("line", ""))
        c2.metric("역명", incident_data.get("station", ""))
        c3.metric("사고유형", incident_data.get("accident_type", ""))

        with st.expander("📄 사고 상세 요약"):
            st.json({"사고개황": incident_data.get("summary", ""), "초동대처": incident_data.get("timeline", ""),
                     "조치": incident_data.get("actions_taken", ""), "조치상태": incident_data.get("current_status", "")})

        section("3️⃣ 규정 질의")
        _toggle_autofill("질의문 자동 갱신(사고 선택 변경 시 기본문구로 재작성)")
        default_question = (
            f"다음 사고가 관련 법령, 사내 규정, 안전 지침을 위반했을 가능성이 있는지 검토해 주세요.\n\n[사고 정보]\n- 호선/역명: {incident_data.get('line', '')} {incident_data.get('station', '')}\n- 사고유형: {incident_data.get('accident_type', '')}\n- 사고개황: {incident_data.get('summary', '')}\n- 초동대처: {incident_data.get('timeline', '')}\n")
        sig = f"trend::{row_id}"
        _apply_autofill(sig, default_question)
        question = st.text_area("질의 내용 (수정 가능)", height=180, key="qa_question_text")

    elif selected == "safety_map":
        section("2️⃣ 점검기록 선택")
        if smap_df.empty:
            st.error("safety_map 데이터가 비어있거나 읽을 수 없습니다.")
            end_page()
            st.stop()

        f1, f2, f3 = st.columns(3)
        with f1:
            dept_opt = ["전체"] + sorted(_safe_series(smap_df, "target_dept", "").dropna().astype(str).unique().tolist())
            dept = st.selectbox("대상부서", dept_opt, key="qa_smap_dept")
        with f2:
            issue_opt = ["전체"] + sorted(_safe_series(smap_df, "issue_type", "").dropna().astype(str).unique().tolist())
            issue = st.selectbox("지적유형", issue_opt, key="qa_smap_issue")
        with f3:
            action_opt = ["전체"] + sorted(
                _safe_series(smap_df, "action_type", "").dropna().astype(str).unique().tolist())
            action = st.selectbox("조치구분", action_opt, key="qa_smap_action")

        fsdf = smap_df.copy()
        if dept != "전체" and "target_dept" in fsdf.columns: fsdf = fsdf[fsdf["target_dept"].astype(str) == dept]
        if issue != "전체" and "issue_type" in fsdf.columns: fsdf = fsdf[fsdf["issue_type"].astype(str) == issue]
        if action != "전체" and "action_type" in fsdf.columns: fsdf = fsdf[fsdf["action_type"].astype(str) == action]

        if fsdf.empty:
            st.info("현재 필터 조건에 해당하는 점검기록이 없습니다.")
            end_page()
            st.stop()

        show_df = fsdf.sort_values("checked_at", ascending=False) if "checked_at" in fsdf.columns else fsdf
        list_cols = [c for c in
                     ["row_id", "checked_at", "check_category", "check_type", "title", "target_dept", "issue_type",
                      "action_type", "place_main", "action_completed_at", "조치상태"] if c in show_df.columns]
        view = show_df[list_cols].copy()
        if "checked_at" in view.columns: view["checked_at"] = view["checked_at"].dt.date
        if "action_completed_at" in view.columns: view["action_completed_at"] = view["action_completed_at"].dt.date
        st.dataframe(view, use_container_width=True, height=280)


        def _smap_label(r: pd.Series) -> str:
            rid = str(r.get("row_id", ""))
            d = r.get("checked_at", pd.NaT)
            d_str = d.date().isoformat() if pd.notna(d) else "-"
            return f"[{rid}] {d_str} | {str(r.get('target_dept', '-'))} | {str(r.get('issue_type', '-'))} | {str(r.get('place_main', '-'))}"


        rid_list = show_df["row_id"].astype(str).tolist()
        label_map_rid = {rid: _smap_label(show_df.loc[show_df["row_id"].astype(str) == rid].iloc[0]) for rid in
                         rid_list}
        row_id = st.selectbox("검토할 점검기록 선택 (row_id)", options=rid_list, index=0, key="qa_smap_row",
                              format_func=lambda x: label_map_rid.get(str(x), str(x)))

        row = show_df[show_df["row_id"].astype(str) == str(row_id)].iloc[0].to_dict()
        smap_data = map_safety_map_row_to_safety_map_data(row)
        st.session_state["qa_context_obj"] = smap_data
        smap_record_text = build_safety_map_record_text(smap_data)

        section("점검기록 요약")
        c1, c2, c3 = st.columns(3)
        c1.metric("대상부서", str(row.get("target_dept", "")))
        c2.metric("지적유형", str(row.get("issue_type", "")))
        c3.metric("장소1", str(row.get("place_main", "")))

        with st.expander("📄 점검기록 상세"):
            st.text(smap_record_text)

        place_main = str(row.get("place_main", "")).strip()
        issue_type = str(row.get("issue_type", "")).strip()
        past_similar_summary = _build_past_similar_summary_cached(smap_df, place_main, issue_type, top_n=12)

        with st.expander("🕘 과거 유사 기록 요약(자동)"):
            st.text(past_similar_summary or "(유사 기록이 없거나 요약할 수 없습니다.)")

        section("3️⃣ 규정 질의")
        _toggle_autofill("질의문 자동 갱신(점검기록 선택 변경 시 기본문구로 재작성)")
        default_question = (
            f"다음 점검/지도 기록이 관련 법령, 사내 규정, 안전 지침을 위반했을 가능성이 있는지 검토해 주세요.\n또한 과거 유사 기록 대비 추세(증가/감소) 및 예방 활동을 제안해 주세요.\n\n[점검 핵심]\n- 점검일: {row.get('checked_at', '')}\n- 대상부서: {row.get('target_dept', '')}\n- 지적유형: {row.get('issue_type', '')}\n- 장소: {row.get('place_main', '')} {row.get('place_detail', '')}\n- 조치구분: {row.get('action_type', '')}\n- 조치결과: {str(row.get('action_result', ''))[:200]}\n")
        sig = f"safety_map::{row_id}"
        _apply_autofill(sig, default_question)
        question = st.text_area("질의 내용 (수정 가능)", height=180, key="qa_question_text")

    section("⚙️ 검색/응답 옵션(속도/정확도 조절)")
    o1, o2, o3, o4 = st.columns(4)
    with o1:
        top_k = st.slider("top_k", min_value=1, max_value=10, value=int(settings.rag.top_k), step=1)
    with o2:
        per_chunk_chars = st.slider("청크 자르기(문자)", min_value=300, max_value=2000, value=1200, step=100)
    with o3:
        max_context_chars = st.slider("전체 컨텍스트(문자)", min_value=1500, max_value=16000, value=6000, step=500)
    with o4:
        num_predict_default = 850 if selected == "safety_map" else 650
        num_predict = st.slider("LLM 출력 길이", min_value=200, max_value=1600, value=num_predict_default, step=50)

    attach_records_to_question = False
    attach_records_max_chars = 1200
    search_tone_filter = False

    if selected == "safety_map":
        st.caption("✅ 권장: 검색(임베딩)은 '질문' 중심으로, 점검기록/유사요약은 프롬프트에만 포함(분리).")
        copt1, copt2, copt3 = st.columns([1.4, 1.4, 1.2])
        with copt1: attach_records_to_question = st.toggle("검색강화(비권장)", value=False)
        with copt2: attach_records_max_chars = st.slider("요약 길이", 300, 2500, 1200, 100)
        with copt3: search_tone_filter = st.toggle("검색 보정(옵션)", value=False)

    btn_cols = st.columns([1, 1, 2])
    with btn_cols[0]:
        run_btn = st.button("🔎 규정 QA 실행", type="primary")
    with btn_cols[1]:
        clear_btn = st.button("🧹 결과 초기화", type="secondary")

    if clear_btn:
        _reset_results()
        st.rerun()

    if run_btn:
        with st.spinner("규정 검색 및 AI 분석 중..."):
            try:
                if selected == "trend":
                    incident_data = st.session_state.get("qa_context_obj") or {}
                    result = qa.ask(
                        question=question, dataset="trend", top_k=int(top_k),
                        incident_data=incident_data, per_chunk_chars=int(per_chunk_chars),
                        max_context_chars=int(max_context_chars), num_predict=int(num_predict),
                    )
                else:
                    row = st.session_state.get("qa_context_obj") or {}
                    safety_record_text = build_safety_map_record_text(row)
                    place_main = str(row.get("place_main", "")).strip()
                    issue_type = str(row.get("issue_type", "")).strip()
                    past_similar_summary = _build_past_similar_summary_cached(smap_df, place_main, issue_type, top_n=12)
                    result = qa.ask(
                        question=question, dataset="safety_map", template_name="qa_safety_map_v2.md",
                        top_k=int(top_k), safety_map_records=safety_record_text,
                        past_similar_summary=past_similar_summary, per_chunk_chars=int(per_chunk_chars),
                        max_context_chars=int(max_context_chars), num_predict=int(num_predict),
                        attach_records_to_question=bool(attach_records_to_question),
                        attach_records_max_chars=int(attach_records_max_chars),
                        search_tone_filter=bool(search_tone_filter),
                    )
                st.session_state["qa_result"] = result
                st.session_state["qa_question"] = question
                st.session_state["qa_pdf_path"] = None
            except Exception as e:
                st.error(f"규정 QA 실패: {e}")
                logger.exception(e)

    result = st.session_state.get("qa_result")
    if result is not None:
        section("✅ AI 분석 결과")
        st.markdown("### 판단 요약")
        st.write(result.answer)
        st.markdown("### 📑 근거(Top-K)")
        if not result.citations:
            st.info("근거를 찾지 못했습니다(근거 부족).")
        else:
            for i, c in enumerate(result.citations, 1):
                st.markdown(
                    f"**[{i}] {c.doc_title}** \n- 구분: {c.category}  \n- 페이지: p.{c.page_no}  \n- 점수: {c.score:.3f}  \n- 경로: `{c.source_path}`")
        with st.expander("🧩 사용된 컨텍스트(디버그/근거 텍스트)"):
            st.text(result.used_context or "")
        st.info("※ 본 분석은 RAG 기반 AI 보조 판단이며, 법적·행정적 최종 판단은 담당자의 검토가 필요합니다.")

# =========================================================
# [모드 2] 신규 인과관계 사고 예측 (Graph-RAG)
# =========================================================
elif "Graph" in app_mode:
    section("🌐 지능형 안전 인과관계 분석 (Graph-RAG)")
    st.info("💡 과거 사고 데이터(trend)와 점검 데이터(safety_map)의 **숨겨진 연결고리**를 분석하여 사고의 원인을 찾고 예방 대책을 브리핑합니다.")

    col1, col2 = st.columns([1, 2])

    with col1:
        # 검색 시작점이 될 단어 (자연어 친화적으로 이름과 설명 변경!)
        graph_keyword = st.text_input(
            "🔍 분석할 사고/상황 설명 (자연어 입력 가능)",
            value="에스컬레이터에서 넘어지거나 다친 사례",
            help="단답형 키워드는 물론, 일상적인 문장으로 상황을 길게 설명해도 AI가 찰떡같이 핵심을 뽑아냅니다. (예: 겨울철 문양기지에서 용접기 쓰다가 다친 사례)"
        )

    with col2:
        # AI에게 지시할 내용
        graph_question = st.text_area(
            "🗣️ AI 분석가에게 요청할 질문",
            value="이 사고의 주요 원인과 예방 대책을 요약해줘.",
            height=68
        )

    # 실행 버튼
    run_graph_btn = st.button("🔎 인과관계 분석 실행", type="primary")

    if run_graph_btn:
        if not graph_keyword.strip():
            st.warning("핵심 키워드를 입력해 주세요.")
        else:
            with st.spinner("거미줄처럼 엮인 지식 지도를 탐색하고 브리핑을 작성 중입니다... (약 10초 소요)"):
                try:
                    # ✅ 여기서 아까 만든 GraphQAService 호출!
                    answer = graph_qa.ask(question=graph_question, keyword=graph_keyword)

                    st.success("✅ 지식 지도 기반 분석 완료!")

                    st.markdown("### 🤖 AI 안전 분석가 브리핑")
                    # 보기 좋게 테두리 박스 안에 답변 출력
                    st.info(answer)

                    # 관리자가 내부 로봇어(Raw Data)를 확인할 수 있도록 토글 제공
                    with st.expander("🧩 탐색된 인과관계 원본 데이터 (Raw Graph Data)"):
                        raw_context = graph_qa.graph_store.search_context(graph_keyword)
                        st.text(raw_context)

                except Exception as e:
                    st.error(f"분석 중 오류가 발생했습니다: {e}")
                    logger.exception(e)

end_page()