# backend/services/graph_qa_service.py
from rag.graph.graph_store import GraphStore
from backend.integrations.ollama_client import OllamaClient
from core.logger import get_logger

# ✅ [신규 추가] 규정(PDF) 검색을 위해 Vector Store 모듈과 경로를 불러옵니다.
from rag.vectorstores.faiss_store import FaissVectorStore
from rag.paths import INDEX_DIR

logger = get_logger(__name__)


class GraphQAService:
    def __init__(self):
        self.client = OllamaClient()

        # 1. 지식 지도(Graph) 로드 (사고/점검 데이터)
        self.graph_store = GraphStore()
        self.graph_store.load_graph()

        # ✅ 2. 규정/법령(Vector) 로드 (PDF 문서 데이터)
        self.vector_store = FaissVectorStore(index_dir=INDEX_DIR)
        try:
            self.vector_store.load()
            self.vector_loaded = True
            logger.info("GraphQAService: Vector DB (규정) 로드 성공")
        except Exception as e:
            self.vector_loaded = False
            logger.warning(f"GraphQAService: Vector DB 로드 실패 (규정 검색 생략됨): {e}")

        logger.info("GraphQAService 초기화 완료 (Graph + Vector 하이브리드 모드)")

    # 자연어에서 핵심 단어만 쏙쏙 뽑아내는 지능형 함수
    def extract_keywords_from_nl(self, nl_text: str) -> list:
        prompt = f"""
        당신은 텍스트 분석가입니다.
        사용자의 질문에서 '장소', '설비명', '사고유형', '원인'과 관련된 가장 중요한 명사(키워드)를 최대 3개만 추출하세요.
        반드시 쉼표(,)로만 구분해서 대답하고 다른 말은 절대 하지 마세요.

        사용자 입력: "{nl_text}"
        """
        response = self.client.generate(prompt=prompt)
        text_output = response.text if hasattr(response, 'text') else str(response)
        keywords = [k.strip() for k in text_output.replace('"', '').split(',')]
        logger.info(f"🧠 AI가 자연어에서 추출한 키워드: {keywords}")
        return keywords

    def ask(self, question: str, keyword: str) -> str:
        logger.info(f"하이브리드 RAG 질문 접수: {question} (입력된 키워드/문장: {keyword})")

        # 1. 키워드 추출 (자연어 처리)
        if " " in keyword or len(keyword) > 10:
            search_keywords = self.extract_keywords_from_nl(keyword)
        else:
            search_keywords = [keyword]

        # ---------------------------------------------------------
        # 🔎 [트랙 1] Graph DB 검색 (과거 사고 및 점검 원인 찾기)
        # ---------------------------------------------------------
        combined_graph_context = []
        for k in search_keywords:
            context = self.graph_store.search_context(k)
            if "데이터가 지식 지도에 없습니다" not in context:
                combined_graph_context.append(f"[{k} 주변 사고/점검 이력]\n{context}")

        # [수정] 아무것도 못 찾았더라도 바로 종료하지 않고, Vector 검색으로 넘어갑니다.
        final_graph_context = "\n\n".join(
            combined_graph_context) if combined_graph_context else "관련된 사고/점검 이력이 지식 지도에 없습니다. (과거 유사 사례 없음)"

        # ---------------------------------------------------------
        # 🔎 [트랙 2] Vector DB 검색 (관련 법령 및 규정 찾기)
        # ---------------------------------------------------------
        final_vector_context = "관련 규정을 찾을 수 없습니다."
        if self.vector_loaded:
            # 원인 분석을 위한 규정 검색용 쿼리 생성
            search_query = " ".join(search_keywords) + " 관련 안전 규정, 예방 대책, 지침"
            try:
                # 시스템마다 Faiss 검색 함수 이름이 다를 수 있어 안전하게 분기 처리
                results = []
                if hasattr(self.vector_store, 'search'):
                    results = self.vector_store.search(search_query, top_k=3)
                elif hasattr(self.vector_store, 'similarity_search'):
                    results = self.vector_store.similarity_search(search_query, k=3)

                vector_texts = []
                for res in results:
                    # 결과 객체의 형태(dict 또는 class)에 구애받지 않고 텍스트 추출
                    content = res.get('text', '') if isinstance(res, dict) else getattr(res, 'page_content', str(res))
                    meta = res.get('metadata', {}) if isinstance(res, dict) else getattr(res, 'metadata', {})
                    source = meta.get('source', '문서(출처 미상)')

                    vector_texts.append(f"- 출처: {source}\n  내용: {content[:300]}...")  # 길어짐 방지

                if vector_texts:
                    final_vector_context = "\n\n".join(vector_texts)
            except Exception as e:
                logger.error(f"Vector DB 검색 실패: {e}")

        # ---------------------------------------------------------
        # 🧠 [트랙 3] AI 최종 통합 브리핑 (할루시네이션 원천 차단 프롬프트)
        # ---------------------------------------------------------
        prompt = f"""
        당신은 대구교통공사(DTRO)의 최고 안전 분석가입니다.
        아래 [자료 1]과 [자료 2]의 내용을 **절대 섞지 말고, 명확히 구분하여** 질문에 답변하세요.
        추측이나 지어낸 내용은 절대 쓰지 마세요.

        [자료 1: 과거 사고/점검 주요 원인 (Graph 데이터)]
        {final_graph_context}

        [자료 2: 관련 안전 규정 및 예방 대책 (Vector 데이터)]
        {final_vector_context}

        [관리자님의 질문]
        {question}

        [답변 작성 지침 - 반드시 아래의 양식을 지킬 것]
        네, 관리자님. 하이브리드 안전 분석 결과입니다.

        **1. 과거 사고 및 지적 주요 원인**
        - (오직 [자료 1]의 내용만 요약하여 작성. 만약 '사례 없음' 이면 "과거 유사 사례가 없습니다." 라고 명시할 것)

        **2. 관련 규정 및 예방 대책**
        - (오직 [자료 2]의 내용만 요약하여 공식적인 대책으로 제시할 것. 출처가 있다면 간단히 언급할 것)
        """

        response = self.client.generate(prompt=prompt)
        final_answer = response.text if hasattr(response, 'text') else str(response)

        return final_answer.strip()