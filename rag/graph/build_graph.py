# rag/graph/build_graph.py
import pandas as pd
from pathlib import Path
from rag.graph.extractor import GraphExtractor
from rag.graph.graph_store import GraphStore
from core.logger import get_logger

logger = get_logger(__name__)


def build_knowledge_graph(limit=10):
    """CSV 데이터를 읽어서 지식 지도를 구축하고 저장합니다."""
    extractor = GraphExtractor()
    store = GraphStore()

    files_to_process = ["safety_map.csv", "trend.csv"]
    print(f"🚀 [지식 지도 구축 시작] 최대 {limit}개의 데이터를 처리합니다...\n")

    for file_name in files_to_process:
        csv_path = extractor.csv_dir / file_name
        if not csv_path.exists():
            logger.warning(f"건너뜀: {file_name} 파일을 찾을 수 없습니다.")
            continue

        df = pd.read_csv(csv_path, encoding='utf-8').head(limit)

        for index, row in df.iterrows():
            # 1. 텍스트 준비
            if file_name == "safety_map.csv":
                text = f"장소: {row.get('장소2', '')}, 조치내용: {row.get('조치결과내용', '')}"
            else:
                text = f"장소: {row.get('발생장소', '')}, 사고유형: {row.get('사고유형', '')}, 원인: {row.get('사고원인', '')}"

            # 2. AI로 관계 추출
            extracted = extractor.extract_from_text(text)

            # --- [이 부분이 수정되었습니다!] ---
            # 포장 상자(OllamaGenerateResult)에서 실제 텍스트 알맹이만 꺼냅니다.
            if hasattr(extracted, 'text'):
                extracted_str = extracted.text
            elif hasattr(extracted, 'response'):
                extracted_str = extracted.response
            else:
                extracted_str = str(extracted)
            # -----------------------------------

            # 3. 추출된 결과를 분석하여 저장소에 넣기
            # 이제 객체가 아닌 텍스트(extracted_str)를 자릅니다.
            lines = extracted_str.strip().split('\n')
            for line in lines:
                parts = line.split('|')
                if len(parts) == 3:
                    source, relation, target = parts
                    store.add_relation(source, relation, target)
                    print(f"✅ 연결 추가됨: {source.strip()} --({relation.strip()})--> {target.strip()}")

    # 4. 최종 그래프 저장
    print("\n💾 데이터 추출 완료. 파일로 저장합니다...")
    store.save_graph()


if __name__ == "__main__":
    build_knowledge_graph(limit=10)