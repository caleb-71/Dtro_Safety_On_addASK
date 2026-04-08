# dataops/pipelines/build_norm_and_meta.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from core.logger import get_logger
from core.utils.hash_utils import generate_row_id
from core.utils.text_utils import clean_text
from core.utils.time_utils import to_iso8601

logger = get_logger(__name__)


@dataclass(frozen=True)
class BuildConfig:
    source: str
    dataset: str
    dataset_prefix: str

    # raw 컬럼 -> norm 컬럼 매핑
    field_map: Dict[str, str]

    # row_id 안정키(순서 고정)
    stable_fields: List[str]

    # meta.text 구성 필드(순서 고정)
    meta_text_fields: List[str]

    # meta.metadata 구성 필드
    meta_metadata_fields: List[str]


def load_raw_table(path: Path) -> pd.DataFrame:
    suf = path.suffix.lower()
    if suf == ".csv":
        try:
            return pd.read_csv(path)
        except Exception as e:
            logger.warning(f"[raw_loader] utf-8 실패, cp949 재시도: {e}")
            return pd.read_csv(path, encoding="cp949")
    if suf in [".xlsx", ".xls"]:
        return pd.read_excel(path)
    raise ValueError(f"지원하지 않는 raw 형식: {path}")


def normalize_df(
    df_raw: pd.DataFrame,
    *,
    cfg: BuildConfig,
    ingested_at: str,
    raw_ref: str,
    required_fields: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    raw -> norm
    - 컬럼 매핑
    - 필수 컬럼 보장
    - 날짜 정규화(checked_at/occurred_at 등)
    - 텍스트 정리
    """
    required_fields = required_fields or []

    rename_map = {k: v for k, v in cfg.field_map.items() if k in df_raw.columns}
    df = df_raw.rename(columns=rename_map).copy()

    # 공통 메타
    df["source"] = cfg.source
    df["dataset"] = cfg.dataset
    df["raw_ref"] = raw_ref
    df["ingested_at"] = ingested_at

    # 필수 컬럼 생성
    for col in required_fields:
        if col not in df.columns:
            df[col] = ""

    # 날짜 정규화: 흔히 쓰는 컬럼들이 있으면 자동 처리
    for date_col in ["checked_at", "action_completed_at", "occurred_at", "created_at", "updated_at"]:
        if date_col in df.columns:
            df[date_col] = df[date_col].apply(to_iso8601)

    # 텍스트 정리
    for c in df.columns:
        df[c] = df[c].apply(clean_text)

    return df


def assign_row_ids(df_norm: pd.DataFrame, *, cfg: BuildConfig) -> pd.DataFrame:
    row_ids: List[str] = []
    for _, r in df_norm.iterrows():
        rid = generate_row_id(
            r.to_dict(),
            dataset_prefix=cfg.dataset_prefix,
            source=cfg.source,
            dataset=cfg.dataset,
            stable_fields=cfg.stable_fields,
            hash_len=12,
        )
        row_ids.append(rid)
    df_norm["row_id"] = row_ids
    return df_norm


def build_meta_rows(df_norm: pd.DataFrame, *, cfg: BuildConfig):
    for _, r in df_norm.iterrows():
        row_id = clean_text(r.get("row_id", ""))

        # text(임베딩 대상)
        parts: List[str] = []
        for f in cfg.meta_text_fields:
            v = clean_text(r.get(f, ""))
            if v:
                parts.append(f"{f}: {v}")
        text = "\n".join(parts).strip()

        # metadata(필터/표시용)
        md: Dict[str, Any] = {}
        for f in cfg.meta_metadata_fields:
            md[f] = clean_text(r.get(f, ""))

        yield {
            "id": f"{row_id}::c0",
            "row_id": row_id,
            "dataset": cfg.dataset,
            "source": cfg.source,
            "text": text,
            "metadata": md,
        }


def write_jsonl(rows, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for obj in rows:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            n += 1
    return n


def run_build_norm_and_meta(
    *,
    raw_path: Path,
    norm_out: Path,
    meta_out: Path,
    cfg: BuildConfig,
    required_fields: Optional[List[str]] = None,
) -> None:
    raw_path = raw_path.resolve()
    norm_out = norm_out.resolve()
    meta_out = meta_out.resolve()

    logger.info(f"[build_norm_meta] raw 로드: {raw_path}")
    df_raw = load_raw_table(raw_path)
    logger.info(f"[build_norm_meta] raw rows={len(df_raw)} cols={len(df_raw.columns)}")

    ingested_at = datetime.now().isoformat(timespec="seconds")

    df_norm = normalize_df(
        df_raw,
        cfg=cfg,
        ingested_at=ingested_at,
        raw_ref=str(raw_path),
        required_fields=required_fields,
    )

    df_norm = assign_row_ids(df_norm, cfg=cfg)

    # norm 저장
    norm_out.parent.mkdir(parents=True, exist_ok=True)
    df_norm.to_csv(norm_out, index=False, encoding="utf-8-sig")
    logger.info(f"[build_norm_meta] norm 저장: {norm_out} (rows={len(df_norm)})")

    # meta 저장
    n = write_jsonl(build_meta_rows(df_norm, cfg=cfg), meta_out)
    logger.info(f"[build_norm_meta] meta 저장: {meta_out} (chunks={n})")
