# rag/loaders/pdf_text.py
"""
PDF 텍스트 추출기 (DTRO-Safety-On)

역할
- 텍스트 선택 가능한 PDF에서 "페이지 단위"로 텍스트를 추출한다.
- RAG 인덱싱(청킹/임베딩) 전에 가장 먼저 수행되는 단계.

주의
- 스캔본(이미지) PDF는 텍스트 추출이 거의 안 됩니다. (OCR 필요)
- 우리는 일단 '텍스트 선택 가능 PDF'를 전제로 간단/안정하게 구현합니다.

의존 라이브러리
- pypdf
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from pypdf import PdfReader

from core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PdfPageText:
    """PDF 한 페이지의 추출 결과"""
    page_no: int          # 1부터 시작
    text: str             # 추출된 텍스트(정리된 문자열)
    char_len: int         # text 길이(문자수)


def _clean_text(text: Optional[str]) -> str:
    """
    PDF 추출 텍스트는 줄바꿈/공백이 어색한 경우가 많아서
    최소한의 정리만 수행합니다.

    - None -> ""
    - 연속 공백/탭 정리
    - 너무 많은 줄바꿈 정리(기본 수준)
    """
    if not text:
        return ""

    # 기본 정리
    t = text.replace("\r", "\n")
    t = t.replace("\t", " ")

    # 연속 공백 정리 (너무 과한 정규화는 의미 구조를 깨뜨릴 수 있으니 최소만)
    while "  " in t:
        t = t.replace("  ", " ")

    # 줄바꿈 과다 정리(3개 이상 연속이면 2개로)
    while "\n\n\n" in t:
        t = t.replace("\n\n\n", "\n\n")

    return t.strip()


def extract_pdf_pages(pdf_path: Path) -> List[PdfPageText]:
    """
    PDF에서 페이지별 텍스트 추출.

    반환:
      List[PdfPageText]  (page_no=1..N)

    예외:
      - 파일이 없거나 PDF 파싱 불가 시 예외 발생
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    logger.info(f"[PDF] loading: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    logger.info(f"[PDF] total_pages={total_pages}")

    results: List[PdfPageText] = []

    for idx, page in enumerate(reader.pages):
        page_no = idx + 1
        raw = page.extract_text()  # pypdf 기본 텍스트 추출
        cleaned = _clean_text(raw)

        item = PdfPageText(
            page_no=page_no,
            text=cleaned,
            char_len=len(cleaned),
        )
        results.append(item)

        # 디버그: 너무 길게 찍지 않도록 앞부분만
        preview = cleaned[:120].replace("\n", " ")
        logger.info(f"[PDF] page={page_no}/{total_pages} chars={item.char_len} preview='{preview}'")

    return results


def extract_pdf_text(pdf_path: Path) -> str:
    """
    PDF 전체 텍스트를 하나로 합쳐 반환.

    - 페이지 구분을 위해 페이지 사이에 구분자(줄바꿈)를 넣습니다.
    - RAG에서는 보통 페이지 단위 메타가 중요하므로,
      인덱싱 파이프라인에서는 extract_pdf_pages() 사용을 권장합니다.
    """
    pages = extract_pdf_pages(pdf_path)
    full = "\n\n".join([p.text for p in pages if p.text])
    return full.strip()
