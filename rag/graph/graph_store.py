# rag/graph/graph_store.py
import json
import networkx as nx
from pathlib import Path
from core.logger import get_logger

logger = get_logger(__name__)


class GraphStore:
    def __init__(self):
        # 1. 빈 지도(그래프) 생성
        self.graph = nx.Graph()

        # 2. 저장할 파일 경로 설정 (기존 FAISS 인덱스 폴더 활용)
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.save_dir = base_dir / "data" / "index" / "graph"
        self.save_dir.mkdir(parents=True, exist_ok=True)  # 폴더가 없으면 생성

        self.save_path = self.save_dir / "safety_knowledge.json"

    def add_relation(self, source: str, relation: str, target: str):
        """지도에 점(Node)과 선(Edge)을 추가합니다."""
        # 양쪽 공백 제거
        src = source.strip()
        tgt = target.strip()
        rel = relation.strip()

        # 그래프에 연결 고리 추가
        self.graph.add_edge(src, tgt, relation=rel)

    def save_graph(self):
        """현재 그려진 지도를 JSON 파일로 영구 저장합니다."""
        # NetworkX 그래프를 딕셔너리 형태로 변환
        data = nx.node_link_data(self.graph)

        # JSON 파일로 저장
        with open(self.save_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"✅ 지식 지도가 성공적으로 저장되었습니다: {self.save_path}")
        print(f"[저장 완료] 총 {self.graph.number_of_nodes()}개의 핵심 단어와 {self.graph.number_of_edges()}개의 관계가 저장되었습니다.")

    def load_graph(self):
        """저장된 JSON 파일에서 지식 지도를 불러옵니다."""
        if self.save_path.exists():
            with open(self.save_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.graph = nx.node_link_graph(data)
            logger.info(f"📂 지식 지도를 불러왔습니다: {self.save_path}")
        else:
            logger.warning("저장된 지식 지도가 없습니다. 새로 생성합니다.")

    def search_context(self, keyword: str) -> str:
        """질문이 들어오면 키워드 주변의 연결된 지식을 찾아 반환합니다."""
        if keyword in self.graph:
            # 키워드와 직접 연결된 이웃들을 찾음
            neighbors = self.graph.edges(keyword, data=True)
            context_lines = []
            for src, tgt, data in neighbors:
                context_lines.append(f"{src} --({data['relation']})--> {tgt}")
            return "\n".join(context_lines)
        return "해당 키워드와 관련된 인과관계 데이터가 지식 지도에 없습니다."