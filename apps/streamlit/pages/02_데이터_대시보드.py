# apps/streamlit/pages/02_데이터_대시보드.py
from __future__ import annotations

from apps.streamlit._bootstrap import ensure_project_root_on_syspath
ensure_project_root_on_syspath()


import datetime as dt
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import streamlit as st

from core.config import get_settings
from core.logger import get_logger
from shared.io.csv_loader import load_csv

# UI modules
from apps.streamlit.ui.theme.layout import apply_layout, page_container, end_page, section, hero
from apps.streamlit.ui.components import kpi_cards
from apps.streamlit.ui.components.filters import dashboard_filters
from apps.streamlit.ui.components.charts import bar_chart_card

kpi_card = kpi_cards.kpi_card

logger = get_logger(__name__)
settings = get_settings()

# --------------------------------------------------
# 페이지 설정
# --------------------------------------------------
st.set_page_config(page_title="데이터 대시보드", layout="wide")

apply_layout()
page_container()

hero(
    "🧾 02. 데이터 대시보드",
    "trend / safety_map 데이터를 탐색하고, 품질·운영지표(지연/반복지적 등)를 확인합니다. (필터→KPI→분포→테이블→다운로드)",
)

# --------------------------------------------------
# 유틸
# --------------------------------------------------
def _to_dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def _pct(n: int, d: int, digits: int = 1) -> str:
    if d <= 0:
        return "-"
    return f"{round((n / d) * 100, digits)}%"


def _safe_series(df: pd.DataFrame, col: str, default=None) -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df))


def _clean_key_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().replace({"nan": "미상", "None": "미상", "": "미상"})


@st.cache_data(show_spinner=False)
def _load_df(path: Path, mtime: float) -> pd.DataFrame:
    res = load_csv(path)
    df = res.df.copy()
    # 컬럼명 정리(엑셀 변환 시 줄바꿈 섞이는 경우 방어)
    df.columns = [str(c).replace("\n", "").replace("\r", "").strip() for c in df.columns]
    return df


def _download_button(df: pd.DataFrame, filename: str, label: str):
    csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label=label,
        data=csv_bytes,
        file_name=filename,
        mime="text/csv",
        width="stretch",  # ✅ use_container_width → width
    )


def _repeat_findings(
    df: pd.DataFrame,
    *,
    group_cols: List[str],
    n: int,
) -> Tuple[pd.DataFrame, int, int, pd.DataFrame]:
    """
    반복지적 계산
    반환:
      - repeat_groups: group_cols + cnt (cnt>=n, 내림차순)
      - repeat_group_cnt: 반복지적 그룹 수
      - repeat_record_cnt: 반복지적에 포함되는 원본 레코드 수
      - tmp: 내부 계산용(드릴다운용 _grp_key 포함)
    """
    if df.empty:
        return pd.DataFrame(), 0, 0, df

    tmp = df.copy()

    for c in group_cols:
        if c not in tmp.columns:
            tmp[c] = "미상"
        tmp[c] = _clean_key_series(tmp[c])

    g = tmp.groupby(group_cols, dropna=False).size().reset_index(name="cnt")
    repeat_groups = g[g["cnt"] >= n].sort_values("cnt", ascending=False)

    repeat_group_cnt = int(len(repeat_groups))
    if repeat_group_cnt > 0:
        tmp["_grp_key"] = list(zip(*[tmp[c] for c in group_cols]))
        repeat_groups["_grp_key"] = list(zip(*[repeat_groups[c] for c in group_cols]))
        keyset = set(repeat_groups["_grp_key"].tolist())
        repeat_record_cnt = int(tmp["_grp_key"].isin(keyset).sum())
    else:
        tmp["_grp_key"] = None
        repeat_record_cnt = 0

    return repeat_groups, repeat_group_cnt, repeat_record_cnt, tmp


def _format_repeat_label(row: dict, group_cols: List[str]) -> str:
    parts = [str(row.get(c, "-")) for c in group_cols]
    return " / ".join(parts)


