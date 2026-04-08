# apps/streamlit/ui/components/tables.py
from __future__ import annotations
import pandas as pd
import streamlit as st


def heatmap_table(
    df: pd.DataFrame,
    *,
    caption: str | None = None,
    height: int = 420,
):
    """
    히트맵 스타일 피벗 테이블
    - PDF에 쓰는 pivot 결과 그대로 재사용
    """
    if df is None or df.empty:
        st.info("표시할 데이터가 없습니다.")
        return

    try:
        styled = (
            df.style
            .background_gradient(cmap="YlOrRd", axis=None)
            .format(precision=0)
        )
        st.dataframe(styled, use_container_width=True, height=height)
    except Exception:
        # fallback
        st.dataframe(df, use_container_width=True, height=height)

    if caption:
        st.caption(caption)
