# backend/mappers/safety_map_mapper.py
"""
safety_map_norm.csv / safety_map.csv 1행(row) -> 표준 safety_map_data 변환

핵심 목표
- 표준 컬럼명 우선:
  checked_at, check_category, check_type, title, target_dept, action_type, issue_type,
  place_main, place_detail, action_completed_at, inspector, action_result, action_status, row_id
- 기존/한글 컬럼명 fallback:
  지도점검일자, 지도점검구분, 점검형태, 제목, 대상부서, 조치구분, 지적유형, 장소1, 장소2,
  조치완료일자, 점검자, 조치결과내용, 조치상태 등
- pandas.Timestamp/datetime/date -> 문자열(ISO) 변환
- ✅ 운영 안정화:
  - 날짜 문자열에서 "T00:00:00" / " 00:00:00" 제거
  - action_status/action_result 정책 1차 강제 적용
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterable, Optional


# -------------------------
# basic utils
# -------------------------
def _is_nan_like(v: Any) -> bool:
    """NaN 유사값 체크(판다스 없이도 동작)."""
    try:
        return v != v  # NaN
    except Exception:
        return False


def _to_iso_str(v: Any) -> str:
    """
    Timestamp / datetime / date -> ISO 문자열
    그 외는 문자열로 변환.
    """
    if v is None:
        return ""
    if _is_nan_like(v):
        return ""

    # pandas.Timestamp / NaT / NaN 안전 처리(판다스 없으면 pass)
    try:
        import pandas as pd  # local import
        if isinstance(v, pd.Timestamp):
            return v.isoformat()
        if pd.isna(v):
            return ""
    except Exception:
        pass

    if isinstance(v, (datetime, date)):
        return v.isoformat()

    s = str(v).strip()
    if s.lower() in ("none", "null", "nan", "nat"):
        return ""
    return s


def _clean_datetime_string(s: str) -> str:
    """
    날짜/일시 문자열 정리:
    - "YYYY-MM-DDT00:00:00" -> "YYYY-MM-DD"
    - "YYYY-MM-DD 00:00:00" -> "YYYY-MM-DD"
    """
    t = (s or "").strip()
    if not t:
        return ""
    t = t.replace("T00:00:00", "")
    t = t.replace(" 00:00:00", "")
    return t.strip()


def _s(v: Any, *, clean_dt: bool = False) -> str:
    """기본 문자열 정리(공백/None/nan 정리 + 선택적 날짜후처리)."""
    s = _to_iso_str(v).strip()
    if clean_dt:
        s = _clean_datetime_string(s)
    return s


def _first_non_empty(values: Iterable[str], default: str = "") -> str:
    for x in values:
        xx = (x or "").strip()
        if xx:
            return xx
    return default


def _get_any(row: Dict[str, Any], keys: Iterable[str], *, default: str = "", clean_dt: bool = False) -> str:
    """
    여러 키 중 첫 번째로 값이 있는 것을 반환.
    - clean_dt=True면 날짜/일시 문자열 후처리 적용
    """
    for k in keys:
        if k in row:
            v = _s(row.get(k), clean_dt=clean_dt)
            if v:
                return v
    return default


# -------------------------
# aliases (운영 데이터 변형 대비)
# -------------------------
ALIASES = {
    "row_id": ["row_id", "관리번호(row_id)", "관리번호", "id", "__row_id__"],
    "seq": ["seq", "순번", "번호"],

    "checked_at": ["checked_at", "점검일", "지도점검일자", "지도 점검일자", "점검일자"],
    "check_category": ["check_category", "점검구분", "지도점검구분", "지도 점검구분"],
    "check_type": ["check_type", "점검형태", "점검 형태"],

    "title": ["title", "제목", "지적사항", "지적 내용"],
    "target_dept": ["target_dept", "대상부서", "대상 부서", "소관부서"],
    "issue_type": ["issue_type", "지적유형", "지적 유형"],
    "action_type": ["action_type", "조치구분", "조치 구분"],

    "place_main": ["place_main", "장소1", "장소", "발생장소", "위치1"],
    "place_detail": ["place_detail", "장소2", "세부위치", "세부 위치", "위치2"],

    "inspector": ["inspector", "점검자", "지도점검자", "지도 점검자"],

    "action_completed_at": ["action_completed_at", "조치완료일", "조치완료일자", "조치 완료일자", "완료일"],
    "action_status": ["action_status", "조치상태", "조치 상태", "처리상태", "처리 상태"],
    "action_result": ["action_result", "조치결과내용", "조치 결과", "조치 결과내용", "결과내용"],
}


def _normalize_action_fields(*, action_status: str, action_completed_at: str, action_result: str) -> Dict[str, str]:
    """
    ✅ 조치상태/조치결과 정책 1차 강제(운영 안정화)
    - action_status가 비면 completed_at 기반 추정
    - action_result 빈칸이면 정책 적용
      - 미조치 & 빈칸 -> "미조치"
      - 완료 & 빈칸 -> "완료(내용 미기재)"
    """
    st = (action_status or "").strip()
    done_at = (action_completed_at or "").strip()
    rs = (action_result or "").strip()

    # status 추정
    if not st:
        st = "완료" if done_at else "미조치"

    # result 정책
    if not rs or rs in ("미기재",):
        if st == "미조치":
            rs = "미조치"
        elif st == "완료":
            rs = "완료(내용 미기재)"
        else:
            # 그 외 상태면 중립적으로
            rs = "미기재"

    return {"action_status": st, "action_result": rs}


def map_safety_map_row_to_safety_map_data(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    safety_map 1행 -> 표준 safety_map_data(dict)
    - 어떤 페이지/서비스로 넘겨도 JSON 직렬화 안전(문자열 기반)
    """
    src = row or {}

    row_id = _get_any(src, ALIASES["row_id"], default="")
    seq = _get_any(src, ALIASES["seq"], default="")

    checked_at = _get_any(src, ALIASES["checked_at"], default="", clean_dt=True)
    check_category = _get_any(src, ALIASES["check_category"], default="")
    check_type = _get_any(src, ALIASES["check_type"], default="")

    title = _get_any(src, ALIASES["title"], default="")
    target_dept = _get_any(src, ALIASES["target_dept"], default="")
    action_type = _get_any(src, ALIASES["action_type"], default="")
    issue_type = _get_any(src, ALIASES["issue_type"], default="")

    place_main = _get_any(src, ALIASES["place_main"], default="")
    place_detail = _get_any(src, ALIASES["place_detail"], default="")

    action_completed_at = _get_any(src, ALIASES["action_completed_at"], default="", clean_dt=True)
    inspector = _get_any(src, ALIASES["inspector"], default="")

    action_status_raw = _get_any(src, ALIASES["action_status"], default="")
    action_result_raw = _get_any(src, ALIASES["action_result"], default="")

    # ✅ 조치 정책 1차 강제
    normalized = _normalize_action_fields(
        action_status=action_status_raw,
        action_completed_at=action_completed_at,
        action_result=action_result_raw,
    )
    action_status = normalized["action_status"]
    action_result = normalized["action_result"]

    # ✅ row_id가 비어있으면 seq 기반으로라도 식별 가능하게(선택)
    # - 기존에 이미 SMAP-xxxx 형태를 쓰고 있다면 그대로 유지됨
    if not row_id and seq:
        row_id = f"SMAP-{seq}"

    # extra: 원본 최대 보존 + Timestamp 등 문자열로 변환
    extra: Dict[str, Any] = {}
    for k, v in src.items():
        # 날짜 문자열도 extra에는 정리해두면 조회 시 편함
        extra[str(k)] = _clean_datetime_string(_to_iso_str(v))

    return {
        "row_id": row_id or "",
        "seq": seq or "",
        "checked_at": checked_at or "",
        "check_category": check_category,
        "check_type": check_type,
        "title": title,
        "target_dept": target_dept,
        "action_type": action_type,
        "issue_type": issue_type,
        "place_main": place_main,
        "place_detail": place_detail,
        "inspector": inspector,
        "action_status": action_status,            # "완료"/"미조치" 등
        "action_completed_at": action_completed_at,
        "action_result": action_result,            # 정책 적용된 값
        "extra": extra,
    }