def _parse_repeat_label(label: str, group_cols: List[str]) -> Tuple[str, ...] | None:
    """
    label = "A / B / C" -> ("A","B","C")
    label 안에 '/'가 섞여있으면 완벽히 안전하진 않지만,
    우리 데이터(장소/유형)는 보통 '/'를 쓰지 않는다는 전제로 간단 파싱.
    """
    parts = [p.strip() for p in label.split("/")]
    parts = [p.strip() for p in parts]
    if len(parts) != len(group_cols):
        return None
    return tuple(parts)


# --------------------------------------------------
# 데이터 로드
# --------------------------------------------------
trend_path = settings.paths.data_dir / "csv" / "trend.csv"
smap_path = settings.paths.data_dir / "csv" / "safety_map.csv"

with st.spinner("데이터 로딩 중..."):
    trend_df = pd.DataFrame()
    smap_df = pd.DataFrame()

    if trend_path.exists():
        trend_df = _load_df(trend_path, trend_path.stat().st_mtime)

    if smap_path.exists():
        smap_df = _load_df(smap_path, smap_path.stat().st_mtime)

if trend_df.empty and smap_df.empty:
    st.warning("trend.csv / safety_map.csv 데이터가 없습니다. data/csv 폴더를 확인하세요.")
    end_page()
    st.stop()

# --------------------------------------------------
# 탭 구성
# --------------------------------------------------
tab1, tab2 = st.tabs(["📈 사고/동향 (trend)", "🛠️ 안전지도/점검 (safety_map)"])

