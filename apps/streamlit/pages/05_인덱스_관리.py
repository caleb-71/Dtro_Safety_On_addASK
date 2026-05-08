# apps/streamlit/pages/05_인덱스_관리.py
from __future__ import annotations

from apps.streamlit._bootstrap import ensure_project_root_on_syspath

ensure_project_root_on_syspath()

import time
from pathlib import Path

import streamlit as st
from core.config import get_settings
from core.logger import get_logger

# [신규 추가] 여기서 hero, apply_layout, page_container를 모두 가져옵니다!
from apps.streamlit.ui.theme.layout import apply_layout, page_container, hero, end_page
from apps.streamlit.ui.theme.layout import end_page
from core.config import get_settings
from core.logger import get_logger

# PDF 인덱스(규정)
from rag.vectorstores.faiss_store import FaissVectorStore
from dataops.pipelines.build_index import build_index

# 데이터(norm/meta)
from dataops.pipelines.build_norm_and_meta import BuildConfig, run_build_norm_and_meta

# 데이터 인덱스(안전지도/추세)
from rag.vectorstores.faiss_store import DataFaissVectorStore
from dataops.pipelines.build_data_index import build_data_index

# ✅ [신규 추가] 지식 지도(Graph) 생성 파이프라인
from rag.graph.build_graph import build_knowledge_graph

# ✅ (공식 경로 단일 진실)
from rag.paths import INDEX_DIR, DATA_INDEX_DIR

logger = get_logger(__name__)
settings = get_settings()


# -----------------------------
# helpers
# -----------------------------
def file_info(p: Path) -> str:
    """파일 상태를 짧게 요약(운영자용)"""
    if not p.exists():
        return "없음"
    size_kb = p.stat().st_size / 1024
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
    return f"{size_kb:.1f} KB | {mtime}"


def count_jsonl_lines(p: Path, max_lines: int = 2_000_000) -> int:
    if not p.exists():
        return 0
    n = 0
    with p.open("r", encoding="utf-8") as f:
        for _ in f:
            n += 1
            if n >= max_lines:
                break
    return n


