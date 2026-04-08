# backend/services/qa_service.py
"""
QA Service (DTRO-Safety-On) - Compatibility Hardened

목표
- v3 프롬프트(trend/safety_map) 강제 적용
- 기존 Streamlit 페이지/테스트 코드에서 넘기는 레거시 인자들을 모두 수용(호환성)
- unexpected keyword argument 류의 런타임 실패를 원천 차단
- 프롬프트 탈선(설명형 답변) 감지 후 1회 재질의

주의
- template_name/template_override 등은 "받기만" 하고 기본은 dataset 정책을 따른다.
"""

from __future__ import annotations

import json
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

    # ✅ 운영 안전: 템플릿 override를 기본적으로 막는다(필요 시 True로 켜서 개발 테스트)
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
        """
        운영 정책: dataset 기준 템플릿 강제.
        개발/테스트에서만 override 허용(ALLOW_TEMPLATE_OVERRIDE=True).
        """
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
        """
        ✅ trend에서 검색 recall 개선용: '핵심 키워드'만 짧게 추가
        (너무 길게 붙이면 임베딩이 오히려 흐려질 수 있어서 최소화)
        """
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
    # Public API (호환성 강화)
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
        # ✅ 레거시/호환 인자들
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

        # safety_map 레거시 옵션
        if attach_records_to_question and safety_map_records:
            s_txt = self._coerce_text(safety_map_records, max_chars=max(200, int(attach_records_max_chars)))
            search_question = q + "\n\n[records]\n" + s_txt

        # ✅ trend에서 incident_data가 있으면 핵심 힌트를 검색에도 조금만 추가
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

        logger.info(
            f"[QAService] done dataset={dataset} template={template} top_k={k} hits={len(hits)} "
            f"context_len={len(context)} answer_len={len(answer)} per_chunk_chars={per_chunk_chars} "
            f"max_context_chars={max_context_chars} num_predict={n_predict}"
        )

        return QAResult(answer=answer, citations=citations, used_context=context)
