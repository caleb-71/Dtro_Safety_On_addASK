# backend/mappers/safety_audit_mapper.py
"""
안전심사/안전지도(safety_map 또는 safety_audit) 1행(row) -> 표준 incident_data 변환
"""

from __future__ import annotations

from typing import Any, Dict, List


def _pick(row: Dict[str, Any], candidates: List[str], default: str = "") -> str:
    for k in candidates:
        if k in row and row.get(k) not in [None, ""]:
            return str(row.get(k)).strip()
    return default


def map_audit_row_to_incident_data(row: Dict[str, Any]) -> Dict[str, Any]:
    report_id = _pick(row, ["관리번호", "지적번호", "id", "report_id"], default="")
    dt = _pick(row, ["점검일", "시행일자", "날짜", "발생일시", "incident_datetime"], default="")
    location = _pick(row, ["점검장소", "장소", "구간", "역", "location"], default="")
    severity = _pick(row, ["위험도", "중요도", "등급", "severity"], default="미정")
    reporter = _pick(row, ["점검자", "작성자", "부서", "reporter"], default="")

    summary = _pick(row, ["지적사항", "요약", "제목", "summary"], default="")
    timeline = _pick(row, ["경과", "추진경과", "timeline"], default="")
    actions_taken = _pick(row, ["조치", "개선조치", "actions_taken"], default="")
    current_status = _pick(row, ["상태", "조치현황", "진행상황", "current_status"], default="")

    return {
        "report_id": report_id,
        "incident_datetime": dt,
        "location": location,
        "category": "안전심사",
        "severity": severity,
        "reporter": reporter,
        "summary": summary,
        "timeline": timeline,
        "actions_taken": actions_taken,
        "current_status": current_status,
    }
