# core/utils/hash_utils.py
from __future__ import annotations

import hashlib
from typing import Any, Dict, List


def sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_stable_key(
    record: Dict[str, Any],
    stable_fields: List[str],
    *,
    sep: str = "|",
) -> str:
    """
    row_id용 안정키 생성: stable_fields 순서대로 값 추출 후 고정 규칙으로 join
    """
    parts: List[str] = []
    for f in stable_fields:
        v = record.get(f, "")
        if v is None:
            v = ""
        parts.append(str(v).strip())
    return sep.join(parts)


def generate_row_id(
    record: Dict[str, Any],
    *,
    dataset_prefix: str,
    source: str,
    dataset: str,
    stable_fields: List[str],
    hash_len: int = 12,
) -> str:
    """
    안정적인 row_id 생성:
    base = source|dataset|stable_key
    row_id = PREFIX + '-' + sha1(base)[:hash_len]
    """
    stable_key = make_stable_key(record, stable_fields)
    base = f"{str(source).strip()}|{str(dataset).strip()}|{stable_key}"
    hlen = max(8, int(hash_len))  # 너무 짧으면 충돌↑
    h = sha1_hex(base)[:hlen]
    return f"{str(dataset_prefix).upper().strip()}-{h}"
