# core/utils/text_utils.py
from __future__ import annotations

import re
from typing import Any, Iterable, List


def clean_text(value: Any) -> str:
    """
    안전한 문자열 정리 함수.
    - None/NaN -> ""
    - 여러 공백/개행/탭 -> 단일 공백
    - 앞뒤 공백 제거
    """
    if value is None:
        return ""

    # pandas NaN 대응(na != na)
    try:
        if value != value:  # noqa: E711
            return ""
    except Exception:
        pass

    s = str(value)
    s = s.replace("\u00a0", " ").replace("\ufeff", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def join_nonempty(parts: Iterable[Any], sep: str = " | ") -> str:
    cleaned: List[str] = []
    for p in parts:
        t = clean_text(p)
        if t:
            cleaned.append(t)
    return sep.join(cleaned)


def truncate(text: Any, max_len: int = 500) -> str:
    s = clean_text(text)
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."
