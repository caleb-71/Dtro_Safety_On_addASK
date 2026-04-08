# rag/chunking/chunker.py
"""
Chunker (DTRO-Safety-On)

역할
- PDF에서 추출된 페이지 텍스트를 "검색 가능한 단위(Chunk)"로 분할한다.
- 각 Chunk는 출처 추적을 위해 최소 메타데이터를 포함한다.
  (문서명, 카테고리, 페이지, chunk_id, 텍스트 길이 등)

입력
- PdfPageText 리스트 (rag/loaders/pdf_text.py)

출력
- DocumentChunk 리스트

청킹 전략
- 기본: 문자 길이 기반 sliding window (chunk_size, overlap)
- 향후 고도화: 조항/절/문단 규칙 기반 split_rules로 확장 가능
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from core.config import get_settings
from core.logger import get_logger
from rag.loaders.pdf_text import PdfPageText

logger = get_logger(__name__)


@dataclass
class DocumentChunk:
    """
    검색/인덱싱에 사용하는 최소 단위.
    """
    doc_id: str               # 문서 고유 ID (보통 상대경로 기반)
    doc_title: str            # 사람이 보기 좋은 이름 (파일명)
    category: str             # laws / regulations / guidelines / accident_reports 등
    source_path: str          # 원본 PDF 경로(문자열)
    page_no: int              # 1부터 시작
    chunk_id: str             # 예: "{doc_id}::p{page_no}::c{n}"
    text: str                 # 청크 본문
    char_len: int             # 본문 길이(문자수)


def _guess_category_from_path(pdf_path: Path, docs_root: Path) -> str:
    """
    data/docs 하위 경로를 기준으로 category 추정
    예) data/docs/laws/xxx.pdf -> laws
        data/docs/regulations/yyy.pdf -> regulations
    """
    try:
        rel = pdf_path.resolve().relative_to(docs_root.resolve())
        parts = rel.parts
        if len(parts) >= 2:
            return parts[0]  # 첫 폴더명을 카테고리로
    except Exception:
        pass
    return "unknown"


def _make_doc_id(pdf_path: Path, docs_root: Path) -> str:
    """
    문서 ID는 되도록 안정적으로(경로 기반) 생성하는 게 좋습니다.
    - 예: laws/철도안전법_2024.pdf
    """
    try:
        rel = pdf_path.resolve().relative_to(docs_root.resolve())
        return str(rel).replace("\\", "/")
    except Exception:
        # docs_root 밖에 있으면 파일명으로
        return pdf_path.name


def _normalize_text_minimal(text: str) -> str:
    """
    과한 정규화는 규정 조항 구조를 망가뜨릴 수 있습니다.
    그래서 최소한만 합니다.
    """
    if not text:
        return ""
    # CR 제거, 탭은 공백으로
    t = text.replace("\r", "\n").replace("\t", " ")
    # 과도한 연속 공백만 축소 (문단 구조는 유지)
    while "  " in t:
        t = t.replace("  ", " ")
    # 과도한 줄바꿈 축소
    while "\n\n\n" in t:
        t = t.replace("\n\n\n", "\n\n")
    return t.strip()


def _sliding_window_chunks(
    text: str,
    chunk_size: int,
    overlap: int,
    min_chunk_chars: int,
) -> List[str]:
    """
    문자 단위 슬라이딩 윈도우로 청크 생성.
    - chunk_size: 청크 길이
    - overlap: 겹치는 길이
    - min_chunk_chars: 너무 짧으면 버림

    반환: 청크 텍스트 리스트
    """
    t = _normalize_text_minimal(text)
    if not t:
        return []

    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0:
        raise ValueError("chunk_overlap must be >= 0")
    if overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    chunks: List[str] = []
    start = 0
    n = len(t)

    while start < n:
        end = min(start + chunk_size, n)
        piece = t[start:end].strip()

        if len(piece) >= min_chunk_chars:
            chunks.append(piece)

        # 다음 시작점
        if end >= n:
            break
        start = end - overlap

        # 안전장치: 혹시나 무한루프 방지
        if start < 0:
            start = 0
        if len(chunks) > 100000:
            raise RuntimeError("Too many chunks created. Check chunk_size/overlap.")

    return chunks


def chunk_pdf_pages(
    pdf_path: Path,
    pages: Sequence[PdfPageText],
    docs_root: Optional[Path] = None,
    category: Optional[str] = None,
) -> List[DocumentChunk]:
    """
    PDF 페이지 텍스트를 받아 DocumentChunk 리스트로 변환.

    - pdf_path: 원본 PDF 경로
    - pages: extract_pdf_pages() 결과
    - docs_root: data/docs 루트(카테고리/문서ID 생성에 사용)
    - category: 강제 카테고리 지정(없으면 경로로 추정)

    반환:
    - DocumentChunk 리스트
    """
    settings = get_settings()

    docs_root_path = docs_root or settings.paths.docs_dir
    pdf_path = Path(pdf_path)

    doc_id = _make_doc_id(pdf_path, docs_root_path)
    doc_title = pdf_path.stem  # 확장자 제외 파일명
    cat = category or _guess_category_from_path(pdf_path, docs_root_path)

    chunk_size = settings.rag.chunk_size
    overlap = settings.rag.chunk_overlap
    min_chars = settings.rag.min_chunk_chars

    logger.info(
        f"[Chunker] start doc_id='{doc_id}' title='{doc_title}' category='{cat}' "
        f"chunk_size={chunk_size} overlap={overlap} min_chars={min_chars}"
    )

    out: List[DocumentChunk] = []
    total_pages = len(list(pages))

    for p_idx, p in enumerate(pages, start=1):
        page_no = int(p.page_no)
        if not p.text or len(p.text.strip()) == 0:
            logger.info(f"[Chunker] skip empty page {page_no}")
            continue

        pieces = _sliding_window_chunks(
            text=p.text,
            chunk_size=chunk_size,
            overlap=overlap,
            min_chunk_chars=min_chars,
        )

        for c_idx, piece in enumerate(pieces, start=1):
            chunk_id = f"{doc_id}::p{page_no}::c{c_idx}"
            chunk = DocumentChunk(
                doc_id=doc_id,
                doc_title=doc_title,
                category=cat,
                source_path=str(pdf_path).replace("\\", "/"),
                page_no=page_no,
                chunk_id=chunk_id,
                text=piece,
                char_len=len(piece),
            )
            out.append(chunk)

        logger.info(
            f"[Chunker] page={page_no}/{total_pages} "
            f"raw_chars={len(p.text)} chunks={len(pieces)}"
        )

    logger.info(f"[Chunker] done total_chunks={len(out)} doc_id='{doc_id}'")
    return out
