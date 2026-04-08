# rag/vectorstores/faiss_store.py

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import faiss
import numpy as np

from core.config import get_settings
from core.logger import get_logger

from typing import Any

# ✅ 여기서 "공식 경로"를 가져온다 (C:\DTRO_DATA\index)
from rag.paths import DATA_INDEX_DIR, INDEX_DIR, FAISS_INDEX_PATH, META_PATH

logger = get_logger(__name__)


@dataclass
class ChunkMeta:
    chunk_id: str
    doc_id: str
    doc_title: str
    category: str
    source_path: str
    page_no: int
    text: str
    char_len: int


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class FaissVectorStore:
    """
    FAISS 인덱스 + meta.jsonl + checksum.json을 관리하는 저장소 클래스
    """

    def __init__(
        self,
        index_dir: Optional[Path] = None,
        dim: Optional[int] = None,
        normalize: bool = True,
    ):
        s = get_settings()

        # ✅ 기본값은 무조건 rag.paths.INDEX_DIR (C:\DTRO_DATA\index)
        # - index_dir를 명시로 넘기면 그걸 쓰되,
        #   웬만하면 기본(공식 경로)을 쓰는 걸 권장
        self.index_dir = Path(index_dir) if index_dir else INDEX_DIR
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # ✅ 파일명은 설정값을 따르되, 경로는 "공식 index_dir" 아래로 고정
        faiss_file = getattr(s.index, "faiss_index_file", "faiss.index")
        meta_file = getattr(s.index, "meta_file", "meta.jsonl")
        checksum_file = getattr(s.index, "checksum_file", "checksum.json")

        self.faiss_path = self.index_dir / faiss_file
        self.meta_path = self.index_dir / meta_file
        self.checksum_path = self.index_dir / checksum_file

        # ✅ 혹시 설정 파일명이 바뀌어도, 최소한 기본 상수 경로와 동일하게 가도록 보호
        # (원하면 아래 2줄은 삭제해도 됨)
        # self.faiss_path = FAISS_INDEX_PATH
        # self.meta_path = META_PATH

        self.dim = dim
        self.normalize = bool(normalize)

        self.index: Optional[faiss.Index] = None
        self.metas: List[ChunkMeta] = []

        logger.info(
            f"[FaissStore] init index_dir={self.index_dir} "
            f"faiss_path={self.faiss_path} meta_path={self.meta_path} "
            f"checksum_path={self.checksum_path}"
        )

    # -------------------------
    # Load / Create
    # -------------------------
    def load(self) -> None:
        if not self.faiss_path.exists():
            raise FileNotFoundError(f"FAISS index not found: {self.faiss_path}")
        if not self.meta_path.exists():
            raise FileNotFoundError(f"meta.jsonl not found: {self.meta_path}")

        self.index = faiss.read_index(str(self.faiss_path))
        self.metas = self._load_meta_jsonl(self.meta_path)

        try:
            self.dim = int(self.index.d)
        except Exception:
            pass

        logger.info(f"[FaissStore] loaded index ntotal={self.ntotal} dim={self.dim} metas={len(self.metas)}")

        if self.index is not None and self.index.ntotal != len(self.metas):
            logger.warning(
                f"[FaissStore] WARNING: index.ntotal({self.index.ntotal}) != metas({len(self.metas)}). "
                f"meta.jsonl과 faiss.index가 불일치합니다."
            )

    def load_or_create(self, dim: int) -> None:
        self.dim = int(dim)

        if self.faiss_path.exists() and self.meta_path.exists():
            self.load()
            return

        self.index = faiss.IndexFlatIP(self.dim)
        self.metas = []

        if not self.meta_path.exists():
            self.meta_path.write_text("", encoding="utf-8")

        logger.info(f"[FaissStore] created new index dim={self.dim}")

    # -------------------------
    # Save (Atomic)
    # -------------------------
    def save(self) -> None:
        if self.index is None:
            raise RuntimeError("index is None. load_or_create() first.")

        tmp_path = self.faiss_path.with_suffix(self.faiss_path.suffix + ".tmp")
        faiss.write_index(self.index, str(tmp_path))
        tmp_path.replace(self.faiss_path)

        logger.info(f"[FaissStore] saved faiss index ntotal={self.ntotal} -> {self.faiss_path}")

    # -------------------------
    # Add vectors + metas
    # -------------------------
    def add(
        self,
        embeddings: List[List[float]],
        metas: List[ChunkMeta],
        append_meta: bool = True,
        save_index: bool = False,
    ) -> None:
        if self.index is None:
            raise RuntimeError("index is None. load_or_create() first.")

        if len(embeddings) != len(metas):
            raise ValueError(f"embeddings({len(embeddings)}) != metas({len(metas)})")

        if not embeddings:
            logger.info("[FaissStore] add skipped: empty embeddings")
            return

        X = np.array(embeddings, dtype=np.float32)

        if self.dim is None:
            self.dim = int(X.shape[1])
        if X.shape[1] != self.dim:
            raise ValueError(f"embedding dim mismatch. expected={self.dim} got={X.shape[1]}")

        if self.normalize:
            faiss.normalize_L2(X)

        before = self.ntotal
        self.index.add(X)
        after = self.ntotal

        if append_meta:
            self._append_meta_jsonl(self.meta_path, metas)
        self.metas.extend(metas)

        logger.info(f"[FaissStore] add ok vectors={len(metas)} ntotal {before} -> {after}")

        if save_index:
            self.save()

    # -------------------------
    # Search
    # -------------------------
    def search(self, query_embedding: List[float], top_k: int = 5) -> List[Tuple[float, ChunkMeta]]:
        if self.index is None:
            raise RuntimeError("index is None. load_or_create() or load() first.")
        if self.ntotal == 0:
            return []

        q = np.array([query_embedding], dtype=np.float32)

        if self.dim is None:
            self.dim = int(q.shape[1])
        if q.shape[1] != self.dim:
            raise ValueError(f"query dim mismatch. expected={self.dim} got={q.shape[1]}")

        if self.normalize:
            faiss.normalize_L2(q)

        k = min(int(top_k), int(self.ntotal))
        D, I = self.index.search(q, k)

        results: List[Tuple[float, ChunkMeta]] = []
        for score, idx in zip(D[0].tolist(), I[0].tolist()):
            if idx < 0:
                continue
            if idx >= len(self.metas):
                logger.warning(f"[FaissStore] meta out of range idx={idx} metas={len(self.metas)}")
                continue
            results.append((float(score), self.metas[idx]))
        return results

    # -------------------------
    # Checksums
    # -------------------------
    def load_checksums(self) -> Dict[str, str]:
        if not self.checksum_path.exists():
            return {}
        try:
            data = json.loads(self.checksum_path.read_text(encoding="utf-8") or "{}")
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
            return {}
        except Exception as e:
            logger.warning(f"[FaissStore] checksum load failed -> empty. err={e}")
            return {}

    def save_checksums(self, checksums: Dict[str, str]) -> None:
        tmp = self.checksum_path.with_suffix(self.checksum_path.suffix + ".tmp")
        tmp.write_text(json.dumps(checksums, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.checksum_path)
        logger.info(f"[FaissStore] saved checksum.json items={len(checksums)}")

    @property
    def ntotal(self) -> int:
        return int(self.index.ntotal) if self.index is not None else 0

    # -------------------------
    # Meta I/O
    # -------------------------
    def _load_meta_jsonl(self, path: Path) -> List[ChunkMeta]:
        metas: List[ChunkMeta] = []
        with Path(path).open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    metas.append(ChunkMeta(**obj))
                except Exception as e:
                    logger.warning(f"[FaissStore] meta.jsonl parse failed line={line_no} err={e}")
        return metas

    def _append_meta_jsonl(self, path: Path, metas: List[ChunkMeta]) -> None:
        if not metas:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("a", encoding="utf-8") as f:
            for m in metas:
                f.write(json.dumps(asdict(m), ensure_ascii=False) + "\n")
        logger.info(f"[FaissStore] appended meta.jsonl lines={len(metas)} -> {path}")

# -------------------------
# (신규) Data 전용 메타/스토어
# -------------------------

@dataclass
class DataChunkMeta:
    """
    safety_map / trend 같은 구조화 데이터용 메타 스키마
    - chunk_id: meta.jsonl의 id (예: ROWID::c0)
    - doc_id: row_id (검색 결과를 원본 row로 연결)
    - doc_title: 표시용(없으면 dataset/title 등으로 구성)
    - category: dataset 이름 (safety_map / trend)
    - source_path: raw_ref 등 출처
    - page_no: 구조화 데이터는 페이지 개념이 없어서 0 고정
    - text: 임베딩한 텍스트
    - char_len: text 길이
    """
    chunk_id: str
    doc_id: str
    doc_title: str
    category: str
    source_path: str
    page_no: int
    text: str
    char_len: int


class DataFaissVectorStore(FaissVectorStore):
    """
    구조화 데이터(safety_map/trend)의 meta.jsonl 형식에 맞춘 FAISS Store
    - 저장 위치: rag.paths.DATA_INDEX_DIR (C:\\DTRO_DATA\\data_index)
    """

    def __init__(self, index_dir: Optional[Path] = None, dim: Optional[int] = None, normalize: bool = True):
        # ✅ 기본값은 DATA_INDEX_DIR
        super().__init__(index_dir=index_dir or DATA_INDEX_DIR, dim=dim, normalize=normalize)

        logger.info(
            f"[DataFaissStore] init data_index_dir={self.index_dir} "
            f"faiss_path={self.faiss_path} meta_path={self.meta_path} checksum_path={self.checksum_path}"
        )

    # ✅ Data 메타 로드는 DataChunkMeta로
    def _load_meta_jsonl(self, path: Path) -> List[DataChunkMeta]:  # type: ignore[override]
        metas: List[DataChunkMeta] = []
        with Path(path).open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)

                    # 우리가 만든 데이터 meta.jsonl 포맷:
                    # { "id","row_id","dataset","text","metadata":{...} }
                    chunk_id = str(obj.get("id", ""))
                    row_id = str(obj.get("row_id", "")) or str(obj.get("doc_id", ""))
                    dataset = str(obj.get("dataset", "")) or str(obj.get("category", "data"))
                    text = str(obj.get("text", ""))
                    md = obj.get("metadata", {}) or {}
                    source_path = str(md.get("raw_ref", ""))  # 원본 경로를 가장 중요하게

                    # 표시용 제목: dataset + (title/summary 등) 우선
                    title_hint = ""
                    for k in ["title", "summary", "issue_type", "incident_type"]:
                        if k in md and str(md.get(k)).strip():
                            title_hint = str(md.get(k)).strip()
                            break
                    doc_title = f"{dataset} | {title_hint}" if title_hint else dataset

                    metas.append(
                        DataChunkMeta(
                            chunk_id=chunk_id,
                            doc_id=row_id,
                            doc_title=doc_title,
                            category=dataset,
                            source_path=source_path,
                            page_no=0,
                            text=text,
                            char_len=len(text),
                        )
                    )
                except Exception as e:
                    logger.warning(f"[DataFaissStore] meta.jsonl parse failed line={line_no} err={e}")
        return metas

    # ✅ Data 메타 append: DataChunkMeta를 그대로 jsonl로 저장 (asdict)
    def _append_meta_jsonl(self, path: Path, metas: List[DataChunkMeta]) -> None:  # type: ignore[override]
        if not metas:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("a", encoding="utf-8") as f:
            for m in metas:
                f.write(json.dumps(asdict(m), ensure_ascii=False) + "\n")
        logger.info(f"[DataFaissStore] appended meta.jsonl lines={len(metas)} -> {path}")
