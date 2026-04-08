# apps/streamlit/pages/06_시스템_모니터링.py
from __future__ import annotations

from apps.streamlit._bootstrap import ensure_project_root_on_syspath
ensure_project_root_on_syspath()


import os
import platform
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from core.config import get_settings
from core.logger import get_logger

# ✅ 공식 경로 단일 진실
from rag.paths import INDEX_DIR, DATA_INDEX_DIR

# ✅ 벡터스토어(인덱스 상태/ntotal 확인용)
from rag.vectorstores.faiss_store import FaissVectorStore, DataFaissVectorStore

# ✅ Ollama 헬스체크
from backend.integrations.ollama_client import OllamaClient

# ✅ 공통 레이아웃/스타일
from apps.streamlit.ui.theme.layout import apply_layout, page_container, end_page, section, hero

logger = get_logger(__name__)
settings = get_settings()

st.set_page_config(page_title="시스템 모니터링", layout="wide")

apply_layout()
page_container()

hero(
    "🖥️ 06. 시스템 모니터링",
    "Ollama/인덱스/데이터 산출물/로그를 한 화면에서 점검하여 운영 장애를 빠르게 발견합니다.",
)

# ---------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------
def _fmt_bytes(n: int) -> str:
    if n is None:
        return "-"
    n = int(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _safe_exists(p: Path) -> bool:
    try:
        return p.exists()
    except Exception:
        return False


def _file_stat_row(p: Path, label: str) -> Dict[str, Any]:
    if not _safe_exists(p):
        return {
            "항목": label,
            "경로": str(p),
            "존재": "❌",
            "크기": "-",
            "최종수정": "-",
        }
    st_ = p.stat()
    return {
        "항목": label,
        "경로": str(p),
        "존재": "✅",
        "크기": _fmt_bytes(st_.st_size),
        "최종수정": _fmt_ts(st_.st_mtime),
    }


def _dir_disk_usage(path: Path) -> Dict[str, Any]:
    """
    Windows/Linux 공통으로 디스크 사용량을 보고(가장 가까운 드라이브 기준)
    """
    try:
        import shutil

        u = shutil.disk_usage(str(path))
        return {
            "총용량": _fmt_bytes(u.total),
            "사용": _fmt_bytes(u.used),
            "여유": _fmt_bytes(u.free),
            "사용률": f"{(u.used / u.total * 100):.1f}%" if u.total else "-",
        }
    except Exception:
        return {"총용량": "-", "사용": "-", "여유": "-", "사용률": "-"}


def _list_recent_files(folder: Path, exts: Tuple[str, ...], top_n: int = 10) -> List[Dict[str, Any]]:
    if not _safe_exists(folder):
        return []
    items: List[Path] = []
    for ext in exts:
        items += list(folder.rglob(f"*{ext}"))
    items = [p for p in items if p.is_file()]
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in items[:top_n]:
        st_ = p.stat()
        out.append(
            {
                "파일": p.name,
                "상대경로": str(p),
                "크기": _fmt_bytes(st_.st_size),
                "최종수정": _fmt_ts(st_.st_mtime),
            }
        )
    return out


# ---------------------------------------------------------
# 프로젝트 경로(내부)
# ---------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"

RAW_DIR = DATA_DIR / "raw"
CSV_DIR = DATA_DIR / "csv"
NORM_DIR = DATA_DIR / "norm"
META_DIR = DATA_DIR / "meta"

OUTPUTS_DIR = settings.paths.outputs_dir if hasattr(settings, "paths") else (DATA_DIR / "outputs")
REPORTS_DIR = OUTPUTS_DIR / "reports"
LOGS_DIR = OUTPUTS_DIR / "logs"

# ---------------------------------------------------------
# 새로고침 컨트롤
# ---------------------------------------------------------
c1, c2, c3 = st.columns([1, 1, 3])
with c1:
    refresh = st.button("🔄 새로고침", type="primary")
with c2:
    auto = st.toggle("자동 새로고침(10초)", value=False)
with c3:
    st.caption("※ 운영 중에는 자동 새로고침을 켜두면 인덱싱/보고서 생성 상태를 빠르게 확인할 수 있습니다.")

if auto:
    # Streamlit에서 간단한 주기 리렌더(강제)
    time.sleep(0.2)
    st.experimental_set_query_params(_ts=str(int(time.time())))
    st.rerun()

if refresh:
    st.rerun()


# =========================================================
# 1) 시스템/환경 헬스체크
# =========================================================
section("1️⃣ 서비스/환경 헬스체크")

env_col1, env_col2, env_col3 = st.columns([1.2, 1.2, 1.6])

with env_col1:
    st.metric("OS", platform.system())
    st.metric("OS 상세", platform.platform())

with env_col2:
    st.metric("Python", platform.python_version())
    st.metric("프로젝트 루트", str(PROJECT_ROOT))

with env_col3:
    st.write("🧭 인덱스 루트/디스크 사용량(안전 경로)")
    du = _dir_disk_usage(INDEX_DIR)
    st.write(f"- 총용량: {du['총용량']}")
    st.write(f"- 사용: {du['사용']} / 여유: {du['여유']} / 사용률: {du['사용률']}")

st.divider()

# Ollama 헬스 체크
oll_col1, oll_col2 = st.columns([1.2, 2.8])

with oll_col1:
    st.subheader("🧠 Ollama 상태")
    ok = False
    err = ""
    try:
        client = OllamaClient()
        ok = bool(client.ping())
    except Exception as e:
        ok = False
        err = str(e)

    st.metric("ping", "✅" if ok else "❌")
    if not ok and err:
        st.caption(f"원인: {err[:160]}")

with oll_col2:
    st.subheader("⚙️ 주요 설정(참고)")
    # settings 구조가 프로젝트마다 다를 수 있으니 방어적으로
    s = settings
    info = {
        "docs_dir": str(getattr(getattr(s, "paths", object()), "docs_dir", "")),
        "outputs_dir": str(getattr(getattr(s, "paths", object()), "outputs_dir", "")),
        "rag.top_k": str(getattr(getattr(s, "rag", object()), "top_k", "")),
        "ollama.base_url": str(getattr(getattr(s, "ollama", object()), "base_url", "")),
        "ollama.llm_model": str(getattr(getattr(s, "ollama", object()), "llm_model", "")),
        "ollama.embed_model": str(getattr(getattr(s, "ollama", object()), "embed_model", "")),
    }
    st.json(info)


# =========================================================
# 2) 인덱스 상태 (PDF / DATA)
# =========================================================
section("2️⃣ 인덱스 상태(FAISS)")

st.caption("✅ 여기서 체크: 파일 존재/크기/최종수정 + 인덱스 로드 가능 여부 + 벡터수(ntotal)")

# PDF 인덱스
st.markdown("### 📘 규정(PDF) 인덱스")
pdf_store = FaissVectorStore(index_dir=INDEX_DIR)

pdf_rows = [
    _file_stat_row(pdf_store.faiss_path, "faiss.index"),
    _file_stat_row(pdf_store.meta_path, "meta.jsonl"),
    _file_stat_row(pdf_store.checksum_path, "checksum.json"),
]
pdf_df = pd.DataFrame(pdf_rows)
st.dataframe(pdf_df, use_container_width=True, height=160)

pdf_ntotal = "N/A"
pdf_dim = "N/A"
if pdf_store.faiss_path.exists() and pdf_store.meta_path.exists():
    try:
        pdf_store.load()
        pdf_ntotal = str(pdf_store.ntotal)
        pdf_dim = str(pdf_store.dim)
    except Exception as e:
        pdf_ntotal = "로드 실패"
        pdf_dim = "로드 실패"
        logger.exception(e)

m1, m2, m3 = st.columns([1, 1, 2])
m1.metric("PDF ntotal", pdf_ntotal)
m2.metric("PDF dim", pdf_dim)
m3.write(f"공식 경로: `{INDEX_DIR}`")

st.divider()

# DATA 인덱스
st.markdown("### 📦 데이터(safety_map/trend) 인덱스")
data_store = DataFaissVectorStore(index_dir=DATA_INDEX_DIR)

data_rows = [
    _file_stat_row(data_store.faiss_path, "faiss.index"),
    _file_stat_row(data_store.meta_path, "meta.jsonl"),
    _file_stat_row(data_store.checksum_path, "checksum.json"),
]
data_df = pd.DataFrame(data_rows)
st.dataframe(data_df, use_container_width=True, height=160)

data_ntotal = "N/A"
data_dim = "N/A"
if data_store.faiss_path.exists() and data_store.meta_path.exists():
    try:
        data_store.load()
        data_ntotal = str(data_store.ntotal)
        data_dim = str(data_store.dim)
    except Exception as e:
        data_ntotal = "로드 실패"
        data_dim = "로드 실패"
        logger.exception(e)

n1, n2, n3 = st.columns([1, 1, 2])
n1.metric("DATA ntotal", data_ntotal)
n2.metric("DATA dim", data_dim)
n3.write(f"공식 경로: `{DATA_INDEX_DIR}`")

st.divider()

st.markdown("### ✅ 운영 체크 포인트")
st.markdown(
    """
- **PDF 인덱스**: `INDEX_DIR/faiss.index` + `INDEX_DIR/meta.jsonl` 이 존재해야 Retriever/QA가 정상 동작합니다.  
- **DATA 인덱스**: `DATA_INDEX_DIR/faiss.index` + `DATA_INDEX_DIR/meta.jsonl` 이 존재해야 데이터 RAG 확장이 가능합니다.  
- `로드 실패`이면: 파일 손상/차원 불일치/인덱싱 중단 가능성이 있으니 **05_인덱스_관리**에서 재생성 권장.
"""
)


# =========================================================
# 3) 데이터 산출물 상태 (raw/csv/norm/meta)
# =========================================================
section("3️⃣ 데이터 산출물 상태(raw/csv/norm/meta)")

# 주요 파일 후보(프로젝트 정책: norm 우선)
trend_norm = DATA_DIR / "norm" / "trend" / "trend_norm.csv"
smap_norm = DATA_DIR / "norm" / "safety_map" / "safety_map_norm.csv"

trend_csv = DATA_DIR / "csv" / "trend.csv"
smap_csv = DATA_DIR / "csv" / "safety_map.csv"

trend_meta = DATA_DIR / "meta" / "trend" / "meta.jsonl"
smap_meta = DATA_DIR / "meta" / "safety_map" / "meta.jsonl"

rows = [
    _file_stat_row(trend_norm, "trend_norm.csv (권장)"),
    _file_stat_row(smap_norm, "safety_map_norm.csv (권장)"),
    _file_stat_row(trend_csv, "trend.csv (fallback)"),
    _file_stat_row(smap_csv, "safety_map.csv (fallback)"),
    _file_stat_row(trend_meta, "trend/meta.jsonl"),
    _file_stat_row(smap_meta, "safety_map/meta.jsonl"),
]
st.dataframe(pd.DataFrame(rows), use_container_width=True, height=260)

st.caption("📌 디렉토리 존재 여부")
dcol1, dcol2, dcol3, dcol4 = st.columns(4)
dcol1.metric("data/raw", "✅" if RAW_DIR.exists() else "❌")
dcol2.metric("data/csv", "✅" if CSV_DIR.exists() else "❌")
dcol3.metric("data/norm", "✅" if NORM_DIR.exists() else "❌")
dcol4.metric("data/meta", "✅" if META_DIR.exists() else "❌")

with st.expander("💡 가이드"):
    st.markdown(
        """
- **05_인덱스_관리**에서 `norm/meta 생성`을 누르면 아래가 생성되어야 정상입니다.
  - `data/norm/trend/trend_norm.csv`
  - `data/meta/trend/meta.jsonl`
  - `data/norm/safety_map/safety_map_norm.csv`
  - `data/meta/safety_map/meta.jsonl`
- 이후 `데이터 인덱싱 실행`을 하면 `DATA_INDEX_DIR`에 인덱스가 만들어집니다.
"""
    )


# =========================================================
# 4) 로그/리포트(출력) 상태
# =========================================================
section("4️⃣ 출력 상태(logs/reports)")

# 폴더 보장(없어도 모니터링은 되게)
try:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

colA, colB = st.columns(2)

with colA:
    st.markdown("### 🧾 최근 보고서(PDF)")
    recent_reports = _list_recent_files(REPORTS_DIR, exts=(".pdf",), top_n=12)
    if not recent_reports:
        st.info("최근 보고서 파일이 없습니다.")
    else:
        st.dataframe(pd.DataFrame(recent_reports), use_container_width=True, height=280)

with colB:
    st.markdown("### 🧰 최근 로그")
    recent_logs = _list_recent_files(LOGS_DIR, exts=(".log", ".txt"), top_n=12)
    if not recent_logs:
        st.info("최근 로그 파일이 없습니다.")
    else:
        st.dataframe(pd.DataFrame(recent_logs), use_container_width=True, height=280)

st.divider()

# ---------------------------------------------------------
# 5) 빠른 점검 체크리스트(운영자용)
# ---------------------------------------------------------
section("✅ 빠른 점검 체크리스트(운영자용)")

checks = []

# Ollama
checks.append(("Ollama ping", "✅" if ok else "❌", "Ollama가 꺼져있으면 임베딩/LLM 호출이 실패합니다."))

# PDF index
pdf_ok = pdf_store.faiss_path.exists() and pdf_store.meta_path.exists()
checks.append(("PDF 인덱스 파일", "✅" if pdf_ok else "❌", "규정 QA/보고서 근거 주입은 PDF 인덱스가 필요합니다."))

# DATA index
data_ok = data_store.faiss_path.exists() and data_store.meta_path.exists()
checks.append(("DATA 인덱스 파일", "✅" if data_ok else "❌", "데이터 RAG 확장을 쓰려면 DATA 인덱스가 필요합니다."))

# meta 산출물
meta_ok = trend_meta.exists() and smap_meta.exists()
checks.append(("meta.jsonl 산출물", "✅" if meta_ok else "❌", "data/meta/**/meta.jsonl 이 없으면 data 인덱싱이 스킵됩니다."))

# norm 산출물
norm_ok = trend_norm.exists() and smap_norm.exists()
checks.append(("norm.csv 산출물", "✅" if norm_ok else "❌", "대시보드/QA는 norm 우선 사용을 권장합니다."))

df_checks = pd.DataFrame(checks, columns=["체크 항목", "상태", "설명"])
st.dataframe(df_checks, use_container_width=True, height=240)

st.caption("※ 빨간 항목(❌)이 있으면, 대부분은 `05_인덱스_관리`에서 생성/재생성으로 해결됩니다.")

end_page()
