# shared/io/csv_loader.py
"""
CSV Loader (DTRO-Safety-On)

역할
- CSV를 pandas DataFrame으로 로드
- 기본 인코딩/구분자/결측치 처리
- (선택) 컬럼 존재 여부 검증, 표준화(공백/개행 제거)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CsvLoadResult:
    path: Path
    df: pd.DataFrame
    encoding: str
    delimiter: str


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    컬럼명 표준화(최소)
    - 앞뒤 공백 제거
    - 줄바꿈 제거
    """
    df = df.copy()
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
    return df


def load_csv(
    path: Path,
    encoding_candidates: Optional[List[str]] = None,
    delimiter_candidates: Optional[List[str]] = None,
    normalize_columns: bool = True,
) -> CsvLoadResult:
    """
    CSV 로드 (인코딩/구분자 자동 탐색)

    - encoding_candidates: 기본 ["utf-8-sig", "utf-8", "cp949"]
    - delimiter_candidates: 기본 [",", "\t", ";", "|"]
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")

    encs = encoding_candidates or ["utf-8-sig", "utf-8", "cp949"]
    seps = delimiter_candidates or [",", "\t", ";", "|"]

    last_err: Exception | None = None

    for enc in encs:
        for sep in seps:
            try:
                df = pd.read_csv(path, encoding=enc, sep=sep)
                if normalize_columns:
                    df = _normalize_columns(df)
                logger.info(f"[CSV] loaded ok: {path.name} rows={len(df)} cols={len(df.columns)} enc={enc} sep='{sep}'")
                return CsvLoadResult(path=path, df=df, encoding=enc, delimiter=sep)
            except Exception as e:
                last_err = e

    raise RuntimeError(f"CSV load failed for {path}. last_err={last_err}")


def ensure_columns(df: pd.DataFrame, required: List[str]) -> Tuple[bool, List[str]]:
    """
    필수 컬럼 존재 여부 확인
    반환: (ok, missing_columns)
    """
    cols = set(df.columns)
    missing = [c for c in required if c not in cols]
    return (len(missing) == 0, missing)
