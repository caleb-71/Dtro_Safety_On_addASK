# apps/streamlit/ui/components/charts.py
from __future__ import annotations

import pandas as pd
import streamlit as st
import altair as alt

# theme token(있으면 사용, 없으면 fallback)
try:
    from apps.streamlit.ui.theme.tokens import TOKENS  # type: ignore
except Exception:
    TOKENS = {
        "primary": "#7C5CFF",
        "secondary": "#00D4FF",
        "ok": "#16A34A",
        "warn": "#F59E0B",
        "danger": "#EF4444",
        "info": "#3B82F6",
        "critical": "#A855F7",
        "card_muted": "rgba(15,23,42,0.60)",
    }


# ------------------------------------------------------------
# 내부 유틸
# ------------------------------------------------------------
def _to_chart_df(series: pd.Series, *, top_n: int | None = None) -> pd.DataFrame:
    """
    value_counts() 같은 Series -> (label, value) DataFrame
    """
    if series is None or len(series) == 0:
        return pd.DataFrame(columns=["label", "value"])

    s = series.copy()
    s.index = s.index.astype(str)
    s = s.sort_values(ascending=False)

    if top_n:
        s = s.head(top_n)

    df = s.reset_index()
    df.columns = ["label", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    return df


def _rank_color_expr() -> str:
    """
    Top rank 기반 색상 규칙(Bar)
    """
    return (
        "datum.rank == 1 ? '#7C5CFF' : "
        "datum.rank == 2 ? '#00D4FF' : "
        "datum.rank == 3 ? '#22C55E' : "
        "datum.rank <= 8 ? '#FFB020' : "
        "'#94A3B8'"
    )


def _base_card(title: str, subtitle: str | None = None):
    st.markdown("<div class='dtro-card'>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-weight:950; font-size:16px; letter-spacing:-0.2px;'>{title}</div>",
        unsafe_allow_html=True,
    )
    if subtitle:
        st.markdown(
            f"<div style='color: rgba(15,23,42,0.65); font-size:12px; margin-top:2px;'>{subtitle}</div>",
            unsafe_allow_html=True,
        )


def _end_card():
    st.markdown("</div>", unsafe_allow_html=True)


def _pivot_to_long(pivot_df: pd.DataFrame) -> pd.DataFrame:
    """
    pivot(행 index, 열 columns) 형태를 long(row, col, value)로 변환.
    - index/columns는 문자열로 통일
    - value는 숫자 변환
    """
    if pivot_df is None or pivot_df.empty:
        return pd.DataFrame(columns=["row", "col", "value"])

    tmp = pivot_df.copy()

    # index / columns -> 문자열
    tmp.index = tmp.index.astype(str)
    tmp.columns = [str(c) for c in tmp.columns]

    long_df = tmp.reset_index().melt(id_vars=[tmp.index.name or "index"], var_name="col", value_name="value")
    long_df.rename(columns={tmp.index.name or "index": "row"}, inplace=True)

    long_df["row"] = long_df["row"].astype(str)
    long_df["col"] = long_df["col"].astype(str)
    long_df["value"] = pd.to_numeric(long_df["value"], errors="coerce").fillna(0)

    return long_df


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------
def bar_chart_card(
    title: str,
    series: pd.Series,
    height: int = 280,
    top_n: int | None = None,
    *,
    subtitle: str | None = None,
    show_values: bool = True,
):
    _base_card(title, subtitle=subtitle)

    df = _to_chart_df(series, top_n=top_n)
    if df.empty:
        st.info("표시할 데이터가 없습니다.")
        _end_card()
        return

    df["rank"] = range(1, len(df) + 1)
    df["label_short"] = df["label"].apply(lambda x: (x[:18] + "…") if len(x) > 18 else x)

    base = (
        alt.Chart(df)
        .transform_calculate(color=_rank_color_expr())
        .properties(height=height)
    )

    bars = base.mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6).encode(
        y=alt.Y(
            "label_short:N",
            sort=alt.SortField(field="value", order="descending"),
            title=None,
            axis=alt.Axis(labelLimit=220),
        ),
        x=alt.X("value:Q", title=None),
        color=alt.Color("color:N", scale=None, legend=None),
        tooltip=[
            alt.Tooltip("label:N", title="항목"),
            alt.Tooltip("value:Q", title="값"),
            alt.Tooltip("rank:Q", title="순위"),
        ],
    )

    if show_values:
        text = base.mark_text(
            align="left",
            baseline="middle",
            dx=6,
            fontWeight=800,
        ).encode(
            y=alt.Y("label_short:N", sort=alt.SortField(field="value", order="descending")),
            x=alt.X("value:Q"),
            text=alt.Text("value:Q"),
            tooltip=[
                alt.Tooltip("label:N", title="항목"),
                alt.Tooltip("value:Q", title="값"),
            ],
        )
        chart = (bars + text)
    else:
        chart = bars

    chart = chart.configure_view(stroke=None).configure_axis(
        grid=False,
        labelFontSize=12,
        titleFontSize=12,
    )

    st.altair_chart(chart, use_container_width=True)
    _end_card()


