# report/renderers/font_utils.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from core.config import get_settings
from core.logger import get_logger

logger = get_logger(__name__)


def register_korean_font() -> str:
    """
    ReportLab에 한글 폰트를 등록하고, 등록된 font_name을 반환한다.

    우선순위(중요):
      1) 프로젝트 폰트: KoPub(업로드한 3종) -> Noto -> Nanum
      2) Windows 폰트 fallback (가능하면)
    """
    settings = get_settings()

    # ReportLab에서 사용할 '등록 이름' (고정)
    font_name = "DTRO_KR"

    # 이미 등록돼 있으면 재등록하지 않고 그대로 반환
    try:
        pdfmetrics.getFont(font_name)
        return font_name
    except Exception:
        pass

    # 프로젝트 폰트 폴더
    font_dir = settings.paths.base_dir / "report" / "pdf_assets" / "fonts"

    # ✅ 업로드한 KoPub 파일명 그대로 1순위로 둔다 (Medium -> Light -> Bold)
    candidates = [
        font_dir / "KOPUB BATANG MEDIUM.TTF",
        font_dir / "KOPUB BATANG LIGHT.TTF",
        font_dir / "KOPUB BATANG BOLD.TTF",
        # 다음 후보(있으면 사용)
        font_dir / "NotoSansKR-Regular.ttf",
        font_dir / "NanumGothic.ttf",
    ]

    # Windows fallback (최후)
    candidates += [
        Path(r"C:\Windows\Fonts\malgun.ttf"),
        Path(r"C:\Windows\Fonts\malgunsl.ttf"),
    ]

    chosen: Optional[Path] = None
    for p in candidates:
        if p.exists():
            chosen = p
            break

    if not chosen:
        raise FileNotFoundError(
            "한글 폰트 파일을 찾지 못했습니다.\n"
            f"- 확인 경로: {font_dir}\n"
            "- 해결: report/pdf_assets/fonts 아래에 KoPub .TTF를 넣으세요."
        )

    # 등록
    pdfmetrics.registerFont(TTFont(font_name, str(chosen)))
    logger.info(f"[PDF] Korean font registered name={font_name} path={chosen}")
    return font_name
