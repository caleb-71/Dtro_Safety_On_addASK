# core/utils/time_utils.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from dateutil import parser


def to_iso8601(value: Any) -> str:
    """
    다양한 날짜/시간 입력을 ISO8601 문자열로 변환.
    실패 시 원문 문자열 반환(파이프라인 중단 방지)
    """
    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return ""

    try:
        dt = parser.parse(text)
        return dt.isoformat()
    except Exception:
        return text


def now_iso(timespec: str = "seconds") -> str:
    return datetime.now().isoformat(timespec=timespec)
