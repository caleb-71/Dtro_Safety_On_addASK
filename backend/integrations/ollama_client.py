# backend/integrations/ollama_client.py
"""
Ollama Client (DTRO-Safety-On) - 100% Enterprise API Mode

역할
1) 임베딩(Embedding): 사내망 API (172.16.101.180:8080/llm/embedding) 사용
2) LLM 생성(Generate): 사내망 API (172.16.101.180:8080/llm/model) 사용

모든 AI 연산을 사내망 고성능 서버(8080 포트)에 위임하여,
일반 사용자의 PC에는 Ollama를 설치할 필요가 없는 완벽한 웹 서비스 환경을 제공합니다.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union, Tuple

import requests
from requests import Response, Session

from core.config import get_settings
from core.logger import get_logger

logger = get_logger(__name__)


# =========================
# Response Models
# =========================
@dataclass
class OllamaGenerateResult:
    text: str
    model: str
    total_duration_ms: Optional[int] = None
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None


@dataclass
class OllamaEmbedResult:
    embeddings: List[List[float]]
    model: str


# =========================
# Client
# =========================
class OllamaClient:
    def __init__(
            self,
            base_url: Optional[str] = None,
            llm_model: Optional[str] = None,
            embed_model: Optional[str] = None,
            timeout_sec: Optional[int] = None,
            max_retries: int = 2,
            retry_sleep_sec: float = 0.8,
            connect_timeout_sec: int = 10,
            session: Optional[Session] = None,
    ):
        s = get_settings()

        # 모든 요청을 사내망 8080 포트로 단일화
        self.base_url = "http://172.16.101.180:8080"

        self.llm_model = llm_model or s.ollama.llm_model or "llama3.1:8b"
        # 사내 서버에 구축된 임베딩 모델명으로 변경 (test_embedding.py 참고)
        self.embed_model = embed_model or "bge-m3:latest"

        self.timeout_sec = int(timeout_sec or s.ollama.timeout_sec)
        self.connect_timeout_sec = int(connect_timeout_sec)
        self.max_retries = int(max_retries)
        self.retry_sleep_sec = float(retry_sleep_sec)
        self._session = session or requests.Session()

        logger.info(
            f"[OllamaClient] Init Enterprise Mode: API={self.base_url}, "
            f"LLM={self.llm_model}, Embed={self.embed_model}"
        )

    @staticmethod
    def _new_request_id() -> str:
        return uuid.uuid4().hex[:8]

    def _timeout_tuple(self, timeout_sec: Optional[int] = None) -> Tuple[int, int]:
        return (self.connect_timeout_sec, int(timeout_sec or self.timeout_sec))

    @staticmethod
    def _safe_body_head(text: str, limit: int = 800) -> str:
        return (text or "").replace("\r", "\\r").replace("\n", "\\n")[:limit]

    def _raise_for_status(self, resp: Response, full_url: str, rid: str, dt: float) -> None:
        if resp.status_code == 200:
            return
        body_head = self._safe_body_head(resp.text or "", limit=800)
        raise RuntimeError(
            f"API error status={resp.status_code}, url={full_url}, rid={rid}, "
            f"dt={dt:.2f}s, body_head={body_head}"
        )

    # 반환 타입을 Any로 변경하여 Dict(Generate)와 List(Embed) 모두 수용
    def _post_json(
            self,
            full_url: str,
            payload: Dict[str, Any],
            *,
            timeout_sec: Optional[int] = None,
            rid: Optional[str] = None,
    ) -> Any:
        rid = rid or self._new_request_id()
        last_err: Optional[Exception] = None
        total_tries = self.max_retries + 1
        t_connect, t_read = self._timeout_tuple(timeout_sec)

        for attempt in range(1, total_tries + 1):
            t0 = time.time()
            try:
                resp = self._session.post(full_url, json=payload, timeout=(t_connect, t_read))
                dt = time.time() - t0
                self._raise_for_status(resp, full_url, rid, dt)

                try:
                    data = resp.json()
                except Exception as je:
                    raise RuntimeError(f"JSON decode failed url={full_url} rid={rid}") from je

                return data

            except Exception as e:
                dt = time.time() - t0
                last_err = e
                logger.warning(f"[OllamaClient] POST failed url={full_url} rid={rid} dt={dt:.2f}s err={e}")

            if attempt < total_tries:
                time.sleep(self.retry_sleep_sec)

        raise RuntimeError(f"API request failed after retries. rid={rid} last_err={last_err}")

    # =========================
    # Embeddings (사내망 API 통신)
    # =========================
    def embed(
            self,
            texts: Union[str, List[str]],
            model: Optional[str] = None,
            timeout_sec: Optional[int] = None,
            request_id: Optional[str] = None,
    ) -> OllamaEmbedResult:
        rid = request_id or self._new_request_id()
        m = model or self.embed_model
        texts_list = [texts] if isinstance(texts, str) else list(texts)
        vectors: List[List[float]] = []

        # 사내망 임베딩 엔드포인트 사용
        full_url = f"{self.base_url}/llm/embedding"

        for i, t in enumerate(texts_list, start=1):
            t = (t or "").strip()
            if not t:
                vectors.append([])
                continue

            payload = {"model": m, "prompt": t}
            data = self._post_json(full_url, payload, timeout_sec=timeout_sec, rid=rid)

            # test_embedding.py 분석 반영: 리스트 자체를 반환함
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected embeddings response (Expected list). rid={rid} data={data}")

            vectors.append([float(x) for x in data])
            time.sleep(0.05)

        return OllamaEmbedResult(embeddings=vectors, model=m)

    # =========================
    # Generation (사내망 API 통신)
    # =========================
    def generate(
            self,
            prompt: str,
            model: Optional[str] = None,
            temperature: float = 0.2,
            top_p: float = 0.9,
            num_predict: int = 4096,
            stop: Optional[List[str]] = None,
            timeout_sec: Optional[int] = None,
            request_id: Optional[str] = None,
    ) -> OllamaGenerateResult:
        rid = request_id or self._new_request_id()
        m = model or self.llm_model
        p = (prompt or "").strip()

        session_id = f"dtro_sync_{rid}_{int(time.time())}"
        payload = {
            "model": m,
            "prompt": p,
            "stream": False,
            "num_ctx": int(num_predict),
            "session": session_id
        }

        # 사내망 LLM 엔드포인트 사용
        full_url = f"{self.base_url}/llm/model"

        t0 = time.time()
        data = self._post_json(full_url, payload, timeout_sec=timeout_sec, rid=rid)
        dt = time.time() - t0

        text = data.get("content", "")
        model_name = data.get("model", m)

        return OllamaGenerateResult(
            text=str(text),
            model=str(model_name),
            total_duration_ms=int(dt * 1000),
            prompt_eval_count=None,
            eval_count=None,
        )

    def ping(self) -> bool:
        url = f"{self.base_url}/llm/list"
        try:
            r = self._session.get(url, timeout=5)
            return r.status_code == 200
        except Exception:
            return False