# backend/services/report_service.py
"""
Report Service (DTRO-Safety-On)

개선 포인트(안정화 + 품질 개선)
- LLM 출력 list/dict가 PDF에 그대로 찍히는 문제 해결 (pretty text 변환)
- 유사사고(trend_norm.csv) 기반 Similar Context 생성/주입 지원
- RAG 근거(reg_context) + 유사사고 패턴(similar_context)을 함께 넣어
  원인분석/재발방지의 구체성을 강화

추가 안정화(리팩토링)
- prompt 로드/렌더/가드레일/LLM호출/파싱/디버그저장 공통화
- report_incident_v1.md에 {similar_context} 변수가 누락되어도, prompt에 강제로 주입
- root_cause / prevention이 "빈약한" 경우(불릿 너무 적음) 자동 보강
- ✅ Timestamp/datetime/NaN 등이 섞여도 json.dumps에서 절대 죽지 않도록 sanitize

✅ 버그 수정(중요)
- safety_map PDF에서 "2. 점검 개요", "3. 조치 결과"가 비어 있는 문제:
  -> normalize 이후, 데이터 기반 자동 보강(fixup) 추가
  -> action_result 정책 강제(미조치/완료)
  -> 날짜 문자열 T00:00:00 / 00:00:00 전역 제거

✅ 가독성 개선(요청 반영)
- 보고서 문장/불릿 끝에 반복되던 "(근거: ...)" 태그 제거
  -> evidence_tag 파라미터는 시그니처 호환을 위해 유지하되 본문에는 부착하지 않음
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

import pandas as pd

from core.config import get_settings
from core.logger import get_logger
from backend.integrations.ollama_client import OllamaClient
from rag.prompting.renderer import render_template
from report.renderers.pdf_reportlab import render_report_pdf
from backend.mappers.safety_map_mapper import map_safety_map_row_to_safety_map_data

logger = get_logger(__name__)

__all__ = [
    "ReportService",
    "ReportResult",
    "build_similar_context_from_trend_df",
    "build_similar_context_from_smap_df",
]


# =========================
# Small utilities
# =========================
def _strip_zero_time(s: str) -> str:
    """날짜 문자열의 00:00:00 패턴 제거(표준화)."""
    t = (s or "").strip()
    if not t:
        return ""
    t = t.replace("T00:00:00", "")
    t = t.replace(" 00:00:00", "")
    return t.strip()


def _inject_analysis_section(report_json: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """02에서 만든 KPI/주간/피벗/예측 문구를 report_json.analysis에 강제 반영."""
    if not analysis:
        return report_json

    sec = report_json.get("analysis")
    if not isinstance(sec, dict):
        report_json["analysis"] = {}
        sec = report_json["analysis"]

    for k, v in (analysis or {}).items():
        sec[k] = v
    return report_json


# =========================
# Prompt loader
# =========================
def _load_text(path: Path, *, label: str) -> str:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path.read_text(encoding="utf-8")


def _load_report_prompt_incident() -> str:
    settings = get_settings()
    path = settings.paths.base_dir / "report" / "prompts" / "report_incident_v1.md"
    return _load_text(path, label="incident report prompt")


def _load_report_prompt_safety_map() -> str:
    settings = get_settings()
    path = settings.paths.base_dir / "report" / "prompts" / "report_safety_map_v1.md"
    return _load_text(path, label="safety_map report prompt")


# =========================
# Template loader
# =========================
def _load_report_template(template_path: Path) -> Dict[str, Any]:
    if not template_path.exists():
        raise FileNotFoundError(f"report template not found: {template_path}")
    return json.loads(template_path.read_text(encoding="utf-8"))


# =========================
# JSON extractor + parser
# =========================
def _extract_json_text(raw: str) -> str:
    if raw is None:
        raise ValueError("LLM raw output is None")

    s = raw.strip()
    if not s:
        raise ValueError("LLM returned empty text.")

    # fenced code block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # naive brace slicing
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start: end + 1].strip()

    raise ValueError("Could not locate JSON object in LLM output.")


def _parse_llm_json(raw: str) -> Dict[str, Any]:
    json_text = _extract_json_text(raw)
    obj = json.loads(json_text)
    if not isinstance(obj, dict):
        raise ValueError("Parsed JSON is not a dict object.")
    return obj


# =========================
# Guardrails
# =========================
def _json_only_guardrail() -> str:
    return (
        "\n\n"
        "=== 출력 규칙(중요) ===\n"
        "1) 반드시 JSON만 출력하세요. 설명/해설/주석/마크다운/코드블록 금지.\n"
        "2) 출력의 첫 글자는 '{' 이어야 하고, 마지막 글자는 '}' 이어야 합니다.\n"
        "3) JSON 외의 어떤 문자도 앞뒤에 붙이지 마세요.\n"
        "=====================\n"
    )


def _reasoning_required_guardrail() -> str:
    return (
        "\n"
        "=== 필수 기재(중요) ===\n"
        "아래 항목은 빈 문자열로 두지 마세요.\n"
        "- root_cause.facts_only\n"
        "- root_cause.assumptions_only\n"
        "- prevention.prevention_plan\n"
        "근거가 부족하면 '근거 부족'을 명시하고, 추가 확인 항목을 먼저 제시한 뒤 일반 예방대책을 작성하세요.\n"
        "=====================\n"
    )


def _clip_text(s: str, max_chars: int) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if len(t) <= max_chars:
        return t
    return t[:max_chars] + "\n...(truncated)"


# =========================
# JSON sanitize (Timestamp 방지 핵심)
# =========================
def _json_sanitize(obj: Any) -> Any:
    """json.dumps에서 깨지는 타입(pandas.Timestamp 등)을 안전한 기본 타입으로 변환."""
    if isinstance(obj, dict):
        return {str(k): _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_sanitize(v) for v in obj]

    try:
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        try:
            if pd.isna(obj):
                return None
        except Exception:
            pass
    except Exception:
        pass

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()

    return obj


# =========================
# similar_context 강제 주입
# =========================
def _ensure_prompt_has_similar_context(prompt_text: str, similar_context: str) -> str:
    p = prompt_text or ""
    sc = (similar_context or "").strip()
    if not sc:
        return p

    if "{similar_context}" in p:
        return p

    marker_patterns = [
        "[유사사고 분석 요약(있으면)]",
        "=== 유사사고 분석 요약",
        "유사사고 분석 요약",
        "PAST_SIMILAR_SUMMARY",
    ]
    if any(m in p for m in marker_patterns):
        return p

    return p + "\n\n[유사사고 분석 요약(있으면)]\n" + sc + "\n"


# =========================
# Pretty text helpers
# =========================
def _is_nan(v: Any) -> bool:
    try:
        return v != v
    except Exception:
        return False


def _to_pretty_text(v: Any) -> str:
    """
    PDF/JSON 출력용 문자열 정리(전역 규칙)
    - None/NaN 방지
    - list/dict를 bullet/kv bullet로 안전 변환
    - ✅ 날짜 문자열 전역 후처리: T00:00:00 / 00:00:00 제거
    """
    if v is None:
        return ""
    if _is_nan(v):
        return ""

    if isinstance(v, str):
        s = v.strip()
        if not s or s.lower() in ("none", "null", "nan"):
            return ""
        return _strip_zero_time(s)

    if isinstance(v, list):
        items = []
        for x in v:
            s = _to_pretty_text(x)
            if s:
                items.append(s)
        if not items:
            return ""
        out = []
        for s in items:
            out.append(s if s.startswith("- ") else f"- {s}")
        return "\n".join(out)

    if isinstance(v, dict):
        lines = []
        for k, val in v.items():
            sv = _to_pretty_text(val)
            if not sv:
                continue
            kk = str(k)
            if "\n" in sv:
                lines.append(f"- {kk}:\n{sv}")
            else:
                lines.append(f"- {kk}: {sv}")
        return "\n".join(lines) if lines else ""

    s = str(v).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return ""
    return _strip_zero_time(s)


def _coalesce_text(*vals: Any, default: str = "미기재") -> str:
    for v in vals:
        s = _to_pretty_text(v)
        if s:
            return s
    return default


def _count_bullets(text: str) -> int:
    return sum(1 for line in (text or "").splitlines() if line.strip().startswith("-"))


# =========================
# Normalize helpers
# =========================
def _unwrap_common_wrappers(d: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    for k in ("report", "data", "result", "output"):
        v = d.get(k)
        if isinstance(v, dict) and v:
            return v
    return d


def _get_from_aliases(src: Dict[str, Any], keys: List[str]) -> Any:
    for k in keys:
        if k in src:
            return src.get(k)
    return None


# =========================
# Template 폴백 매핑 (incident + safety_map 통합)
# =========================
def _fallback_for_field(field_key: str, data: Dict[str, Any]) -> str:
    d = data or {}

    common = {
        # ---- incident(trend) ----
        "report_id": ["report_id", "row_id", "순번", "id", "__row_id__", "관리번호", "관리번호(순번)"],
        "incident_datetime": [
            "incident_datetime", "발생일시", "일시", "datetime",
            "date_time", "occurred_at", "occurred_time", "일자", "시간"
        ],
        "category": ["category", "report_type", "보고구분", "보고 구분", "구분"],
        "line": ["line", "호선"],
        "station": ["station", "역명"],
        "detail_location": [
            "detail_location", "세부 위치", "세부위치", "장소", "발생장소",
            "place_main", "place_detail_1", "place_detail_2", "세부1", "세부2", "세부3",
        ],
        "accident_type": ["accident_type", "incident_type", "사고유형"],
        "related_train": ["related_train", "관계열차", "관계 열차"],
        "cctv": ["cctv", "CCTV유무", "CCTV 유무", "CCTV", "CCTV여부"],
        "weather": ["weather", "날씨", "기상", "기상상태"],
        "severity": ["severity", "영향도", "영향도(추정)", "심각도"],
        "reporter": ["reporter", "출동/보고 소속", "출동소속(명)", "보고자", "작성자"],
        "summary": ["summary", "사고개황", "개황", "요약"],
        "timeline": ["timeline", "초동대처", "경과", "초동대처 및 시간대별 경과"],
        "actions_taken": ["actions_taken", "조치", "조치 내용", "조치내용"],
        "current_status": ["current_status", "조치상태", "상태"],
        "facts_only": ["facts_only", "FACT", "사실", "확인된 사실", "확인된사실"],
        "assumptions_only": ["assumptions_only", "ASSUMPTION", "추정", "판단", "추정 및 판단"],
        "prevention_plan": ["prevention_plan", "재발방지", "재발방지대책", "개선의견"],

        # ---- safety_map ----
        "row_id": ["row_id", "관리번호(row_id)", "관리번호", "순번"],
        "checked_at": ["checked_at", "점검일", "지도점검일자", "점검일자"],
        "check_category": ["check_category", "점검구분", "지도점검구분"],
        "check_type": ["check_type", "점검형태"],
        "title": ["title", "제목", "지적사항", "지적 내용"],
        "target_dept": ["target_dept", "대상부서", "대상 부서"],
        "issue_type": ["issue_type", "지적유형", "지적 유형"],
        "action_type": ["action_type", "조치구분", "조치 구분"],
        "place_main": ["place_main", "장소1", "장소", "발생장소"],
        "place_detail": ["place_detail", "장소2", "세부위치", "세부 위치"],
        "inspector": ["inspector", "점검자", "지도점검자"],
        "action_status": ["action_status", "조치상태", "조치 상태", "처리상태", "처리 상태"],
        "action_completed_at": ["action_completed_at", "조치완료일", "조치완료일자", "조치완료일자"],
        "action_result": ["action_result", "조치결과내용", "조치 결과", "조치 결과내용"],
        "summary_smap": ["summary", "요약", "점검개요", "개요"],
    }

    if field_key not in common:
        return "미기재"

    v = _get_from_aliases(d, common[field_key])

    # incident_datetime: occurred_at + occurred_time
    if field_key == "incident_datetime":
        if _to_pretty_text(v):
            return _coalesce_text(v, default="미기재")
        date_v = _get_from_aliases(d, ["occurred_at", "일자", "발생일자"])
        time_v = _get_from_aliases(d, ["occurred_time", "시간", "발생시간"])
        ds = _strip_zero_time(_to_pretty_text(date_v))
        ts = _to_pretty_text(time_v)
        if ds and ts:
            return f"{ds} {ts}"
        if ds:
            return ds

    return _coalesce_text(v, default="미기재")


def _normalize_report_json(
    *,
    template: Dict[str, Any],
    llm_json: Dict[str, Any],
    incident_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    template.sections 기준으로 LLM JSON을 정규화.
    - field가 비어있으면 root/incident_data에서 fallback
    - 결과는 {section_key: {field_key: str}} 형태로 고정
    """
    out: Dict[str, Any] = {}

    root = _unwrap_common_wrappers(llm_json or {})
    if not isinstance(root, dict):
        root = {}

    sections = template.get("sections", [])
    if not isinstance(sections, list) or not sections:
        logger.warning("[ReportService] template has no sections. return llm_json as-is.")
        return root

    for sec in sections:
        sec_key = sec.get("key")
        if not sec_key:
            continue

        llm_sec = root.get(sec_key, {})
        if not isinstance(llm_sec, dict):
            llm_sec = {}

        fields = sec.get("fields", [])
        sec_out: Dict[str, Any] = {}

        for f in fields:
            k = f.get("key")
            if not k:
                continue

            v = llm_sec.get(k)

            if not _to_pretty_text(v):
                v = root.get(k)

            if not _to_pretty_text(v):
                v = _fallback_for_field(k, incident_data)

            sec_out[k] = _coalesce_text(v, default="미기재")

        out[sec_key] = sec_out

    return out


