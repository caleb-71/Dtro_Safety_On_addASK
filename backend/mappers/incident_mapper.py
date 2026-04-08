# backend/mappers/incident_mapper.py
"""
철도사고(incident.csv) 1행(row) -> 표준 incident_data 변환
"""

from __future__ import annotations

from typing import Any, Dict, List


def _pick(row: Dict[str, Any], candidates: List[str], default: str = "") -> str:
    for k in candidates:
        if k in row and row.get(k) not in [None, ""]:
            return str(row.get(k)).strip()
    return default


def map_incident_row_to_incident_data(row: Dict[str, Any]) -> Dict[str, Any]:
    report_id = _pick(row, ["관리번호", "사고번호", "id", "report_id"], default="")
    dt = _pick(row, ["발생일시", "일시", "날짜", "incident_datetime"], default="")
    location = _pick(row, ["장소", "구간", "역", "location"], default="")
    severity = _pick(row, ["영향도", "사고등급", "중요도", "severity"], default="미정")
    reporter = _pick(row, ["보고자", "작성자", "작성부서", "reporter"], default="")

    summary = _pick(row, ["사고개요", "개요", "요약", "제목", "summary"], default="")
    timeline = _pick(row, ["경과", "시간대별경과", "timeline"], default="")
    actions_taken = _pick(row, ["즉시조치", "초동조치", "조치내용", "actions_taken"], default="")
    current_status = _pick(row, ["현재상태", "조치현황", "진행상황", "current_status"], default="")

    return {
        "report_id": report_id,
        "incident_datetime": dt,
        "location": location,
        "category": "철도사고",
        "severity": severity,
        "reporter": reporter,
        "summary": summary,
        "timeline": timeline,
        "actions_taken": actions_taken,
        "current_status": current_status,
    }