def scan_candidate_files(raw_dir: Path, csv_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for base in [raw_dir, csv_dir]:
        if base.exists():
            candidates += list(base.glob("*.csv"))
            candidates += list(base.glob("*.xlsx"))
            candidates += list(base.glob("*.xls"))
    return sorted(set(candidates))


def scan_meta_jsonls(meta_root: Path) -> list[Path]:
    if not meta_root.exists():
        return []
    return sorted([p for p in meta_root.rglob("meta.jsonl") if p.is_file()])


# -----------------------------
# Streamlit Page
# -----------------------------
# --- 화면 세팅 영역 ---
st.set_page_config(page_title="인덱스 관리", layout="wide")

# [신규 추가] 화면 전체에 예쁜 옷(CSS)을 입힙니다!
apply_layout()
page_container()

# [수정] 다시 깔끔한 hero 함수를 사용합니다.
hero(
    "🧰 05. 통합 인덱스 관리 (관리자)",
    "규정(PDF) 벡터 + 데이터(CSV) 벡터 + 지식 지도(Graph)를 함께 관리합니다."
)

# -----------------------------
# 공통 경로 (프로젝트 내부)
# -----------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
NORM_DIR = DATA_DIR / "norm"
META_DIR = DATA_DIR / "meta"
CSV_DIR = DATA_DIR / "csv"
DOCS_DIR = settings.paths.docs_dir

# ✅ [신규 추가] 그래프 인덱스 경로
GRAPH_DIR = DATA_DIR / "index" / "graph"
GRAPH_FILE = GRAPH_DIR / "safety_knowledge.json"

for p in [RAW_DIR, NORM_DIR, META_DIR, GRAPH_DIR]:
    p.mkdir(parents=True, exist_ok=True)

# -----------------------------
# 상단: 경로 단일진실 안내(중요)
# -----------------------------
with st.expander("📌 공식 인덱스 경로(단일 진실) 확인", expanded=True):
    st.write("- 아래 경로가 **항상 기준**입니다.")
    st.code(f"PDF 벡터: {INDEX_DIR}\n데이터 벡터: {DATA_INDEX_DIR}\n지식 지도(Graph): {GRAPH_DIR}")

# =========================================================
# 1) 데이터(norm/meta) 생성 (기존 로직 동일, 코드 생략 방지)
# =========================================================
st.subheader("🧱 1단계: 데이터 전처리 (CSV ➔ Norm/Meta)")
st.caption("안전지도 및 사고추세 데이터를 표준 형태로 변환합니다.")

# ... (기존의 cfg_safety_map 및 cfg_trend 설정 로직 100% 유지) ...
cfg_safety_map = BuildConfig(
    source="DTRO", dataset="safety_map", dataset_prefix="SMAP",
    field_map={"지도점검일자": "checked_at", "지도점검구분": "check_category", "점검형태": "check_type", "제목": "title",
               "대상부서": "target_dept", "조치구분": "action_type", "지적유형": "issue_type", "장소1": "place_main",
               "장소2": "place_detail", "조치완료일자": "action_completed_at", "점검자": "inspector", "조치결과내용": "action_result"},
    stable_fields=["checked_at", "check_category", "check_type", "issue_type", "place_main", "place_detail"],
    meta_text_fields=["title", "issue_type", "place_main", "place_detail", "check_type", "action_type", "action_result",
                      "target_dept"],
    meta_metadata_fields=["checked_at", "check_category", "check_type", "issue_type", "place_main", "place_detail",
                          "target_dept", "action_completed_at", "raw_ref"],
)

cfg_trend = BuildConfig(
    source="DTRO", dataset="trend", dataset_prefix="TRND",
    field_map={"발생일자": "occurred_at", "발생시간": "occurred_time", "호선": "line", "역명": "station", "발생장소": "place_main",
               "장소세부1": "place_detail_1", "장소세부2": "place_detail_2", "사고유형": "incident_type", "CCTV유무": "cctv",
               "사고원인": "cause", "상세원인": "cause_detail", "보고구분": "report_type", "사고개황": "summary", "조치": "actions_taken",
               "기타": "misc"},
    stable_fields=["occurred_at", "occurred_time", "line", "station", "place_main", "incident_type"],
    meta_text_fields=["summary", "incident_type", "place_main", "place_detail_1", "place_detail_2", "cause",
                      "cause_detail", "actions_taken", "misc"],
    meta_metadata_fields=["occurred_at", "occurred_time", "line", "station", "place_main", "incident_type", "cctv",
                          "report_type", "raw_ref"],
)

c1, c2 = st.columns(2)
with c1:
    if st.button("▶ safety_map 전처리", type="secondary"):
        raw_path = (PROJECT_ROOT / "data/csv/safety_map.csv").resolve()
        if raw_path.exists():
            with st.spinner("safety_map 처리 중..."):
                run_build_norm_and_meta(raw_path=raw_path, norm_out=NORM_DIR / "safety_map" / "safety_map_norm.csv",
                                        meta_out=META_DIR / "safety_map" / "meta.jsonl", cfg=cfg_safety_map,
                                        required_fields=["row_id"])
            st.success("safety_map 전처리 완료!")
        else:
            st.error("파일이 없습니다.")
with c2:
    if st.button("▶ trend 전처리", type="secondary"):
        raw_path = (PROJECT_ROOT / "data/csv/trend.csv").resolve()
        if raw_path.exists():
            with st.spinner("trend 처리 중..."):
                run_build_norm_and_meta(raw_path=raw_path, norm_out=NORM_DIR / "trend" / "trend_norm.csv",
                                        meta_out=META_DIR / "trend" / "meta.jsonl", cfg=cfg_trend,
                                        required_fields=["row_id"])
            st.success("trend 전처리 완료!")
        else:
            st.error("파일이 없습니다.")

st.divider()

# =========================================================
# 2) 데이터 통합 인덱싱 (Vector RAG + Graph RAG) [핵심 변경]
# =========================================================
st.subheader("📦 2단계: 데이터 통합 인덱싱 (Vector + Graph)")
st.caption("전처리된 데이터를 바탕으로 검색용 벡터 인덱스와 추론용 지식 지도를 동시에 생성합니다.")

data_store = DataFaissVectorStore(index_dir=DATA_INDEX_DIR)

d1, d2, d3, d4 = st.columns(4)
with d1: st.metric("Vector FAISS", "✅" if data_store.faiss_path.exists() else "❌")
with d2: st.metric("Vector Meta", "✅" if data_store.meta_path.exists() else "❌")
# ✅ [신규 추가] 지식 지도 파일 상태 표시
with d3: st.metric("Graph JSON (지식지도)", "✅" if GRAPH_FILE.exists() else "❌")
with d4:
    if data_store.faiss_path.exists():
        try:
            data_store.load()
            st.metric("Vector 개수", str(data_store.ntotal))
        except:
            st.metric("Vector 개수", "로드 실패")
    else:
        st.metric("Vector 개수", "N/A")

st.markdown("#### ⚙️ 통합 인덱싱 실행 옵션")
da, db, dc = st.columns([1, 1, 2])

with da:
    data_force = st.checkbox("강제 재인덱싱(--force)", value=False)
with db:
    data_embed_batch = st.number_input("Vector 임베딩 배치", min_value=1, max_value=128, value=16, step=1)
with dc:
    # ✅ [신규 추가] Graph 추출 시 LLM 부하를 막기 위한 제한 옵션
    graph_limit = st.number_input(
        "Graph 추출 제한 (파일당 건수)",
        min_value=10, max_value=2000, value=30, step=10,
        help="지식 지도는 LLM이 직접 읽고 추출하므로 숫자가 클수록 오래 걸립니다. (30건 = 약 3분 소요)"
    )

# ✅ [신규 추가] 버튼 이름 변경 (통합)
run_data_btn = st.button("🚀 Vector & Graph 통합 인덱싱 실행", type="primary")

if run_data_btn:
    # 1. 벡터 인덱싱 시작
    with st.spinner("1/2: 데이터 벡터 인덱싱 진행 중... (FAISS)"):
        try:
            t0 = time.time()
            build_data_index(force=bool(data_force), embed_batch_size=int(data_embed_batch))
            dt1 = time.time() - t0
            st.success(f"✅ 벡터 인덱싱 완료! (소요: {dt1:.1f}초)")
        except Exception as e:
            st.error(f"벡터 인덱싱 실패: {e}")
            logger.exception(e)
            st.stop()

    # 2. 지식 지도(Graph) 인덱싱 시작
    with st.spinner(f"2/2: 지식 지도(Graph) 구축 중... (최대 {graph_limit * 2}건 처리, 시간이 다소 소요됩니다)"):
        try:
            t1 = time.time()
            # 우리가 만든 Graph-RAG 엔진 구동
            build_knowledge_graph(limit=int(graph_limit))
            dt2 = time.time() - t1
            st.success(f"✅ 지식 지도(Graph) 구축 완료! (소요: {dt2:.1f}초)")

            st.balloons()
            st.info(f"🎉 통합 인덱싱 완료! (총 소요시간: {dt1 + dt2:.1f}초)\n이제 Q&A 화면에서 최신 데이터로 규정 검색 및 인과관계 예측이 가능합니다.")

        except Exception as e:
            st.error(f"지식 지도 구축 실패: {e}")
            logger.exception(e)

st.divider()

# =========================================================
# 3) PDF 문서 상태 + PDF 인덱스 상태/실행 (INDEX_DIR) (기존 동일)
# =========================================================
st.subheader("📁 문서(PDF) 저장소 상태 & 규정 인덱싱")
st.caption("PDF 원본(법령, 규정, 지침) 변경 시 실행하세요.")

pdfs = sorted([p for p in DOCS_DIR.rglob("*.pdf") if p.is_file()])
pcol1, pcol2 = st.columns([1, 3])
with pcol1: st.metric("PDF 문서 개수", f"{len(pdfs)}개")

colA, colB, colC = st.columns([1, 1, 2])
with colA: force = st.checkbox("강제 재인덱싱 [PDF]", value=False)
with colB: embed_batch_size = st.number_input("임베딩 배치 [PDF]", min_value=1, max_value=128, value=16, step=1)

run_btn = st.button("🚀 규정 PDF 벡터 인덱싱 실행", type="secondary")

if run_btn:
    if not pdfs:
        st.error("PDF가 없습니다.")
    else:
        with st.spinner("PDF 인덱싱 실행 중..."):
            try:
                t0 = time.time()
                build_index(force=bool(force), embed_batch_size=int(embed_batch_size))
                st.success(f"✅ PDF 인덱싱 완료! (소요: {time.time() - t0:.1f}초)")
            except Exception as e:
                st.error(f"인덱싱 실패: {e}")

end_page()