import gspread
import os
import ssl
import warnings
from datetime import datetime
from typing import Any, List, Dict, Optional
from core.logger import get_logger

# 1. 강력한 SSL 검증 비활성화 (보안망 환경 강제 대응)
# 파이썬 기본 SSL 컨텍스트 및 환경 변수 강제 설정
os.environ['PYTHONHTTPSVERIFY'] = '0'
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

try:
    # 전역 SSL 컨텍스트 우회
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    pass

logger = get_logger(__name__)


class GoogleSheetService:
    def __init__(self, json_key_path: str, spreadsheet_name: str):
        try:
            if not os.path.exists(json_key_path):
                raise FileNotFoundError(f"인증 키 파일 없음: {json_key_path}")

            # 2. gspread 인증 - 내부적으로 SSL 검증을 건너뛰도록 설정
            # service_account()는 내부적으로 요청을 보낼 때 위에서 설정한 전역 SSL 설정을 따릅니다.
            self.client = gspread.service_account(filename=json_key_path)

            # 3. 스프레드시트 열기
            self.sheet = self.client.open(spreadsheet_name)
            logger.info(f"[GoogleSheetService] 구글 시트 연결 성공: {spreadsheet_name}")

        except Exception as e:
            logger.error(f"[GoogleSheetService] 연결 실패: {str(e)}")
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                logger.error("💡 보안 인증서 차단 지속 발생. 로컬 환경의 강력한 보안 정책 때문입니다.")
            raise

    def update_dashboard_kpis(self, kpi_data: Dict[str, Any]):
        """현황 대시보드 데이터를 구글 시트 상단에 업데이트"""
        try:
            try:
                worksheet = self.sheet.worksheet("Dashboard")
            except:
                worksheet = self.sheet.get_worksheet(0)

            update_data = [
                ["최종 업데이트", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
                ["총 사고 건수", kpi_data.get("total_cnt", 0)],
                ["월평균 사고", round(kpi_data.get("monthly_avg", 0), 2)],
                ["최근 30일 증감률", f"{kpi_data.get('growth', 0):+.2f}%"]
            ]

            # 최신 gspread 문법 적용
            worksheet.update(values=update_data, range_name="A1:B4")
            logger.info("[GoogleSync] 모바일 대시보드 KPI 전송 성공")

        except Exception as e:
            logger.warning(f"[GoogleSync] KPI 업데이트 중 오류: {e}")

    def get_mobile_questions(self, range_name: str = "A10:C20") -> List[List[str]]:
        """Mobile_QA 탭 질문 가져오기"""
        try:
            worksheet = self.sheet.worksheet("Mobile_QA")
            return worksheet.get(range_name)
        except:
            return []

    def write_answer(self, row_idx: int, answer: str):
        """AI 답변 기록 (C열)"""
        try:
            worksheet = self.sheet.worksheet("Mobile_QA")
            worksheet.update_cell(row_idx, 3, answer)
            logger.info(f"[GoogleSync] {row_idx}행 답변 기록 완료")
        except Exception as e:
            logger.error(f"[GoogleSync] 답변 기록 실패: {e}")