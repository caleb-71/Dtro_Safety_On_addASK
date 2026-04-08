import time
import sys
import os

# 프로젝트 루트 경로 추가 (PYTHONPATH 설정과 동일 효과)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from backend.services.google_sheet_service import GoogleSheetService
from backend.services.qa_service import QAService
from core.logger import get_logger

logger = get_logger(__name__)


def run_worker():
    # 1. 서비스 초기화 (기존 QAService 재사용)
    gs_service = GoogleSheetService(
        json_key_path="config/google_keys.json",
        spreadsheet_name="DTRO_안전관리_모바일"
    )
    qa_service = QAService(auto_load_index=True)

    logger.info("👷 구글 시트 감시 로봇이 가동되었습니다.")

    while True:
        try:
            # 2. 구글 시트에서 질문 목록 확인 (10행부터 20행까지)
            rows = gs_service.get_mobile_questions("A10:C20")

            for i, row in enumerate(rows):
                # row[0]: 질문, row[2]: 답변 (값이 없으면 답변해야 함)
                if len(row) >= 1 and row[0]:  # 질문이 있으면
                    if len(row) < 3 or not row[2]:  # 아직 답변이 없다면
                        question = row[0]
                        logger.info(f"📱 모바일 질문 발견: {question}")

                        # 3. 기존 RAG 엔진 호출
                        result = qa_service.ask(question, dataset="unified")

                        # 4. 답변 기록 (A10이 시작이므로 i+10행)
                        gs_service.write_answer(i + 10, result.answer)
                        logger.info(f"✅ 답변 전송 완료 (행 번호: {i + 10})")

            time.sleep(10)  # 10초 대기
        except Exception as e:
            logger.error(f"❌ 워커 루프 에러: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_worker()