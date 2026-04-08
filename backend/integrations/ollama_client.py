# backend/integrations/ollama_client.py
"""
Ollama Client (DTRO-Safety-On)

역할
- Ollama HTTP API를 호출해
  1) 임베딩(Embedding) 벡터 생성
  2) LLM 텍스트 생성(Generate)
을 수행한다.

운영 안정화 포인트(실전)
- timeout을 (connect, read)로 분리해 느린 CPU/서버 환경에서 안정성을 높임
- JSON 파싱 실패(빈 응답/텍스트 응답) 시 디버그가 가능하도록 에러 메시지 강화
- stream=False 강제(초보자 운영/파서 안정)
- 재시도 로그를 더 구체화(Timeout/Connection/HTTP/JSON)
- request_id(rid) 로깅으로 "중복 generate" 추적 가능
- requests.Session 재사용으로 연결 비용 절감
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
    """
    Ollama API 호출 공통 래퍼

    - embed(): texts -> embeddings
    - generate(): prompt -> completion text

    운영 팁
    - CPU 환경에서는 generate가 오래 걸릴 수 있음 → read timeout을 넉넉히
    - 실패 시 1~2회 가벼운 재시도 권장
    """

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

        self.base_url = (base_url or s.ollama.base_url).rstrip("/")
        self.llm_model = llm_model or s.ollama.llm_model
        self.embed_model = embed_model or s.ollama.embed_model

        # settings.yaml의 timeout_sec를 그대로 사용(함수 인자가 우선)
        self.timeout_sec = int(timeout_sec or s.ollama.timeout_sec)

        # connect/read 분리 (requests timeout 튜플)
        self.connect_timeout_sec = int(connect_timeout_sec)

        self.max_retries = int(max_retries)
        self.retry_sleep_sec = float(retry_sleep_sec)

        # Session 재사용(연결 비용/지연 감소)
        self._session = session or requests.Session()

        logger.info(
            f"[OllamaClient] init base_url={self.base_url}, llm_model={self.llm_model}, "
            f"embed_model={self.embed_model}, timeout={self.timeout_sec}s "
            f"(connect={self.connect_timeout_sec}s, read={self.timeout_sec}s)"
        )

    # -------------------------
    # Utils
    # -------------------------
    @staticmethod
    def _new_request_id() -> str:
        # 추적이 쉬운 짧은 rid
        return uuid.uuid4().hex[:8]

    def _timeout_tuple(self, timeout_sec: Optional[int] = None) -> Tuple[int, int]:
        """
        requests timeout을 (connect, read)로 반환.
        - timeout_sec가 들어오면 read timeout으로 사용
        """
        read_t = int(timeout_sec or self.timeout_sec)
        return (self.connect_timeout_sec, read_t)

    @staticmethod
    def _safe_body_head(text: str, limit: int = 800) -> str:
        t = text or ""
        t = t.replace("\r", "\\r").replace("\n", "\\n")
        return t[:limit]

    def _raise_for_status(self, resp: Response, url: str, path: str, rid: str, dt: float) -> None:
        """
        HTTP status 에러를 더 친절한 메시지로 변환.
        """
        if resp.status_code == 200:
            return
        body_head = self._safe_body_head(resp.text or "", limit=800)
        raise RuntimeError(
            f"Ollama API error status={resp.status_code}, url={url}, path={path}, rid={rid}, "
            f"dt={dt:.2f}s, body_head={body_head}"
        )

    def _post_json(
        self,
        path: str,
        payload: Dict[str, Any],
        *,
        timeout_sec: Optional[int] = None,
        rid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ollama에 JSON POST 요청.
        - 네트워크/일시 오류에 대해 가벼운 재시도 포함
        - JSON 파싱 실패(빈 응답 / 텍스트 응답 / HTML 응답 등)도 명확히 로깅
        """
        rid = rid or self._new_request_id()
        url = f"{self.base_url}{path}"
        last_err: Optional[Exception] = None

        total_tries = self.max_retries + 1
        (t_connect, t_read) = self._timeout_tuple(timeout_sec)

        for attempt in range(1, total_tries + 1):
            t0 = time.time()
            try:
                resp = self._session.post(
                    url,
                    json=payload,
                    timeout=(t_connect, t_read),
                )
                dt = time.time() - t0

                self._raise_for_status(resp, url, path, rid, dt)

                try:
                    data = resp.json()
                except Exception as je:
                    head = self._safe_body_head(resp.text or "", limit=800)
                    raise RuntimeError(
                        f"Ollama response JSON decode failed url={url} path={path} rid={rid} "
                        f"status={resp.status_code} dt={dt:.2f}s body_head={head}"
                    ) from je

                logger.info(
                    f"[OllamaClient] POST ok path={path} rid={rid} attempt={attempt}/{total_tries} "
                    f"dt={dt:.2f}s"
                )
                return data

            except requests.exceptions.ReadTimeout as e:
                dt = time.time() - t0
                last_err = e
                logger.warning(
                    f"[OllamaClient] POST ReadTimeout path={path} rid={rid} attempt={attempt}/{total_tries} "
                    f"dt={dt:.2f}s timeout_read={t_read}s err={e}"
                )
            except requests.exceptions.ConnectTimeout as e:
                dt = time.time() - t0
                last_err = e
                logger.warning(
                    f"[OllamaClient] POST ConnectTimeout path={path} rid={rid} attempt={attempt}/{total_tries} "
                    f"dt={dt:.2f}s timeout_connect={t_connect}s err={e}"
                )
            except requests.exceptions.ConnectionError as e:
                dt = time.time() - t0
                last_err = e
                logger.warning(
                    f"[OllamaClient] POST ConnectionError path={path} rid={rid} attempt={attempt}/{total_tries} "
                    f"dt={dt:.2f}s err={e}"
                )
            except Exception as e:
                dt = time.time() - t0
                last_err = e
                logger.warning(
                    f"[OllamaClient] POST failed path={path} rid={rid} attempt={attempt}/{total_tries} "
                    f"dt={dt:.2f}s err={e}"
                )

            if attempt < total_tries:
                time.sleep(self.retry_sleep_sec)

        raise RuntimeError(f"Ollama API request failed after retries. rid={rid} last_err={last_err}")

    # =========================
    # Embeddings
    # =========================
    def embed(
        self,
        texts: Union[str, List[str]],
        model: Optional[str] = None,
        timeout_sec: Optional[int] = None,
        request_id: Optional[str] = None,
    ) -> OllamaEmbedResult:
        """
        텍스트(1개 또는 여러개)를 임베딩 벡터로 변환.

        Ollama API:
        - POST /api/embeddings
          payload: {"model": "...", "prompt": "text"}

        구현:
        - 안정성을 위해 단건 호출 반복(추후 batch 확장 가능)
        """
        rid = request_id or self._new_request_id()
        m = model or self.embed_model
        texts_list = [texts] if isinstance(texts, str) else list(texts)

        logger.info(f"[EMB-START] rid={rid} model={m} n={len(texts_list)}")

        vectors: List[List[float]] = []
        t0_all = time.time()

        for i, t in enumerate(texts_list, start=1):
            t = (t or "").strip()
            if not t:
                vectors.append([])
                logger.info(f"[EMB-SKIP] rid={rid} ({i}/{len(texts_list)}) empty_text")
                continue

            payload = {"model": m, "prompt": t}
            data = self._post_json("/api/embeddings", payload, timeout_sec=timeout_sec, rid=rid)

            emb = data.get("embedding")
            if not isinstance(emb, list) or len(emb) == 0:
                raise RuntimeError(f"Unexpected embeddings response rid={rid} keys={list(data.keys())}")

            vectors.append([float(x) for x in emb])
            logger.info(f"[EMB-OK] rid={rid} ({i}/{len(texts_list)}) dim={len(emb)}")

            # 과도한 연속 호출 방지(서버/로컬 안정)
            time.sleep(0.05)

        dt_all = time.time() - t0_all
        logger.info(f"[EMB-END] rid={rid} model={m} n={len(texts_list)} elapsed={dt_all:.2f}s")

        return OllamaEmbedResult(embeddings=vectors, model=m)

    # =========================
    # Generation (prompt → text)
    # =========================
    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.2,
        top_p: float = 0.9,
        num_predict: int = 800,
        stop: Optional[List[str]] = None,
        timeout_sec: Optional[int] = None,
        request_id: Optional[str] = None,
    ) -> OllamaGenerateResult:
        """
        단일 프롬프트 → 결과 텍스트 생성.

        Ollama API:
        - POST /api/generate
          payload: {
            "model": "...",
            "prompt": "...",
            "stream": false,
            "options": {...}
          }

        주의:
        - stream=false로 "한 번에" 결과 받는 방식(운영 안정)
        - rid로 "중복 generate" 추적 가능
        """
        rid = request_id or self._new_request_id()

        m = model or self.llm_model
        p = (prompt or "").strip()
        if not p:
            raise ValueError("prompt is empty")

        payload: Dict[str, Any] = {
            "model": m,
            "prompt": p,
            "stream": False,  # ✅ 안정적으로 단일 JSON 응답 받기
            "options": {
                "temperature": float(temperature),
                "top_p": float(top_p),
                "num_predict": int(num_predict),
            },
        }
        if stop:
            payload["options"]["stop"] = stop

        logger.info(
            f"[GEN-START] rid={rid} model={m} prompt_len={len(p)} "
            f"num_predict={int(num_predict)} temp={float(temperature)} top_p={float(top_p)}"
        )

        t0 = time.time()
        data = self._post_json("/api/generate", payload, timeout_sec=timeout_sec, rid=rid)
        dt = time.time() - t0

        text = data.get("response", "")
        model_name = data.get("model", m)

        if not isinstance(text, str):
            text = str(text)

        if len(text.strip()) == 0:
            logger.warning(
                f"[GEN-WARN] rid={rid} generate returned EMPTY text. model={model_name} keys={list(data.keys())}"
            )

        total_duration = data.get("total_duration")
        total_ms = None
        if isinstance(total_duration, int):
            total_ms = int(total_duration / 1_000_000)  # ns → ms (보수적)

        result = OllamaGenerateResult(
            text=text,
            model=str(model_name),
            total_duration_ms=total_ms,
            prompt_eval_count=data.get("prompt_eval_count"),
            eval_count=data.get("eval_count"),
        )

        logger.info(
            f"[GEN-END] rid={rid} model={result.model} elapsed={dt:.2f}s "
            f"text_len={len(result.text)} total_ms={result.total_duration_ms} "
            f"timeout_read={timeout_sec or self.timeout_sec}s"
        )
        return result

    # =========================
    # Health check
    # =========================
    def ping(self) -> bool:
        """
        Ollama 서버가 살아있는지 확인.
        - GET /api/tags
        """
        url = f"{self.base_url}/api/tags"
        rid = self._new_request_id()
        try:
            t0 = time.time()
            r = self._session.get(url, timeout=(self.connect_timeout_sec, 10))
            dt = time.time() - t0
            ok = r.status_code == 200
            logger.info(f"[PING] rid={rid} ok={ok} status={r.status_code} dt={dt:.2f}s")
            return ok
        except Exception as e:
            logger.warning(f"[PING] rid={rid} failed err={e}")
            return False