def line_chart_card(
    title: str,
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    height: int = 280,
    *,
    subtitle: str | None = None,
    smooth: bool = False,
):
    _base_card(title, subtitle=subtitle)

    if df is None or df.empty or x_col not in df.columns or y_col not in df.columns:
        st.info("표시할 데이터가 없습니다.")
        _end_card()
        return

    cdf = df[[x_col, y_col]].dropna().copy()
    cdf[x_col] = pd.to_datetime(cdf[x_col], errors="ignore")
    cdf[y_col] = pd.to_numeric(cdf[y_col], errors="coerce")
    cdf = cdf.dropna().sort_values(x_col)

    if cdf.empty:
        st.info("표시할 데이터가 없습니다.")
        _end_card()
        return

    line_color = TOKENS.get("secondary", "#00D4FF")
    point_color = TOKENS.get("primary", "#7C5CFF")

    line = (
        alt.Chart(cdf)
        .mark_line(color=line_color, strokeWidth=3)
        .encode(
            x=alt.X(f"{x_col}:T", title=None),
            y=alt.Y(f"{y_col}:Q", title=None),
            tooltip=[alt.Tooltip(f"{x_col}:T", title="X"), alt.Tooltip(f"{y_col}:Q", title="Y")],
        )
        .properties(height=height)
    )

    points = (
        alt.Chart(cdf)
        .mark_circle(color=point_color, size=55, opacity=0.85)
        .encode(
            x=alt.X(f"{x_col}:T"),
            y=alt.Y(f"{y_col}:Q"),
            tooltip=[alt.Tooltip(f"{x_col}:T", title="X"), alt.Tooltip(f"{y_col}:Q", title="Y")],
        )
    )

    chart = (line + points).configure_view(stroke=None).configure_axis(
        grid=False,
        labelFontSize=12,
        titleFontSize=12,
    )

    st.altair_chart(chart, use_container_width=True)
    _end_card()


def heatmap_chart_card(
    title: str,
    pivot_df: pd.DataFrame,
    *,
    subtitle: str | None = None,
    height: int = 360,
    max_row_labels: int = 18,
    max_col_labels: int = 18,
):
    """
    피벗(행x열) 데이터프레임을 Altair Heatmap으로 렌더링하는 카드

    - pivot_df: index=행 라벨, columns=열 라벨, values=카운트/점수
    - 너무 라벨이 길면 글자 크기/라벨 제한을 조절
    """
    _base_card(title, subtitle=subtitle)

    if pivot_df is None or pivot_df.empty:
        st.info("표시할 데이터가 없습니다.")
        _end_card()
        return

    long_df = _pivot_to_long(pivot_df)
    if long_df.empty:
        st.info("표시할 데이터가 없습니다.")
        _end_card()
        return

    # 라벨이 너무 많으면 보기 어려우니 안전장치(서비스에서 top_n 주지만, 혹시 대비)
    row_uni = long_df["row"].unique().tolist()
    col_uni = long_df["col"].unique().tolist()
    if len(row_uni) > max_row_labels or len(col_uni) > max_col_labels:
        st.caption(
            f"ℹ️ 라벨이 많아 가독성이 떨어질 수 있어요. "
            f"(row={len(row_uni)}, col={len(col_uni)}). top_n을 줄이면 더 미려합니다."
        )

    # 값 범위
    vmin = float(long_df["value"].min())
    vmax = float(long_df["value"].max())

    # 색상 스케일(깔끔한 블루 톤)
    # - low/high를 토큰 기반으로 잡아서 테마 일관성 유지
    low = "#EEF2FF"  # 연한 라벤더(배경과 자연)
    high = TOKENS.get("primary", "#7C5CFF")

    base = alt.Chart(long_df).properties(height=height)

    rect = base.mark_rect(
        cornerRadius=4,
        stroke="rgba(15,23,42,0.10)",
        strokeWidth=1,
    ).encode(
        x=alt.X(
            "col:N",
            title=None,
            sort=col_uni,  # pivot 순서를 유지
            axis=alt.Axis(labelAngle=0, labelLimit=220),
        ),
        y=alt.Y(
            "row:N",
            title=None,
            sort=row_uni,  # pivot 순서를 유지
            axis=alt.Axis(labelLimit=220),
        ),
        color=alt.Color(
            "value:Q",
            scale=alt.Scale(domain=[vmin, vmax], range=[low, high]),
            legend=alt.Legend(title="값", orient="right"),
        ),
        tooltip=[
            alt.Tooltip("row:N", title="행"),
            alt.Tooltip("col:N", title="열"),
            alt.Tooltip("value:Q", title="값"),
        ],
    )

    # 값 텍스트(셀에 숫자 박기) - 너무 촘촘하면 지저분해져서 옵션처럼 가볍게
    # 기본은 OFF에 가깝게: vmax가 작고 셀이 적으면 자동으로 표시
    show_text = (len(row_uni) <= 12 and len(col_uni) <= 12)
    if show_text:
        text = base.mark_text(fontWeight=900, fontSize=11).encode(
            x=alt.X("col:N", sort=col_uni),
            y=alt.Y("row:N", sort=row_uni),
            text=alt.Text("value:Q"),
        )
        chart = rect + text
    else:
        chart = rect

    chart = chart.configure_view(stroke=None).configure_axis(
        grid=False,
        labelFontSize=11,
        titleFontSize=12,
    ).configure_legend(
        labelFontSize=11,
        titleFontSize=12,
    )

    st.altair_chart(chart, use_container_width=True)
    _end_card()


def empty_chart_hint(msg: str = "표시할 데이터가 없습니다."):
    st.info(msg)