# =========================
# ✅ safety_map fixup helpers (A/B/C 해결 핵심)
# =========================
def _fixup_safety_map_overview(report_json: Dict[str, Any], smap_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    2. 점검 개요(overview.summary)가 빈칸/미기재일 때,
    safety_map_data 기반으로 2~6줄 요약을 강제 생성.
    """
    if not isinstance(report_json, dict):
        return report_json

    overview = report_json.get("overview")
    if not isinstance(overview, dict):
        report_json["overview"] = {}
        overview = report_json["overview"]

    cur = _to_pretty_text(overview.get("summary"))
    if cur and cur != "미기재":
        return report_json

    checked_at = _coalesce_text(smap_data.get("checked_at"), default="")
    check_category = _coalesce_text(smap_data.get("check_category"), default="")
    check_type = _coalesce_text(smap_data.get("check_type"), default="")
    dept = _coalesce_text(smap_data.get("target_dept"), default="")
    issue = _coalesce_text(smap_data.get("issue_type"), default="")
    action_type = _coalesce_text(smap_data.get("action_type"), default="")
    place = _coalesce_text(smap_data.get("place_main"), smap_data.get("place_detail"), default="")
    title = _coalesce_text(smap_data.get("title"), default="")
    inspector = _coalesce_text(smap_data.get("inspector"), default="")

    lines: List[str] = []
    if checked_at or check_category or check_type:
        a = " / ".join([x for x in [checked_at, check_category, check_type] if x])
        lines.append(f"{a} 기준으로 점검을 수행했습니다.")
    if place:
        lines.append(f"점검 장소는 '{place}'입니다.")
    if dept:
        lines.append(f"대상 부서는 '{dept}'입니다.")
    if issue:
        lines.append(f"주요 지적유형은 '{issue}'입니다.")
    if title:
        lines.append(f"지적/제목 요약: {title}")
    if action_type:
        lines.append(f"조치구분: {action_type}")
    if inspector:
        lines.append(f"점검자: {inspector}")

    if len(lines) < 2:
        lines = [
            "점검 개요 정보가 충분하지 않아 원자료(점검표/사진/이력)를 추가 확인할 필요가 있습니다.",
            "가능한 범위에서 지적유형/대상부서/장소/조치현황을 재정리하여 보고서에 반영해야 합니다.",
        ]

    overview["summary"] = "\n".join(lines[:6]).strip()
    report_json["overview"] = overview
    return report_json


def _fixup_safety_map_actions(report_json: Dict[str, Any], smap_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    3. 조치 결과(actions.action_result) 빈칸 문제 및 정책 강제 적용.
    - action_status == "미조치" AND action_result empty -> "미조치"
    - action_status == "완료" AND action_result empty -> "완료(내용 미기재)"
    """
    if not isinstance(report_json, dict):
        return report_json

    basic = report_json.get("basic_info")
    if not isinstance(basic, dict):
        report_json["basic_info"] = {}
        basic = report_json["basic_info"]

    actions = report_json.get("actions")
    if not isinstance(actions, dict):
        report_json["actions"] = {}
        actions = report_json["actions"]

    status = _coalesce_text(basic.get("action_status"), smap_data.get("action_status"), default="")
    completed_at = _coalesce_text(basic.get("action_completed_at"), smap_data.get("action_completed_at"), default="")

    if not status:
        status = "완료" if completed_at and completed_at != "미기재" else "미조치"

    result = _to_pretty_text(actions.get("action_result"))
    if not result or result == "미기재":
        result = _to_pretty_text(smap_data.get("action_result"))

    if not result or result == "미기재":
        if status == "미조치":
            result = "미조치"
        elif status == "완료":
            result = "완료(내용 미기재)"
        else:
            result = "미기재"

    basic["action_status"] = _coalesce_text(status, default="미기재")
    if basic.get("action_status") == "완료":
        if not _to_pretty_text(basic.get("action_completed_at")) or basic.get("action_completed_at") == "미기재":
            basic["action_completed_at"] = _coalesce_text(smap_data.get("action_completed_at"), default="미기재")

    actions["action_result"] = _coalesce_text(result, default="미기재")

    report_json["basic_info"] = basic
    report_json["actions"] = actions
    return report_json


# =========================
# 최소 품질 보강 (generic: incident/safety_map 공용)
# =========================
def _ensure_reasoning_fields_generic(
    report_json: Dict[str, Any],
    data: Dict[str, Any],
    *,
    evidence_tag: str,
) -> Dict[str, Any]:
    """
    root_cause / prevention이 비었거나 불릿이 너무 적으면, 안전한 기본 텍스트로 보강.
    - evidence_tag는 시그니처 호환용(본문에는 미부착)
    """
    root = report_json.get("root_cause") or {}
    prev = report_json.get("prevention") or {}

    facts = _to_pretty_text(root.get("facts_only"))
    asmp = _to_pretty_text(root.get("assumptions_only"))
    plan = _to_pretty_text(prev.get("prevention_plan"))

    def _bullets(items: List[str]) -> str:
        items = [x.strip() for x in items if x and x.strip()]
        return "\n".join([x if x.startswith("- ") else f"- {x}" for x in items]) if items else "미기재"

    # ---- facts_only ----
    if (not facts) or facts == "미기재" or _count_bullets(facts) < 2:
        facts_list: List[str] = []
        title = _coalesce_text(data.get("title"), default="")
        issue = _coalesce_text(data.get("issue_type"), default="")
        dept = _coalesce_text(data.get("target_dept"), default="")
        place = _coalesce_text(data.get("place_main"), data.get("place_detail"), default="")
        checked = _coalesce_text(data.get("checked_at"), default="")

        # incident 쪽도 같이 커버 (있으면 더 풍부해짐)
        inc_dt = _coalesce_text(data.get("incident_datetime"), data.get("occurred_at"), default="")
        station = _coalesce_text(data.get("station"), default="")
        line = _coalesce_text(data.get("line"), default="")
        acc_type = _coalesce_text(data.get("accident_type"), data.get("incident_type"), default="")

        if inc_dt and inc_dt != "미기재":
            facts_list.append(f"발생/점검일시: {inc_dt}")
        elif checked:
            facts_list.append(f"점검일: {checked}")

        if line:
            facts_list.append(f"호선: {line}")
        if station:
            facts_list.append(f"역: {station}")
        if dept:
            facts_list.append(f"대상부서: {dept}")
        if issue:
            facts_list.append(f"지적유형: {issue}")
        if acc_type:
            facts_list.append(f"사고유형: {acc_type}")
        if place:
            facts_list.append(f"장소: {place}")
        if title:
            facts_list.append(f"제목/지적내용 요약: {title}")

        if not facts_list:
            facts_list = [
                "확인 가능한 사실이 부족합니다. 원자료(점검표/사진/근무일지/조치이력)를 추가 확인할 필요가 있습니다."
            ]

        root["facts_only"] = _bullets(facts_list)

    # ---- assumptions_only ----
    if (not asmp) or asmp == "미기재" or _count_bullets(asmp) < 2:
        guesses = [
            "원인 규명을 위해 현장 사진/점검표 원본/조치 이력(시정조치 증빙)을 추가 확인할 필요가 있습니다.",
            "동일 유형이 반복되는 경우(부서/유형/장소), 점검 주기 또는 관리 절차에 취약점이 있을 가능성이 있습니다(단정 금지).",
        ]
        root["assumptions_only"] = _bullets(guesses)

    # ---- prevention_plan ----
    if (not plan) or plan == "미기재" or _count_bullets(plan) < 4:
        plan_list = [
            "추가 확인 필요 항목: 조치 미완료 사유, 시정조치 증빙(사진/서류) 유무, 재발 여부",
            "현장 즉시 시정(위험요인 제거/표지/정리정돈) 및 점검기록 표준화(체크리스트) 적용",
            "취약 조합(부서×유형×장소) 중심으로 집중점검/관리자 확인(결재/서명) 강화",
            "조치 완료 기준(완료 판정 요건) 명확화 및 미완료 건에 대한 기한/재점검 루프 도입",
            "현장 교육/캠페인(반복 지적/사고유형 중심) 및 월간 피드백 공유",
        ]
        prev["prevention_plan"] = _bullets(plan_list)

    report_json["root_cause"] = root
    report_json["prevention"] = prev
    return report_json


def _safe_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")


def _safe_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_obj = _json_sanitize(obj)
    path.write_text(json.dumps(safe_obj, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================
# 공통: LLM 실행 + 파싱 + 디버그 저장
# =========================
def _run_llm_and_parse_json(
    *,
    client: OllamaClient,
    prompt: str,
    debug_dir: Path,
    debug_prefix: str,
    num_predict: int,
    temperature: float = 0.2,
    top_p: float = 0.9,
    save_debug_files: bool = True,
) -> Tuple[Dict[str, Any], str, bool, str]:
    """
    공통 LLM 실행기
    return: (llm_json, raw_text, parse_ok, ts)
    """
    gen = client.generate(
        prompt=prompt,
        temperature=float(temperature),
        top_p=float(top_p),
        num_predict=int(num_predict),
    )
    raw = (gen.text or "").strip()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_dir.mkdir(parents=True, exist_ok=True)

    llm_json: Dict[str, Any] = {}
    parse_ok = True
    try:
        llm_json = _parse_llm_json(raw)
    except Exception as e:
        parse_ok = False
        logger.error(f"[ReportService] {debug_prefix} LLM JSON parse failed. raw_len={len(raw)} err={e}")
        if save_debug_files:
            _safe_write_text(debug_dir / f"{debug_prefix}_last_llm_output_{ts}.txt", raw)
            _safe_write_text(debug_dir / f"{debug_prefix}_last_prompt_{ts}.txt", prompt)

    if save_debug_files and parse_ok:
        _safe_write_json(debug_dir / f"{debug_prefix}_last_llm_json_{ts}.json", llm_json)
        _safe_write_text(debug_dir / f"{debug_prefix}_last_prompt_{ts}.txt", prompt)

    return llm_json, raw, parse_ok, ts


# =========================
# Similar 사고 분석 컨텍스트 생성 (trend_norm 기반)
# =========================
def build_similar_context_from_trend_df(
    trend_df: pd.DataFrame,
    incident_data: Dict[str, Any],
    *,
    months: Optional[int] = 12,
    max_cases: int = 20,
) -> str:
    if trend_df is None or trend_df.empty:
        return ""

    itype = _coalesce_text(incident_data.get("accident_type"), incident_data.get("incident_type"), default="")
    place = _coalesce_text(incident_data.get("detail_location"), incident_data.get("place_main"), default="")
    station = _coalesce_text(incident_data.get("station"), default="")
    line = _coalesce_text(incident_data.get("line"), default="")
    cause = _coalesce_text(incident_data.get("cause"), default="")

    target_row_id = _coalesce_text(incident_data.get("report_id"), incident_data.get("row_id"), default="").strip()

    df = trend_df.copy()

    if "occurred_at" in df.columns:
        df["occurred_at_dt"] = pd.to_datetime(df["occurred_at"], errors="coerce")
    else:
        df["occurred_at_dt"] = pd.NaT

    if target_row_id and "row_id" in df.columns:
        df = df[df["row_id"].astype(str) != target_row_id]

    if months and df["occurred_at_dt"].notna().any():
        cutoff = pd.Timestamp.now() - pd.DateOffset(months=int(months))
        df = df[df["occurred_at_dt"].isna() | (df["occurred_at_dt"] >= cutoff)]

    if df.empty:
        return ""

    def _itype_family_keys(t: str) -> List[str]:
        tt = (t or "").strip()
        if not tt:
            return []
        if any(k in tt for k in ["넘어", "전도", "낙상", "미끄"]):
            return ["넘어", "전도", "낙상", "미끄"]
        return [tt]

    itype_keys = _itype_family_keys(itype)

    mask = pd.Series(True, index=df.index)

    if itype_keys and "incident_type" in df.columns:
        m_itype = pd.Series(False, index=df.index)
        for k in itype_keys:
            m_itype |= df["incident_type"].astype(str).str.contains(k, na=False)
        mask &= m_itype

    place_key = ""
    if place:
        place_key = place.split("/")[0].strip() if "/" in place else place.strip()

    if place_key:
        m_place = pd.Series(False, index=df.index)
        for col in ["place_main", "place_detail_1", "place_detail_2", "발생장소"]:
            if col in df.columns:
                m_place |= df[col].astype(str).str.contains(place_key, na=False)
        mask &= m_place

    cand = df[mask].copy()
    if cand.empty:
        return ""

    if station and "station" in cand.columns and len(cand) > 200:
        same_station = cand[cand["station"].astype(str) == station]
        if len(same_station) >= 20:
            cand = same_station

    score = pd.Series(0, index=cand.index, dtype=int)
    if station and "station" in cand.columns:
        score += (cand["station"].astype(str) == station).astype(int) * 2
    if line and "line" in cand.columns:
        score += (cand["line"].astype(str) == line).astype(int) * 1
    if cause and "cause" in cand.columns:
        score += cand["cause"].astype(str).str.contains(cause, na=False).astype(int) * 1

    cand["__sim_score__"] = score
    cand = cand.sort_values(["__sim_score__", "occurred_at_dt"], ascending=[False, False])

    total_cnt = len(cand)
    top = cand.head(int(max_cases))

    def _top_counts(col: str, n: int = 5) -> str:
        if col not in cand.columns:
            return ""
        vc = cand[col].astype(str).replace("nan", "").replace("None", "").value_counts()
        vc = vc[vc.index.astype(str).str.len() > 0].head(n)
        if vc.empty:
            return ""
        return ", ".join([f"{idx}({cnt})" for idx, cnt in vc.items()])

    summary_lines = []
    summary_lines.append(
        f"- 유사사고 기준: 사고유형='{itype or '미기재'}' / 장소키='{place_key or '미기재'}' / "
        f"역='{station or '미기재'}' / 호선='{line or '미기재'}'"
    )
    summary_lines.append(
        f"- 유사사고 건수: {total_cnt}건 (최근 {months}개월 기준)" if months else f"- 유사사고 건수: {total_cnt}건"
    )

    s_station = _top_counts("station", 5)
    s_place = _top_counts("place_main", 5)
    s_cause = _top_counts("cause", 5)

    if s_place:
        summary_lines.append(f"- 장소 Top: {s_place}")
    if s_station:
        summary_lines.append(f"- 역 Top: {s_station}")
    if s_cause:
        summary_lines.append(f"- 원인 Top: {s_cause}")

    case_lines = []
    for _, r in top.iterrows():
        rid = str(r.get("row_id", "")) if "row_id" in r else ""
        d = _strip_zero_time(str(r.get("occurred_at", ""))).split("T")[0]
        stn = str(r.get("station", ""))
        plc = str(r.get("place_main", "")) if "place_main" in r else str(r.get("발생장소", ""))
        smy = str(r.get("summary", ""))
        if len(smy) > 80:
            smy = smy[:80] + "…"
        case_lines.append(f"- [{rid}] {d} | {stn} | {plc} | {smy}")

    text = (
        "=== 유사사고 분석 요약(trend_norm) ===\n"
        + "\n".join(summary_lines)
        + "\n\n"
        + "=== 대표 유사사례(상위) ===\n"
        + ("\n".join(case_lines) if case_lines else "- (없음)")
    )

    return _clip_text(text, 2500)


# =========================
# Similar 점검 분석 컨텍스트 생성 (safety_map_norm 기반)
# =========================
def build_similar_context_from_smap_df(
    smap_df: pd.DataFrame,
    smap_data: Dict[str, Any],
    *,
    months: Optional[int] = 12,
    max_cases: int = 20,
) -> str:
    if smap_df is None or smap_df.empty:
        return ""

    issue = _coalesce_text(smap_data.get("issue_type"), default="")
    dept = _coalesce_text(smap_data.get("target_dept"), default="")
    place = _coalesce_text(smap_data.get("place_main"), default="")
    rid = _coalesce_text(smap_data.get("row_id"), default="").strip()

    df = smap_df.copy()

    if "checked_at" in df.columns:
        df["checked_at_dt"] = pd.to_datetime(df["checked_at"], errors="coerce")
    else:
        df["checked_at_dt"] = pd.NaT

    if rid and "row_id" in df.columns:
        df = df[df["row_id"].astype(str) != rid]

    if months and df["checked_at_dt"].notna().any():
        cutoff = pd.Timestamp.now() - pd.DateOffset(months=int(months))
        df = df[df["checked_at_dt"].isna() | (df["checked_at_dt"] >= cutoff)]

    if df.empty:
        return ""

    mask = pd.Series(True, index=df.index)
    if issue and "issue_type" in df.columns:
        mask &= df["issue_type"].astype(str).str.contains(issue, na=False)
    if dept and "target_dept" in df.columns:
        mask &= df["target_dept"].astype(str).str.contains(dept, na=False)
    if place and "place_main" in df.columns:
        mask &= df["place_main"].astype(str).str.contains(place, na=False)

    cand = df[mask].copy()
    if cand.empty:
        return ""

    cand = cand.sort_values(["checked_at_dt"], ascending=[False])
    total = len(cand)
    top = cand.head(int(max_cases))

    def _top_counts(col: str, n: int = 5) -> str:
        if col not in cand.columns:
            return ""
        vc = cand[col].astype(str).replace("nan", "").replace("None", "").value_counts()
        vc = vc[vc.index.astype(str).str.len() > 0].head(n)
        if vc.empty:
            return ""
        return ", ".join([f"{idx}({cnt})" for idx, cnt in vc.items()])

    lines = []
    lines.append(f"- 유사점검 기준: 부서='{dept or '미기재'}' / 지적유형='{issue or '미기재'}' / 장소='{place or '미기재'}'")
    lines.append(f"- 유사점검 건수: {total}건 (최근 {months}개월)" if months else f"- 유사점검 건수: {total}건")

    s_issue = _top_counts("issue_type", 5)
    s_dept = _top_counts("target_dept", 5)
    s_place = _top_counts("place_main", 5)

    if s_issue:
        lines.append(f"- 지적유형 Top: {s_issue}")
    if s_dept:
        lines.append(f"- 대상부서 Top: {s_dept}")
    if s_place:
        lines.append(f"- 장소 Top: {s_place}")

    case_lines = []
    for _, r in top.iterrows():
        rid2 = str(r.get("row_id", ""))
        d = _strip_zero_time(str(r.get("checked_at", ""))).split("T")[0]
        ttl = str(r.get("title", ""))
        if len(ttl) > 80:
            ttl = ttl[:80] + "…"
        case_lines.append(f"- [{rid2}] {d} | {str(r.get('target_dept',''))} | {str(r.get('issue_type',''))} | {ttl}")

    text = (
        "=== 유사점검 분석 요약(safety_map_norm) ===\n"
        + "\n".join(lines)
        + "\n\n"
        + "=== 대표 유사사례(상위) ===\n"
        + ("\n".join(case_lines) if case_lines else "- (없음)")
    )
    return _clip_text(text, 2500)


# =========================
# Result schema
# =========================
@dataclass
class ReportResult:
    pdf_path: Path
    report_json: Dict[str, Any]
    raw_llm_text: str


class ReportService:
    def __init__(self, client: Optional[OllamaClient] = None):
        self.settings = get_settings()
        self.client = client or OllamaClient()

    def generate_report_pdf(
        self,
        incident_data: Dict[str, Any],
        reg_context: str = "",
        output_filename: Optional[str] = None,
        *,
        similar_context: str = "",
        analysis_section: Optional[Dict[str, Any]] = None,  # ✅ 추가: 02 분석 섹션 trend에도 주입
        num_predict: int = 1200,
        reg_context_max_chars: int = 6000,
        similar_context_max_chars: int = 2500,
        save_debug_files: bool = True,
    ) -> ReportResult:
        settings = self.settings

        prompt_template = _load_report_prompt_incident()
        template_path = settings.paths.base_dir / "report" / "templates" / "report_base.json"
        template = _load_report_template(template_path)

        reg_context_safe = _clip_text(reg_context or "", max_chars=int(reg_context_max_chars))
        similar_context_safe = _clip_text(similar_context or "", max_chars=int(similar_context_max_chars))

        safe_incident_data = _json_sanitize(incident_data or {})
        incident_json_text = json.dumps(safe_incident_data, ensure_ascii=False, indent=2)

        prompt = render_template(
            prompt_template,
            values={
                "incident_data": incident_json_text,
                "reg_context": reg_context_safe,
                "similar_context": similar_context_safe,
            },
        )
        prompt = _ensure_prompt_has_similar_context(prompt, similar_context_safe)
        prompt = prompt + _reasoning_required_guardrail() + _json_only_guardrail()

        debug_dir = settings.paths.outputs_dir / "reports"
        llm_json, raw, parse_ok, ts = _run_llm_and_parse_json(
            client=self.client,
            prompt=prompt,
            debug_dir=debug_dir,
            debug_prefix="incident",
            num_predict=int(num_predict),
            save_debug_files=bool(save_debug_files),
        )

        report_json = _normalize_report_json(
            template=template,
            llm_json=llm_json if parse_ok else {},
            incident_data=safe_incident_data,
        )

        report_json = _ensure_reasoning_fields_generic(report_json, safe_incident_data, evidence_tag="incident_data")

        # ✅ 02 분석 섹션 강제 주입 (trend도 이제 가능)
        report_json = _inject_analysis_section(report_json, analysis_section or {})

        report_json_safe = _json_sanitize(report_json)

        fname = output_filename or f"report_{ts}.pdf"
        pdf_path = settings.paths.outputs_dir / "reports" / fname
        title = template.get("title") or "DTRO-Safety-On 사고(동향) 보고서"

        render_report_pdf(
            output_path=pdf_path,
            template_path=template_path,
            report_data=report_json_safe,
            title_override=title,
        )

        return ReportResult(pdf_path=pdf_path, report_json=report_json_safe, raw_llm_text=raw)

    def generate_safety_map_pdf(
        self,
        safety_map_data: Dict[str, Any],
        reg_context: str = "",
        output_filename: Optional[str] = None,
        *,
        similar_context: str = "",
        analysis_section: Optional[Dict[str, Any]] = None,
        num_predict: int = 1100,
        reg_context_max_chars: int = 6000,
        similar_context_max_chars: int = 2500,
        save_debug_files: bool = True,
    ) -> ReportResult:
        settings = self.settings

        # ✅ 1) safety_map 입력 표준화(원본 row여도 여기서 강제 변환)
        smap_in = safety_map_data or {}
        if "row_id" not in smap_in and ("지도점검일자" in smap_in or "점검자" in smap_in or "지적유형" in smap_in):
            smap_in = map_safety_map_row_to_safety_map_data(smap_in)

        prompt_template = _load_report_prompt_safety_map()
        template_path = settings.paths.base_dir / "report" / "templates" / "report_base_safety_map.json"
        template = _load_report_template(template_path)

        reg_context_safe = _clip_text(reg_context or "", max_chars=int(reg_context_max_chars))
        similar_context_safe = _clip_text(similar_context or "", max_chars=int(similar_context_max_chars))

        safe_data = _json_sanitize(smap_in)
        data_json_text = json.dumps(safe_data, ensure_ascii=False, indent=2)

        prompt = render_template(
            prompt_template,
            values={
                "safety_map_data": data_json_text,
                "reg_context": reg_context_safe,
                "similar_context": similar_context_safe,
            },
        )
        prompt = _ensure_prompt_has_similar_context(prompt, similar_context_safe)
        prompt = prompt + _reasoning_required_guardrail() + _json_only_guardrail()

        debug_dir = settings.paths.outputs_dir / "reports"
        llm_json, raw, parse_ok, ts = _run_llm_and_parse_json(
            client=self.client,
            prompt=prompt,
            debug_dir=debug_dir,
            debug_prefix="smap",
            num_predict=int(num_predict),
            save_debug_files=bool(save_debug_files),
        )

        # ✅ 2) normalize
        report_json = _normalize_report_json(
            template=template,
            llm_json=llm_json if parse_ok else {},
            incident_data=safe_data,  # safety_map alias fallback이 먹도록 여기 넣음
        )

        # ✅ 3) safety_map 빈칸 보강(A/B/C 핵심)
        report_json = _fixup_safety_map_overview(report_json, safe_data)
        report_json = _fixup_safety_map_actions(report_json, safe_data)

        # ✅ 4) reasoning 품질 보강(공용)
        report_json = _ensure_reasoning_fields_generic(report_json, safe_data, evidence_tag="safety_map_data")

        # ✅ 5) 분석 섹션 강제 주입
        report_json = _inject_analysis_section(report_json, analysis_section or {})

        report_json_safe = _json_sanitize(report_json)

        fname = output_filename or f"safety_map_report_{ts}.pdf"
        pdf_path = settings.paths.outputs_dir / "reports" / fname
        title = template.get("title") or "DTRO-Safety-On 점검(safety_map) 보고서"

        render_report_pdf(
            output_path=pdf_path,
            template_path=template_path,
            report_data=report_json_safe,
            title_override=title,
        )

        return ReportResult(pdf_path=pdf_path, report_json=report_json_safe, raw_llm_text=raw)