def build_safety_map_record_text(smap: Dict[str, Any]) -> str:
    """
    표준 safety_map_data를 LLM에게 주기 좋은 텍스트로 변환.
    """
    def _pretty_date(x: Any) -> str:
        s = str(x or "").strip()
        if not s:
            return ""
        s = _clean_datetime_string(s)
        # ISO에 T가 남아있으면 날짜만
        if "T" in s:
            s = s.split("T", 1)[0].strip()
        return s

    keys = [
        ("row_id", "row_id"),
        ("checked_at", "점검일"),
        ("check_category", "점검구분"),
        ("check_type", "점검형태"),
        ("title", "제목"),
        ("target_dept", "대상부서"),
        ("action_type", "조치구분"),
        ("issue_type", "지적유형"),
        ("place_main", "장소1"),
        ("place_detail", "장소2"),
        ("action_completed_at", "조치완료일"),
        ("inspector", "점검자"),
        ("action_result", "조치결과내용"),
        ("action_status", "조치상태"),
    ]

    lines = ["[점검기록 1건 요약]"]
    for k, label in keys:
        v = (smap or {}).get(k, "")
        if k in ("checked_at", "action_completed_at"):
            vv = _pretty_date(v)
        else:
            vv = str(v or "").strip()

        lines.append(f"- {label}: {vv}")

    return "\n".join(lines).strip()