# ==================================================
# TAB 1) trend
# ==================================================
with tab1:
    if trend_df.empty:
        st.info("trend.csv 데이터가 없습니다.")
    else:
        tdf = trend_df.copy()

        # ✅ 표준 컬럼 매핑(발생일자/발생시간 → 일자/시간)
        date_src = "발생일자" if "발생일자" in tdf.columns else ("일자" if "일자" in tdf.columns else None)
        time_src = "발생시간" if "발생시간" in tdf.columns else ("시간" if "시간" in tdf.columns else None)

        if date_src is None:
            # 날짜 컬럼이 없으면 필터/집계를 최소화하고 안내
            st.warning("trend.csv에 날짜 컬럼(발생일자/일자)이 없습니다. CSV 헤더를 확인하세요.")
            tdf["일자"] = pd.NaT
        else:
            tdf["일자"] = _to_dt(_safe_series(tdf, date_src, pd.NaT))

        if time_src is None:
            tdf["시간"] = ""
        else:
            tdf["시간"] = _safe_series(tdf, time_src, "")

        # 나머지 탐색용 보강
        tdf["조치상태"] = _safe_series(tdf, "조치상태", "미상").fillna("미상")
        tdf["요일"] = _safe_series(tdf, "요일", "미상").fillna("미상")
        tdf["_sort_date"] = tdf["일자"].fillna(pd.Timestamp.min)

        section("필터 (trend)")
        ftdf = dashboard_filters(tdf, dataset="trend", key_prefix="dash06_trend")
        st.caption(f"필터 결과: {len(ftdf)}건")

        # 데이터가 0건이면 이유를 쉽게 확인할 수 있게 안내
        if ftdf.empty:
            st.info("현재 필터 조건에서 표시할 데이터가 없습니다. 날짜 기준/기간을 조정해 보세요.")
        else:
            # KPI
            section("KPI (trend)")
            total_cnt = len(ftdf)
            done_cnt = int((_safe_series(ftdf, "조치상태", "미상").astype(str) == "완료").sum())
            emergency_cnt = int((_safe_series(ftdf, "긴급신고", "").astype(str) == "119").sum())
            cctv_cnt = int(
                _safe_series(ftdf, "CCTV유무", "")
                .astype(str)
                .isin(["유", "예", "Y", "YES", "True", "TRUE", "1"])
                .sum()
            )
            latest_date = ftdf["일자"].max() if "일자" in ftdf.columns else pd.NaT

            k1, k2, k3, k4, k5 = st.columns(5)
            with k1:
                kpi_card("전체 건수", total_cnt, tone="info", chip="trend")
            with k2:
                kpi_card("조치 완료", done_cnt, _pct(done_cnt, total_cnt), tone="ok", chip="완료")
            with k3:
                kpi_card("119 신고", emergency_cnt, tone="critical", chip="비상")
            with k4:
                kpi_card("CCTV 확보", cctv_cnt, tone="info", chip="증거")
            with k5:
                kpi_card("최근 일자", latest_date.date() if pd.notna(latest_date) else "-", tone="info", chip="최신")

            # 분포
            section("주요 분포 (trend)")
            c1, c2 = st.columns(2)
            with c1:
                bar_chart_card(
                    title="사고유형 TOP 10",
                    series=_safe_series(ftdf, "사고유형", "미상").value_counts(),
                    top_n=10,
                )
            with c2:
                bar_chart_card(
                    title="호선별 발생 건수",
                    series=_safe_series(ftdf, "호선", "미상").value_counts(),
                )

            c3, c4 = st.columns(2)
            with c3:
                ordered = ["월", "화", "수", "목", "금", "토", "일"]
                series = _safe_series(ftdf, "요일", "미상").value_counts().reindex(ordered, fill_value=0)
                bar_chart_card(title="요일 분포", series=series)
            with c4:
                bar_chart_card(
                    title="조치상태 분포",
                    series=_safe_series(ftdf, "조치상태", "미상").value_counts(),
                )

            # 테이블
            section("원본 테이블 (trend)")
            preferred_cols = [
                "순번",
                "일자",
                "시간",
                "호선",
                "역명",
                "발생장소",
                "장소세부1",
                "장소세부2",
                "사고유형",
                "사고원인",
                "상세원인",
                "조치상태",
                "보고구분",
            ]
            list_cols = [c for c in preferred_cols if c in ftdf.columns]
            show_df = ftdf.sort_values("_sort_date", ascending=False)

            st.dataframe(
                show_df[list_cols] if list_cols else show_df,
                width="stretch",  # ✅ use_container_width → width
                height=380,
            )

            d1, _ = st.columns([1, 3])
            with d1:
                _download_button(
                    show_df.drop(columns=["_sort_date"], errors="ignore"),
                    "trend_filtered.csv",
                    "⬇️ trend 다운로드(필터 반영)",
                )

