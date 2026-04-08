# rag/paths.py
from __future__ import annotations

import os
from pathlib import Path


def get_index_root() -> Path:
    """
    인덱스 루트 디렉토리 결정 규칙 (단일 진실)

    1) 환경변수 DTRO_INDEX_ROOT가 있으면 최우선 사용
       - 다른 PC / 서버에서 위치를 바꾸고 싶을 때 사용
    2) 기본값: 모든 Windows PC에서 한글 경로 문제 없는 안전 경로
       - C:\\DTRO_DATA
    """
    env = os.getenv("DTRO_INDEX_ROOT")
    if env:
        return Path(env)

    return Path(r"C:\DTRO_DATA")


def ensure_dir(path: Path) -> Path:
    """디렉토리 생성 보장(없으면 생성) 후 path 반환"""
    path.mkdir(parents=True, exist_ok=True)
    return path


# =========================================================
# Index Root (Single Source of Truth)
# =========================================================
INDEX_ROOT = ensure_dir(get_index_root())


# =========================================================
# 1) 규정 / PDF 전용 RAG 인덱스
#    - data/docs/**.pdf
#    - dataops/pipelines/build_index.py 에서 사용
# =========================================================
INDEX_DIR = ensure_dir(INDEX_ROOT / "index")

FAISS_INDEX_PATH = INDEX_DIR / "faiss.index"
META_PATH = INDEX_DIR / "meta.jsonl"
CHECKSUM_PATH = INDEX_DIR / "checksum.json"  # FaissVectorStore가 사용하는 checksum 파일(기본)


# (선택) 확장 파일(지금 당장 안 써도 됨: 미래 대비용)
MANIFEST_PATH = INDEX_DIR / "manifest.json"
STATS_PATH = INDEX_DIR / "stats.json"
OFFSETS_PATH = INDEX_DIR / "offsets.json"


# =========================================================
# 2) 안전지도 / 사고 데이터 전용 RAG 인덱스
#    - data/meta/safety_map/meta.jsonl
#    - data/meta/trend/meta.jsonl
#    - dataops/pipelines/build_data_index.py (다음 단계)
# =========================================================
DATA_INDEX_DIR = ensure_dir(INDEX_ROOT / "data_index")

DATA_FAISS_INDEX_PATH = DATA_INDEX_DIR / "faiss.index"
DATA_META_PATH = DATA_INDEX_DIR / "meta.jsonl"
DATA_CHECKSUM_PATH = DATA_INDEX_DIR / "checksum.json"

# (선택) 확장 파일(지금 당장 안 써도 됨: 미래 대비용)
DATA_MANIFEST_PATH = DATA_INDEX_DIR / "manifest.json"
DATA_STATS_PATH = DATA_INDEX_DIR / "stats.json"
DATA_OFFSETS_PATH = DATA_INDEX_DIR / "offsets.json"
