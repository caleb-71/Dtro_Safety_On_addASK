# core/logger.py
"""
DTRO-Safety-On 로거 설정

- config/logging.yaml 로깅 설정을 읽어 적용
- filename(로그 파일 경로)은 settings.paths.logs_dir 기준 "절대경로"로 강제 주입
  (Streamlit 실행 위치/작업 디렉토리 흔들림 방지)
- 없거나 오류가 나면 안전하게 기본 로깅으로 fallback
"""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path
from typing import Optional, Any, Dict

import yaml

from core.config import get_project_root, get_settings


_LOGGING_CONFIG_APPLIED = False


def _inject_absolute_log_path(cfg: Dict[str, Any], log_file_name: str = "dtro_safety_on.log") -> Dict[str, Any]:
    """
    logging.yaml의 file handler filename을 settings.paths.logs_dir 기반 절대경로로 교체
    """
    s = get_settings()
    logs_dir: Path = s.paths.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_path = logs_dir / log_file_name

    handlers = cfg.get("handlers", {})
    file_handler = handlers.get("file", None)

    if isinstance(file_handler, dict):
        # ✅ filename 강제 주입 (상대경로 문제 제거)
        file_handler["filename"] = str(log_path)

        # ✅ 권장 옵션: delay=True (프로세스 시작 시 파일 핸들 바로 잡지 않아도 됨)
        # streamlit rerun/재시작 상황에서 조금 더 안정적
        file_handler.setdefault("delay", True)

        # ✅ encoding 기본값 보강
        file_handler.setdefault("encoding", "utf-8")

    return cfg


def setup_logging(force: bool = False) -> None:
    """
    logging.yaml 기반 로깅 설정 적용
    - force=True면 재적용
    """
    global _LOGGING_CONFIG_APPLIED
    if _LOGGING_CONFIG_APPLIED and not force:
        return

    base_dir = get_project_root()
    cfg_path = base_dir / "config" / "logging.yaml"

    try:
        if cfg_path.exists():
            # logs_dir가 먼저 생성돼 있어야 file handler가 실패하지 않음
            settings = get_settings()
            settings.paths.logs_dir.mkdir(parents=True, exist_ok=True)

            with cfg_path.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            if not isinstance(cfg, dict):
                raise ValueError("logging.yaml must be a YAML mapping(dict).")

            # ✅ filename을 절대경로로 강제 주입
            cfg = _inject_absolute_log_path(cfg, log_file_name="dtro_safety_on.log")

            logging.config.dictConfig(cfg)

        else:
            # fallback
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            )

        _LOGGING_CONFIG_APPLIED = True

    except Exception as e:
        # 어떤 이유로든 로깅 설정이 실패하면 fallback
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )
        logging.getLogger(__name__).warning(f"logging.yaml apply failed -> fallback. reason={e}")
        _LOGGING_CONFIG_APPLIED = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    공통 로거 획득
    """
    setup_logging()
    return logging.getLogger(name if name else "DTRO-Safety-On")
