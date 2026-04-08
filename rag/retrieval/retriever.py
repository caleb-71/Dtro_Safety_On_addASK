# rag/retrieval/retriever.py
"""
Retriever (DTRO-Safety-On)

역할
- 사용자 질문을 임베딩
- FAISS에서 Top-K 유사 청크 검색
- 검색 결과(점수 + 메타)를 반환

주의
- Retriever는 "인덱싱"을 절대 수행하지 않는다.
- 인덱스가 없으면 명확하게 예외/안내를 해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from core.config import get_settings
from core.logger import get_logger
from backend.integrations.ollama_client import OllamaClient
from rag.vectorstores.faiss_store import FaissVectorStore, ChunkMeta

logger = get_logger(__name__)


@dataclass
class RetrievedChunk:
    score: float
    meta: ChunkMeta


class Retriever:
    def __init__(
        self,
        store: Optional[FaissVectorStore] = None,
        client: Optional[OllamaClient] = None,
        top_k: Optional[int] = None,
    ):
        settings = get_settings()
        self.store = store or FaissVectorStore()
        self.client = client or OllamaClient()
        self.top_k = int(top_k or settings.rag.top_k)

    def load_index(self) -> None:
        """
        인덱스 로드(없으면 예외)
        """
        self.store.load()
        logger.info(f"[Retriever] index loaded ntotal={self.store.ntotal}")

    def retrieve(self, question: str, top_k: Optional[int] = None) -> List[RetrievedChunk]:
        """
        질문으로부터 Top-K 근거 청크를 검색
        """
        q = (question or "").strip()
        if not q:
            return []

        k = int(top_k or self.top_k)

        # 1) 질문 임베딩
        emb_res = self.client.embed(q)
        if not emb_res.embeddings or not emb_res.embeddings[0]:
            raise RuntimeError("Question embedding failed (empty embedding).")

        q_vec = emb_res.embeddings[0]

        # 2) FAISS 검색
        hits = self.store.search(query_embedding=q_vec, top_k=k)

        results: List[RetrievedChunk] = []
        for score, meta in hits:
            results.append(RetrievedChunk(score=float(score), meta=meta))

        logger.info(f"[Retriever] retrieve done k={k} hits={len(results)}")
        return results
