# backend/services/qa_service.py
"""
QA Service (DTRO-Safety-On) - Compatibility Hardened & NL2SQL Added

목표
- v3 프롬프트(trend/safety_map) 강제 적용
- 기존 Streamlit 페이지/테스트 코드에서 넘기는 레거시 인자들을 모두 수용(호환성)
- unexpected keyword argument 류의 런타임 실패를 원천 차단
- 프롬프트 탈선(설명형 답변) 감지 후 1회 재질의
- [NEW] 대시보드 자연어 필터 추출(NL2SQL) 기능 추가
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Literal

from core.config import get_settings
from core.logger import get_logger
from backend.integrations.ollama_client import OllamaClient
from rag.retrieval.retriever import Retriever, RetrievedChunk
from rag.prompting.renderer import render_from_file

logger = get_logger(__name__)

DatasetType = Literal["trend", "safety_map", "unified"]


# ==================================================
# Dataclasses
# ==================================================
@dataclass
class Citation:
    doc_title: str
    category: str
    page_no: int
    source_path: str
    chunk_id: str
    score: float


@dataclass
class QAResult:
    answer: str
    citations: List[Citation]
    used_context: str


# ==================================================
# QA Service
# ==================================================
class QAService:
    """
    dataset별 템플릿 정책(운영 기준)
    - trend      -> qa_trend_v3.md
    - safety_map -> qa_safety_map_v3.md
    - unified    -> qa_general_v1.md
    """

    DEFAULT_TEMPLATE_BY_DATASET: Dict[str, str] = {
        "trend": "qa_trend_v3.md",
        "safety_map": "qa_safety_map_v3.md",
        "unified": "qa_general_v1.md",
    }

    BAD_PATTERNS = ["이 프롬프트", "입력:", "출력:", "분석은 다음"]

    # 운영 안전: 템플릿 override를 기본적으로 막는다
    ALLOW_TEMPLATE_OVERRIDE = False

    def __init__(
            self,
            retriever: Optional[Retriever] = None,
            client: Optional[OllamaClient] = None,
            auto_load_index: bool = False,
    ):
        self.settings = get_settings()
        self.client = client or OllamaClient()
        self.retriever = retriever or Retriever(client=self.client)

        self._index_loaded = False
        if auto_load_index:
            self._ensure_index_loaded()

    # ==================================================
    # [NEW] 자연어 대시보드 필터 추출 (NL2SQL)
    # ==================================================
    def extract_dashboard_filters(self, user_query: str) -> dict:
        """
        사용자의 자연어 질의에서 날짜(start, end) 및 사고/지적 유형 키워드를 추출하여 반환합니다.
        """
        system_prompt = """
        당신은 철도안전 데이터 대시보드의 필터 조건을 추출하는 AI 어시스턴트입니다.
        사용자의 자연어 질문을 분석하여 다음 JSON 스키마에 맞게 필터 조건을 추출하세요.
        반드시 JSON 형식으로만 답변해야 하며, 다른 설명은 절대 추가하지 마세요.

        [JSON 스키마]
        {
            "start_date": "YYYY-MM-DD",
            "end_date": "YYYY-MM-DD",
            "type_keyword": "사고유형 또는 장소 관련 단어"
        }

        - 조건에 해당하는 내용이 없으면 값에 null을 넣으세요.
        - 연도만 명시되어 있다면(예: 2024년) start_date는 "2024-01-01", end_date는 "2024-12-31"로 설정하세요.
        - 특정 월까지만 명시되어 있다면(예: 2024년 1월부터 8월) start_date는 "2024-01-01", end_date는 "2024-08-31"로 설정하세요.
        """
        prompt_text = f"{system_prompt}\n\n질문: {user_query}\n답변:"

        try:
            # 빠른 응답과 정확성을 위해 temperature를 낮춤
            gen = self.client.generate(prompt=prompt_text, temperature=0.1, num_predict=200)
            answer_text = gen.text or ""

            # JSON 블록만 정규식으로 안전하게 추출
            json_match = re.search(r'\{.*\}', answer_text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception as e:
            logger.error(f"[QAService] 필터 추출 실패: {e}")

        return {}

    # ==================================================
    # Internal helpers
    # ==================================================
    def _ensure_index_loaded(self) -> None:
        if self._index_loaded:
            return
        self.retriever.load_index()
        self._index_loaded = True
        logger.info(f"[QAService] index loaded ntotal={self.retriever.store.ntotal}")

    def _coerce_text(self, v: Any, *, max_chars: int) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            text = v
        else:
            try:
                text = json.dumps(v, ensure_ascii=False, indent=2)
            except Exception:
                text = str(v)

        text = (text or "").strip()
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars].rstrip() + " ...[truncated]"
        return text

    def _safe_json_block(self, title: str, obj: Any) -> str:
        if obj is None:
            return ""
        try:
            return f"\n\n[{title}]\n" + json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            return f"\n\n[{title}]\n(직렬화 실패)"

    def _build_context(
            self,
            chunks: List[RetrievedChunk],
            *,
            per_chunk_chars: int,
            max_context_chars: int,
    ) -> str:
        lines: List[str] = []
        total = 0

        for i, ch in enumerate(chunks, start=1):
            m = ch.meta
            header = f"- [#{i}] ({m.doc_title} | {m.category} | p.{m.page_no} | score={ch.score:.3f})"

            body = (m.text or "").strip()
            if per_chunk_chars > 0 and len(body) > per_chunk_chars:
                body = body[:per_chunk_chars].rstrip() + " ...[truncated]"

            block = header + "\n" + body + "\n\n"

            if max_context_chars > 0 and total + len(block) > max_context_chars:
                break

            lines.append(block)
            total += len(block)

        return "".join(lines).strip()

    def _make_citations(self, hits: List[RetrievedChunk]) -> List[Citation]:
        out: List[Citation] = []
        for h in hits:
            m = h.meta
            out.append(
                Citation(
                    doc_title=m.doc_title,
                    category=m.category,
                    page_no=int(m.page_no),
                    source_path=m.source_path,
                    chunk_id=m.chunk_id,
                    score=float(h.score),
                )
            )
        return out

    def _has_bad_pattern(self, text: str) -> bool:
        t = text or ""
        return any(p in t for p in self.BAD_PATTERNS)

    def _pick_template(
            self,
            dataset: DatasetType,
            *,
            template_override: Optional[str],
            template_name: Optional[str],
    ) -> str:
        base = self.DEFAULT_TEMPLATE_BY_DATASET.get(dataset, "qa_general_v1.md")

        if self.ALLOW_TEMPLATE_OVERRIDE:
            if template_override:
                logger.warning("[QAService] template_override applied (dev/test mode).")
                return template_override
            if template_name:
                logger.warning("[QAService] template_name applied (dev/test mode).")
                return template_name

        return base

    def _trend_search_hint(self, incident_data: Dict[str, Any]) -> str:
        if not incident_data:
            return ""

        line = str(incident_data.get("line", "") or "").strip()
        station = str(incident_data.get("station", "") or "").strip()
        atype = str(incident_data.get("accident_type", "") or "").strip()
        loc = str(incident_data.get("detail_location", "") or "").strip()

        bits = [b for b in [line, station, atype, loc] if b]
        if not bits:
            return ""
        return " | " + " ".join(bits[:4])

    # ==================================================
    # Public API
    # ==================================================
    def ask(
            self,
            question: str,
            top_k: Optional[int] = None,
            *,
            dataset: DatasetType = "trend",
            safety_map_records: Optional[Any] = None,
            past_similar_summary: Optional[Any] = None,
            incident_data: Optional[Dict[str, Any]] = None,
            template_name: Optional[str] = None,
            template_override: Optional[str] = None,
            per_chunk_chars: int = 1200,
            max_context_chars: int = 6000,
            num_predict: Optional[int] = None,
            timeout_sec: Optional[int] = None,
            attach_records_to_question: bool = False,
            attach_records_max_chars: int = 1200,
            search_tone_filter: bool = False,
            **_ignored: Any,
    ) -> QAResult:

        q = (question or "").strip()
        if not q:
            return QAResult(answer="질문이 비어 있습니다.", citations=[], used_context="")

        # 0) 인덱스 로드
        try:
            self._ensure_index_loaded()
        except Exception:
            return QAResult(answer="규정 인덱스를 로드하지 못했습니다.", citations=[], used_context="")

        # 1) 검색
        k = int(top_k or self.settings.rag.top_k)

        search_question = q

        if attach_records_to_question and safety_map_records:
            s_txt = self._coerce_text(safety_map_records, max_chars=max(200, int(attach_records_max_chars)))
            search_question = q + "\n\n[records]\n" + s_txt

        if dataset == "trend" and incident_data:
            search_question = search_question + self._trend_search_hint(incident_data)

        if search_tone_filter:
            logger.info("[QAService] search_tone_filter=True (currently passthrough)")

        hits = self.retriever.retrieve(search_question, top_k=k)
        if not hits:
            return QAResult(answer="관련 규정 근거를 찾지 못했습니다. (근거 부족)", citations=[], used_context="")

        # 2) context 구성
        context = self._build_context(
            hits,
            per_chunk_chars=int(per_chunk_chars),
            max_context_chars=int(max_context_chars),
        )

        # 3) 프롬프트 입력 구성
        safety_txt = self._coerce_text(safety_map_records, max_chars=5000)
        past_txt = self._coerce_text(past_similar_summary, max_chars=3500)

        question_for_prompt = q
        if dataset == "trend" and incident_data:
            question_for_prompt = q + self._safe_json_block("사고/상황 정보(JSON)", incident_data)

        values: Dict[str, Any] = {
            "question": question_for_prompt,
            "context": context,
            "REGULATION_CONTEXT": context,
            "regulation_context": context,
            "SAFETY_MAP_RECORDS": safety_txt,
            "PAST_SIMILAR_SUMMARY": past_txt,
            "safety_map_records": safety_txt,
            "past_similar_summary": past_txt,
        }

        template = self._pick_template(
            dataset=dataset,
            template_override=template_override,
            template_name=template_name,
        )

        prompt = render_from_file(template, values=values)

        # 4) LLM 호출
        if num_predict is None:
            n_predict = 1600 if dataset == "safety_map" else 1200 if dataset == "trend" else 900
        else:
            n_predict = int(num_predict)

        gen = self.client.generate(
            prompt=prompt,
            temperature=0.2,
            top_p=0.9,
            num_predict=n_predict,
            timeout_sec=timeout_sec,
        )

        answer = (gen.text or "").strip()

        # 5) 프롬프트 탈선 감지 → 1회 재질의
        if dataset in ("trend", "safety_map") and self._has_bad_pattern(answer):
            logger.warning("[QAService] bad pattern detected → retry once")
            retry_prompt = (
                    prompt
                    + "\n\n※ 주의: 프롬프트 설명/입력/출력 안내를 작성하지 말고, "
                      "공식 보고서 내용만 작성하십시오."
            )
            gen = self.client.generate(
                prompt=retry_prompt,
                temperature=0.2,
                top_p=0.9,
                num_predict=n_predict,
                timeout_sec=timeout_sec,
            )
            answer = (gen.text or "").strip()

        citations = self._make_citations(hits)

        return QAResult(answer=answer, citations=citations, used_context=context)