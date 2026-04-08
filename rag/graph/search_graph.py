# rag/graph/search_graph.py
from rag.graph.graph_store import GraphStore


# 함수 이름을 test_search 에서 run_search 로 변경했습니다!
def run_search():
    store = GraphStore()

    # 1. 저장된 safety_knowledge.json 불러오기
    store.load_graph()

    print("\n" + "=" * 50)
    print(" 🌐 DTRO 지능형 안전망 (Graph-RAG) 검색 테스트")
    print("=" * 50)

    while True:
        # 2. 검색어 입력받기
        keyword = input("\n검색할 키워드를 입력하세요 (종료하려면 'q' 입력) \n👉 (예: 에스컬레이터, 문양기지, 넘어짐): ")

        if keyword.lower() == 'q':
            print("검색을 종료합니다. 안전!")
            break

        print(f"\n🔍 '{keyword}' 주변의 연결된 지식을 탐색합니다...\n")

        # 3. 지도에서 키워드 주변 정보 찾기
        result = store.search_context(keyword.strip())

        # 4. 결과 출력
        print(result)
        print("-" * 50)


if __name__ == "__main__":
    # 아래 실행 부분도 run_search() 로 변경했습니다!
    run_search()