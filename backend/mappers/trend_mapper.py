# backend/mappers/trend_mapper.py
"""
동향보고(trend_norm.csv / trend.csv) 1행(row) -> 표준 incident_data 변환 (report_base.json v1.1-trend 일치)

핵심:
- norm 컬럼명(occurred_at, occurred_time, line, station, place_main, incident_type, summary, actions_taken 등) 우선
- 기존 csv 컬럼명(일자, 시간, 호선, 역명, 장소, 사고유형, 사고개황, 조치 등) fallback
"""

from __future__ import annotations
from typing import Any, Dict


def _s(v: Any) -> str:
    if v is None:
        return ""
    t = str(v).strip()
    if t.lower() in ("none", "null", "nan"):
        return ""
    return t


def _get_any(row: Dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        if k in row:
            v = _s(row.get(k))
            if v:
                return v
    return default


def _build_datetime(row: Dict[str, Any]) -> str:
    # norm 우선
    d = _get_any(row, "occurred_at", "발생일자", "일자")
    t = _get_any(row, "occurred_time", "발생시간", "시간")

    # occurred_at이 2026-01-26T00:00:00 형태면 날짜만
    if d:
        if "T" in d:
            d = d.split("T", 1)[0].strip()
        if " 00:00:00" in d:
            d = d.replace(" 00:00:00", "").strip()

    if d and t:
        return f"{d} {t}"
    return d or _get_any(row, "작성일시", "수정일시", default="")


def _build_detail_location(row: Dict[str, Any]) -> str:
    # norm 우선: place_main/detail_1/detail_2
    base = _get_any(row, "place_main", "발생장소", "장소")
    d1 = _get_any(row, "place_detail_1", "세부1")
    d2 = _get_any(row, "place_detail_2", "세부2")
    d3 = _get_any(row, "세부3")
    # 설비 상세(있으면)
    extra = _get_any(row, "시설승강기번호", "승강기번호", "에스컬레이터번호", default="")

    parts = [p for p in [base, d1, d2, d3] if p]
    if extra:
        parts.append(extra)
    return " / ".join(parts)


def _guess_severity(row: Dict[str, Any]) -> str:
    itype = _get_any(row, "incident_type", "사고유형")
    injury = _get_any(row, "부상종류")
    emergency = _get_any(row, "긴급신고")

    if emergency in ["119", "긴급", "예", "유"]:
        return "중"
    if injury and injury not in ["해당없음"]:
        return "중"
    if itype in ["갇힘", "응급환자"]:
        return "중"
    return "미정"


def map_trend_row_to_incident_data(row: Dict[str, Any]) -> Dict[str, Any]:
    report_id = _get_any(row, "순번", "관리번호", default="")
    category = _get_any(row, "report_type", "보고구분", default="동향보고")

    line = _get_any(row, "line", "호선")
    station = _get_any(row, "station", "역명")
    detail_location = _build_detail_location(row)

    accident_type = _get_any(row, "incident_type", "사고유형")
    related_train = _get_any(row, "관계열차")
    cctv = _get_any(row, "cctv", "CCTV유무", "CCTV 유무", "CCTV\n유무")
    weather = _get_any(row, "날씨", "weather")
    severity = _guess_severity(row)
    reporter = _get_any(row, "출동소속(명)", "출동/보고 소속", default="")

    summary = _get_any(row, "summary", "사고개황", default="")
    timeline = _get_any(row, "초동대처", default="")
    actions_taken = _get_any(row, "actions_taken", "조치", default="")
    current_status = _get_any(row, "조치상태", default="미상")

    extra = dict(row)  # 원본 최대 보존(추후 분석에 유리)

    return {
        "report_id": report_id,
        "incident_datetime": _build_datetime(row),
        "category": category,
        "line": line,
        "station": station,
        "detail_location": detail_location,
        "accident_type": accident_type,
        "related_train": related_train,
        "cctv": cctv,
        "weather": weather,
        "severity": severity,
        "reporter": reporter,
        "summary": summary,
        "timeline": timeline,
        "actions_taken": actions_taken,
        "current_status": current_status,
        "extra": extra,
    }
