# rag/graph/extractor.py
import os
import pandas as pd
from pathlib import Path
from backend.integrations.ollama_client import OllamaClient
from core.logger import get_logger

logger = get_logger(__name__)


class GraphExtractor:
    def __init__(self):
        # 관리자님의 OllamaClient 활용
        self.client = OllamaClient()

        # 프로젝트 루트 기준으로 data 폴더 경로 설정
        self.base_dir = Path(__file__).resolve().parent.parent.parent
        self.csv_dir = self.base_dir / "data" / "csv"

    def extract_from_text(self, text: str) -> str:
        """
        Ollama를 이용해 텍스트에서 [주체 -> 관계 -> 대상] 형태의 지식을 추출합니다.
        """
        prompt = f"""
        당신은 산업안전 데이터 분석 전문가입니다.
        다음 데이터를 읽고, 주요 객체 간의 관계를 추출하세요.

        [규칙]
        1. 반드시 "A | 관계 | B" 형태로만 한 줄씩 출력할 것. 부연 설명 금지.
        2. A와 B는 장소, 설비명, 사고유형, 원인, 대책 중 하나여야 합니다.

        [기록]
        {text}

        [출력 예시]
        에스컬레이터 | 사고유형 | 넘어짐
        넘어짐 | 원인 | 중심잃음
        """

        # 로컬 AI 모델 호출
        response = self.client.generate(prompt=prompt)
        return response

    def test_extraction(self, file_name: str, limit: int = 3):
        """
        지정된 CSV 파일을 읽어 상위 limit 개수만큼 테스트 추출을 진행합니다.
        """
        csv_path = self.csv_dir / file_name
        if not csv_path.exists():
            logger.error(f"❌ CSV 파일을 찾을 수 없습니다: {csv_path}")
            return

        logger.info(f"데이터 로딩 중: {file_name}")
        df = pd.read_csv(csv_path, encoding='utf-8')

        print(f"\n=== 🚀 Graph-RAG 관계 추출 테스트: {file_name} ===\n")

        for index, row in df.head(limit).iterrows():
            # 💡 파일 종류에 따라 읽어오는 컬럼을 다르게 설정합니다.
            if file_name == "safety_map.csv":
                place = row.get('장소2', '알수없음')
                issue = row.get('지적유형', '알수없음')
                action = row.get('조치결과내용', '내용없음')
                text_to_analyze = f"장소: {place}, 문제유형: {issue}, 조치내용: {action}"

            elif file_name == "trend.csv":
                place = row.get('발생장소', '알수없음')
                type_ = row.get('사고유형', '알수없음')
                cause = row.get('사고원인', '알수없음')
                overview = row.get('사고개황', '내용없음')
                text_to_analyze = f"장소: {place}, 사고유형: {type_}, 원인: {cause}, 개황: {overview}"

            else:
                text_to_analyze = str(row.to_dict())

            print(f"[{index + 1}번 원본 요약] {text_to_analyze}")

            # Ollama를 통한 관계 추출
            extracted_relation = self.extract_from_text(text_to_analyze)
            print(f"👉 [AI 추출 결과]\n{extracted_relation}\n")
            print("-" * 60)


# 파이참에서 단독 실행 시 테스트
if __name__ == "__main__":
    extractor = GraphExtractor()

    # 1. 안전지도점검 데이터 테스트 (3줄)
    extractor.test_extraction(file_name="safety_map.csv", limit=3)

    # 2. 동향보고(사고) 데이터 테스트 (3줄)
    extractor.test_extraction(file_name="trend.csv", limit=3)