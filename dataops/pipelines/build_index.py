# dataops/pipelines/build_index.py
"""
DTRO-Safety-On RAG 인덱싱 파이프라인

기능
- data/docs/**.pdf 스캔
- checksum.json으로 "문서 변경" 감지
- 변경이 있으면: 전체 재인덱싱(안정/단순)
- 결과 생성:
  - C:\DTRO_DATA\index\faiss.index
  - C:\DTRO_DATA\index\meta.jsonl
  - C:\DTRO_DATA\index\checksum.json

실행
- (가상환경) python dataops/pipelines/build_index.py
- 옵션:
    python dataops/pipelines/build_index.py --force
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Dict, List, Tuple

from core.config import get_settings
from core.logger import get_logger

from backend.integrations.ollama_client import OllamaClient
from rag.loaders.pdf_text import extract_pdf_pages
from rag.chunking.chunker import chunk_pdf_pages, DocumentChunk
from rag.vectorstores.faiss_store import FaissVectorStore, ChunkMeta, sha256_file

# ✅ 인덱스 저장은 settings가 아니라 rag.paths가 단일 진실
from rag.paths import INDEX_DIR

logger = get_logger(__name__)


def _scan_pdfs(docs_dir: Path) -> List[Path]:
    """data/docs 하위의 PDF를 재귀 스캔"""
    pdfs = sorted([p for p in docs_dir.rglob("*.pdf") if p.is_file()])
    return pdfs


def _compute_checksums(pdfs: List[Path], docs_dir: Path) -> Dict[str, str]:
    """
    PDF 목록에 대해 checksum(dict) 생성
    key: docs_dir 기준 상대경로(슬래시 통일)
    value: sha256 hex
    """
    out: Dict[str, str] = {}
    for p in pdfs:
        rel = str(p.resolve().relative_to(docs_dir.resolve())).replace("\\", "/")
        out[rel] = sha256_file(p)
    return out


def _has_changes(old: Dict[str, str], new: Dict[str, str]) -> Tuple[bool, Dict[str, int]]:
    """checksum 비교로 변경 여부 판단"""
    old_keys = set(old.keys())
    new_keys = set(new.keys())

    added = len(new_keys - old_keys)
    removed = len(old_keys - new_keys)

    modified = 0
    for k in (old_keys & new_keys):
        if old.get(k) != new.get(k):
            modified += 1

    changed = (added + removed + modified) > 0
    return changed, {"added": added, "modified": modified, "removed": removed}


def _reset_index_files(index_dir: Path) -> None:
    """
    전체 재인덱싱 시, 기존 인덱스/메타/체크섬 파일을 삭제(초기화)
    """
    settings = get_settings()

    faiss_path = index_dir / settings.index.faiss_index_file
    meta_path = index_dir / settings.index.meta_file
    checksum_path = index_dir / settings.index.checksum_file

    for p in [faiss_path, meta_path, checksum_path]:
        if p.exists():
            p.unlink()
            logger.info(f"[Index] removed old file: {p}")


def _batch(iterable: List[str], batch_size: int) -> List[List[str]]:
    """리스트를 배치 단위로 분할"""
    return [iterable[i:i + batch_size] for i in range(0, len(iterable), batch_size)]


def build_index(force: bool = False, embed_batch_size: int = 16) -> None:
    """인덱스 구축 메인 함수"""
    settings = get_settings()

    # docs_dir는 settings 기반(프로젝트 내 data/docs)
    docs_dir = settings.paths.docs_dir

    # ✅ index_dir는 무조건 C:\DTRO_DATA\index (rag/paths.py 기준)
    index_dir = INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"[BuildIndex] docs_dir={docs_dir}")
    logger.info(f"[BuildIndex] index_dir(OFFICIAL)={index_dir}")

    pdfs = _scan_pdfs(docs_dir)
    logger.info(f"[BuildIndex] found pdfs={len(pdfs)}")

    if len(pdfs) == 0:
        logger.warning("[BuildIndex] No PDFs found. Put PDFs under data/docs/**.pdf")
        return

    # 현재 checksum 계산
    new_checksums = _compute_checksums(pdfs, docs_dir)

    # ✅ Store는 경로 주입하지 않음(= rag.paths 기준으로 동작)
    store = FaissVectorStore()

    # 기존 checksum 로드
    old_checksums = store.load_checksums()
    changed, stat = _has_changes(old_checksums, new_checksums)

    # 인덱스/메타 파일 존재 여부(공식 index_dir 기준)
    faiss_file = index_dir / settings.index.faiss_index_file
    meta_file = index_dir / settings.index.meta_file
    has_index_files = faiss_file.exists() and meta_file.exists()

    logger.info(f"[BuildIndex] has_index_files={has_index_files} force={force} changed={changed} stat={stat}")

    # 변경 없고 인덱스 파일 존재하면 스킵
    if (not force) and has_index_files and (not changed):
        logger.info("[BuildIndex] No changes detected. Skip indexing.")
        return

    # 안정성을 위해: 변경이 있으면 전체 재인덱싱
    logger.info("[BuildIndex] Rebuild index (full) started...")
    _reset_index_files(index_dir)

    # Ollama 준비
    client = OllamaClient()
    if not client.ping():
        raise RuntimeError("Ollama server not reachable. Check Ollama is running and base_url is correct.")

    # 임베딩 차원(dimension) 프로브
    probe = client.embed("dimension probe")
    if not probe.embeddings or not probe.embeddings[0]:
        raise RuntimeError("Embedding probe failed. Check embed_model in settings.yaml and Ollama models.")
    dim = len(probe.embeddings[0])
    logger.info(f"[BuildIndex] embedding dim={dim}")

    # 새 인덱스 생성
    store.load_or_create(dim=dim)

    total_chunks = 0
    t0_all = time.time()

    # PDF별 처리
    for idx, pdf_path in enumerate(pdfs, start=1):
        logger.info(f"[BuildIndex] ({idx}/{len(pdfs)}) processing pdf={pdf_path}")

        # 1) PDF -> pages
        pages = extract_pdf_pages(pdf_path)

        # 2) pages -> chunks (메타 포함)
        chunks: List[DocumentChunk] = chunk_pdf_pages(
            pdf_path=pdf_path,
            pages=pages,
            docs_root=docs_dir,
        )

        if not chunks:
            logger.warning(f"[BuildIndex] no chunks created (maybe empty text). pdf={pdf_path}")
            continue

        # 3) chunks -> embeddings + metas -> FAISS add
        texts = [c.text for c in chunks]
        metas = [
            ChunkMeta(
                chunk_id=c.chunk_id,
                doc_id=c.doc_id,
                doc_title=c.doc_title,
                category=c.category,
                source_path=c.source_path,
                page_no=c.page_no,
                text=c.text,
                char_len=c.char_len,
            )
            for c in chunks
        ]

        batches = _batch(texts, embed_batch_size)
        meta_batches = [metas[i:i + embed_batch_size] for i in range(0, len(metas), embed_batch_size)]

        for b_i, (text_batch, meta_batch) in enumerate(zip(batches, meta_batches), start=1):
            emb_res = client.embed(text_batch)
            store.add(
                embeddings=emb_res.embeddings,
                metas=meta_batch,
                append_meta=True,
                save_index=False,  # 마지막에 한번 저장
            )
            logger.info(
                f"[BuildIndex] pdf={pdf_path.name} batch={b_i}/{len(batches)} "
                f"added={len(meta_batch)} ntotal={store.ntotal}"
            )

        total_chunks += len(chunks)
        time.sleep(0.1)

    # 최종 저장
    store.save()
    store.save_checksums(new_checksums)

    dt_all = time.time() - t0_all
    logger.info(
        f"[BuildIndex] DONE. pdfs={len(pdfs)} total_chunks={total_chunks} "
        f"ntotal={store.ntotal} elapsed={dt_all:.2f}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="DTRO-Safety-On - Build RAG Index (FAISS)")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if no changes detected.")
    parser.add_argument("--embed-batch-size", type=int, default=16, help="Embedding batch size (default=16).")
    args = parser.parse_args()

    build_index(force=bool(args.force), embed_batch_size=int(args.embed_batch_size))


if __name__ == "__main__":
    main()