# ==================================================
# TAB 2) safety_map
# ==================================================
with tab2:
    if smap_df.empty:
        st.info("safety_map.csv 데이터가 없습니다.")
    else:
        sdf = smap_df.copy()

        # 날짜 파싱
        sdf["지도점검일자"] = _to_dt(_safe_series(sdf, "지도점검일자", pd.NaT))
        sdf["조치완료일자"] = _to_dt(_safe_series(sdf, "조치완료일자", pd.NaT))

        # 조치상태 파생(운영 표준)
        sdf["조치상태"] = sdf["조치완료일자"].apply(lambda x: "완료" if pd.notna(x) else "미조치")

        # 경과/소요일
        today = pd.Timestamp(dt.date.today())
        sdf["경과일수"] = (today - sdf["지도점검일자"]).dt.days
        sdf["조치소요일"] = (sdf["조치완료일자"] - sdf["지도점검일자"]).dt.days

        # 정렬 키
        sdf["_sort_date"] = sdf["지도점검일자"].fillna(pd.Timestamp.min)

        # 지연 기준
        section("지연 기준(Overdue)")
        overdue_days = st.slider(
            "미조치 상태에서 지연으로 볼 기준(일)",
            min_value=3,
            max_value=60,
            value=14,
            step=1,
            key="dash06_overdue_days",
        )

        sdf["지연여부"] = (sdf["조치상태"] == "미조치") & (sdf["경과일수"] >= overdue_days)

        # 필터
        section("필터 (safety_map)")
        fsdf = dashboard_filters(
            sdf,
            dataset="safety_map",
            key_prefix="dash06_smap",
            use_line=False,
            use_station=False,
            use_type=False,
            use_status=True,
            use_dept=True,
            use_issue_type=True,
            use_action_type=True,
        )
        st.caption(f"필터 결과: {len(fsdf)}건")

        if fsdf.empty:
            st.info("현재 필터 조건에서 표시할 데이터가 없습니다. 날짜 기준/기간 또는 추가 필터를 조정해 보세요.")
        else:
            # KPI
            section("KPI (safety_map)")
            total_cnt = len(fsdf)
            status_s = _safe_series(fsdf, "조치상태", "미조치").astype(str)

            done_cnt = int((status_s == "완료").sum())
            open_cnt = int((status_s == "미조치").sum())
            overdue_cnt = int(_safe_series(fsdf, "지연여부", False).astype(bool).sum())

            # 중요지적(룰)
            action_s = _safe_series(fsdf, "조치구분", "").astype(str)
            issue_s = _safe_series(fsdf, "지적유형", "").astype(str)
            critical_cnt = int(((action_s == "시정지시") | (issue_s.isin(["화재예방", "비상대응"]))).sum())

            k1, k2, k3, k4, k5 = st.columns(5)
            with k1:
                kpi_card("전체 건수", total_cnt, tone="info", chip="safety_map")
            with k2:
                kpi_card("조치 완료", done_cnt, _pct(done_cnt, total_cnt), tone="ok", chip="완료")
            with k3:
                kpi_card("미조치", open_cnt, _pct(open_cnt, total_cnt), tone="danger", chip="리스크")
            with k4:
                kpi_card("지연", overdue_cnt, _pct(overdue_cnt, total_cnt), tone="warn", chip=f"≥{overdue_days}일")
            with k5:
                kpi_card("중요(룰)", critical_cnt, _pct(critical_cnt, total_cnt), tone="critical", chip="중요")

            # 반복지적
            section("반복지적(Repeat Findings)")

            copt1, copt2, _ = st.columns([1.2, 1.8, 2.5])
            with copt1:
                repeat_n = st.slider(
                    "반복 기준(N회 이상)",
                    min_value=2,
                    max_value=10,
                    value=3,
                    step=1,
                    key="dash06_repeat_n",
                )
            with copt2:
                group_mode = st.radio(
                    "그룹 기준",
                    ["장소1+지적유형", "장소1+장소2+지적유형"],
                    horizontal=True,
                    key="dash06_repeat_group_mode",
                )

            group_cols = ["장소1", "지적유형"] if group_mode == "장소1+지적유형" else ["장소1", "장소2", "지적유형"]

            repeat_groups, repeat_group_cnt, repeat_record_cnt, tmp = _repeat_findings(
                fsdf,
                group_cols=group_cols,
                n=repeat_n,
            )

            if repeat_group_cnt > 0:
                top_row = repeat_groups.iloc[0].to_dict()
                top_label = _format_repeat_label(top_row, group_cols)
                top_cnt = int(top_row.get("cnt", 0))
            else:
                top_label = "-"
                top_cnt = 0

            rk1, rk2, rk3, rk4, rk5 = st.columns(5)
            with rk1:
                kpi_card("반복지적 그룹", repeat_group_cnt, tone="critical", chip=f"≥{repeat_n}")
            with rk2:
                kpi_card("반복지적 건수", repeat_record_cnt, _pct(repeat_record_cnt, max(total_cnt, 1)), tone="warn", chip="레코드")
            with rk3:
                kpi_card("기준(N회 이상)", repeat_n, tone="info", chip="기준")
            with rk4:
                kpi_card("TOP 반복 항목", top_label, tone="info", chip="TOP")
            with rk5:
                kpi_card("TOP 반복 횟수", top_cnt, tone="info", chip="횟수")

            c1, c2 = st.columns(2)
            with c1:
                if repeat_group_cnt == 0:
                    st.info("현재 필터 조건에서 반복지적 항목이 없습니다.")
                else:
                    rg = repeat_groups.copy()
                    rg["label"] = rg.apply(lambda r: _format_repeat_label(r.to_dict(), group_cols), axis=1)
                    series = rg.set_index("label")["cnt"].head(10)
                    bar_chart_card("반복지적 TOP 10", series=series, top_n=10)

            with c2:
                if repeat_group_cnt > 0:
                    show_cols = group_cols + ["cnt"]
                    st.dataframe(repeat_groups[show_cols].head(15), width="stretch", height=320)

            # 드릴다운
            if repeat_group_cnt > 0:
                section("반복지적 상세(선택 항목 원본)")

                rg = repeat_groups.copy()
                rg["label"] = rg.apply(lambda r: _format_repeat_label(r.to_dict(), group_cols), axis=1)

                selected_label = st.selectbox(
                    "반복 항목 선택",
                    rg["label"].head(30).tolist(),
                    key="dash06_repeat_pick",
                )

                picked_key = _parse_repeat_label(selected_label, group_cols)
                if picked_key is None:
                    st.warning("선택 라벨 파싱 실패(구분자 문제). 다른 항목을 선택해 보세요.")
                    drill = pd.DataFrame()
                else:
                    drill = tmp[tmp["_grp_key"] == picked_key].copy()

                if drill.empty:
                    st.info("선택 항목의 원본 레코드를 찾지 못했습니다.")
                else:
                    preferred_cols = [
                        "순번",
                        "지도점검일자",
                        "지도점검구분",
                        "점검형태",
                        "제목",
                        "대상부서",
                        "조치구분",
                        "지적유형",
                        "장소1",
                        "장소2",
                        "조치완료일자",
                        "점검자",
                        "조치결과내용",
                        "조치상태",
                        "경과일수",
                        "조치소요일",
                        "지연여부",
                    ]
                    list_cols = [c for c in preferred_cols if c in drill.columns]
                    drill = drill.sort_values("지도점검일자", ascending=False)
                    st.dataframe(drill[list_cols] if list_cols else drill, width="stretch", height=360)

            # 주요 분포
            section("주요 분포 (safety_map)")

            c1, c2 = st.columns(2)
            with c1:
                bar_chart_card(
                    title="지적유형 TOP 10",
                    series=_safe_series(fsdf, "지적유형", "미상").value_counts(),
                    top_n=10,
                )
            with c2:
                bar_chart_card(
                    title="조치구분 분포",
                    series=_safe_series(fsdf, "조치구분", "미상").value_counts(),
                )

            c3, c4 = st.columns(2)
            with c3:
                bar_chart_card(
                    title="대상부서 TOP 10",
                    series=_safe_series(fsdf, "대상부서", "미상").value_counts(),
                    top_n=10,
                )
            with c4:
                bar_chart_card(
                    title="장소1 TOP 10",
                    series=_safe_series(fsdf, "장소1", "미상").value_counts(),
                    top_n=10,
                )

            # 테이블 + 다운로드
            section("원본 테이블 (safety_map)")

            preferred_cols = [
                "순번",
                "지도점검일자",
                "지도점검구분",
                "점검형태",
                "제목",
                "대상부서",
                "조치구분",
                "지적유형",
                "장소1",
                "장소2",
                "조치완료일자",
                "점검자",
                "조치결과내용",
                "조치상태",
                "경과일수",
                "조치소요일",
                "지연여부",
            ]
            list_cols = [c for c in preferred_cols if c in fsdf.columns]
            show_df = fsdf.sort_values("_sort_date", ascending=False)

            st.dataframe(show_df[list_cols] if list_cols else show_df, width="stretch", height=420)

            d1, _ = st.columns([1, 3])
            with d1:
                _download_button(
                    show_df.drop(columns=["_sort_date"], errors="ignore"),
                    "safety_map_filtered.csv",
                    "⬇️ safety_map 다운로드(필터 반영)",
                )

end_page()
