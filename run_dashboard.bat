@echo off
chcp 65001 > nul
cd /d %~dp0
set PYTHONPATH=%cd%

:: 가상환경 활성화
call .venv\Scripts\activate

:: SSL 보안 검증 강제 비활성화 (환경 변수 설정)
set PYTHONHTTPSVERIFY=0
set CURL_CA_BUNDLE=
set SSL_CERT_FILE=

:: 1. 구글 시트 감시 워커를 백그라운드에서 실행
start /b python backend/workers/jobs/google_sync_worker.py

:: 2. 기존 스트림릿 대시보드 실행
streamlit run apps\streamlit\Home.py

pause