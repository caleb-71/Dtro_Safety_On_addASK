# apps/streamlit/ui/components/filters.py
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st


# ------------------------------------------------------------
# 내부 유틸
# ------------------------------------------------------------
def _to_dt(s: pd.Series) -> pd.Series:
    """Series를 datetime64[ns]로 안전 변환 (NaT 허용)."""
    return pd.to_datetime(s, errors="coerce")


def _today() -> dt.date:
    return dt.date.today()


def _month_range(d: dt.date) -> Tuple[dt.date, dt.date]:
    start = d.replace(day=1)
    if d.month == 12:
        next_month = dt.date(d.year + 1, 1, 1)
    else:
        next_month = dt.date(d.year, d.month + 1, 1)
    end = next_month - dt.timedelta(days=1)
    return start, end


def _prev_month_range(d: dt.date) -> Tuple[dt.date, dt.date]:
    first_this, _ = _month_range(d)
    prev_end = first_this - dt.timedelta(days=1)
    return _month_range(prev_end)


def _year_range(d: dt.date) -> Tuple[dt.date, dt.date]:
    return dt.date(d.year, 1, 1), dt.date(d.year, 12, 31)


def _prev_year_range(d: dt.date) -> Tuple[dt.date, dt.date]:
    return dt.date(d.year - 1, 1, 1), dt.date(d.year - 1, 12, 31)


def _to_timestamp(x) -> Optional[pd.Timestamp]:
    """
    Streamlit date_input 반환값(dt.date) / datetime / str 등을
    pandas Timestamp로 통일. 실패 시 None.
    """
    if x is None:
        return None
    # date_input -> dt.date (datetime이 아닌 date)
    if isinstance(x, dt.date) and not isinstance(x, dt.datetime):
        return pd.Timestamp(x)  # 00:00:00
    ts = pd.to_datetime(x, errors="coerce")
    if pd.isna(ts):
        return None
    return ts


def _apply_date_filter(df: pd.DataFrame, date_col: str, start, end) -> pd.DataFrame:
    """
    date_col 기준으로 [start, end] (inclusive) 필터 적용.

    ✅ 핵심:
    - df[date_col]은 datetime64[ns]로 통일
    - start/end는 pandas Timestamp로 통일
    - 종료일(end)은 해당 날짜의 '끝'까지 포함(23:59:59.999999)
    """
    if df.empty or date_col not in df.columns:
        return df

    s = _to_dt(df[date_col])

    start_ts = _to_timestamp(start)
    end_ts = _to_timestamp(end)

    # start/end 둘 다 없으면 필터 미적용
    if start_ts is None and end_ts is None:
        return df

    # 날짜 단위 필터로 해석: start는 00:00:00, end는 그날 끝까지
    if start_ts is not None:
        start_ts = start_ts.normalize()

    if end_ts is not None:
        end_ts = end_ts.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)

    mask = pd.Series(True, index=df.index)
    # NaT는 자동 제외되도록 비교 전에 notna 반영
    mask &= s.notna()

    if start_ts is not None:
        mask &= s >= start_ts
    if end_ts is not None:
        mask &= s <= end_ts

    return df.loc[mask].copy()


def _get_unique_sorted(df: pd.DataFrame, col: str) -> List[str]:
    if df.empty or col not in df.columns:
        return []
    return sorted(df[col].dropna().astype(str).unique().tolist())


# ------------------------------------------------------------
# 공통 필터(대시보드)
# ------------------------------------------------------------
def dashboard_filters(
    df: pd.DataFrame,
    *,
    dataset: str = "trend",  # "trend" | "safety_map" | "both"
    date_field_map: Optional[Dict[str, List[Tuple[str, str]]]] = None,
    show_date_filter: bool = True,
    # 기존 필터 토글
    use_line: bool = True,
    use_station: bool = True,
    use_type: bool = True,
    use_status: bool = True,
    # safety_map 확장 필터(옵션)
    use_dept: bool = False,
    use_issue_type: bool = False,
    use_action_type: bool = False,
    # session key prefix
    key_prefix: str = "dash",
) -> pd.DataFrame:
    """
    대시보드 공통 필터
    - (옵션) 날짜/기간(프리셋 + 사용자지정)
    - (선택) 분류 필터(호선/역명/사고유형/조치상태)
    - (옵션) safety_map 필터(대상부서/지적유형/조치구분)
    - 반환: 필터링된 DataFrame
    """
    fdf = df.copy()

    # --------------------------------------------------------
    # 0) 기본 date_field 후보 설정
    # --------------------------------------------------------
    if date_field_map is None:
        date_field_map = {
            "trend": [
                ("발생일자", "발생일자"),  # ✅ 너 CSV에 실제 존재
                ("발생일", "발생일"),  # (혹시 다른 데이터셋 대비)
                ("일자", "일자"),  # (레거시 대비)
                ("접수일", "접수일"),
                ("보고일", "보고일"),
                ("등록일", "등록일"),
                ("작성일", "작성일"),
            ],

            "safety_map": [
                # 점검 / 지도
                ("지도점검일자", "지도점검일자"),
                ("지도/점검일", "지도/점검일"),
                ("점검일", "점검일"),
                ("점검일자", "점검일자"),
                ("지도일", "지도일"),
                ("지도일자", "지도일자"),
                ("확인일", "확인일"),
                ("확인일자", "확인일자"),
                # 조치 / 완료
                ("조치일", "조치일"),
                ("조치일자", "조치일자"),
                ("조치완료일", "조치완료일"),
                ("조치완료일자", "조치완료일자"),
                ("개선일", "개선일"),
                ("개선일자", "개선일자"),
                ("이행일", "이행일"),
                ("이행일자", "이행일자"),
                ("완료일", "완료일"),
                ("완료일자", "완료일자"),
                # 계획 / 시행
                ("시행일", "시행일"),
                ("시행일자", "시행일자"),
                ("계획일", "계획일"),
                ("계획일자", "계획일자"),
                ("예정일", "예정일"),
                ("예정일자", "예정일자"),
                ("착수일", "착수일"),
                ("착수일자", "착수일자"),
                ("종결일", "종결일"),
                ("종결일자", "종결일자"),
                # 공통
                ("등록일", "등록일"),
                ("등록일자", "등록일자"),
                ("작성일", "작성일"),
                ("작성일자", "작성일자"),
                ("입력일", "입력일"),
                ("입력일자", "입력일자"),
                ("수정일", "수정일"),
                ("수정일자", "수정일자"),
            ],
            "both": [
                ("발생/점검일", "event_date"),
                ("종결/완료일", "close_date"),
            ],
        }


    # --------------------------------------------------------
    # 2) 날짜/기간 필터 (옵션)
    # --------------------------------------------------------
    if show_date_filter:
        candidates = date_field_map.get(dataset, [])
        valid_candidates: List[Tuple[str, str]] = []

        if dataset == "both":
            valid_candidates = list(candidates)
        else:
            for label, col in candidates:
                if col in fdf.columns:
                    valid_candidates.append((label, col))

        if valid_candidates:
            date_cols = st.columns(4)

            # (1) 날짜 기준 선택 (라벨 중복 방지: label (col) 형태로 표시)
            with date_cols[0]:
                options = [(f"{label} ({col})", col) for (label, col) in valid_candidates]
                option_labels = [x[0] for x in options]
                option_cols = [x[1] for x in options]

                selected = st.selectbox(
                    "날짜 기준",
                    option_labels,
                    index=0,
                    key=f"{key_prefix}_date_label",
                )
                date_col = option_cols[option_labels.index(selected)]

            # (2) 기간 프리셋
                # (2) 기간 프리셋 변경 시 작동할 동적 콜백 함수 정의
                def sync_filter_dates():
                    p = st.session_state.get(f"{key_prefix}_preset")
                    t = _today()
                    if p == "최근 7일":
                        st.session_state[f"{key_prefix}_start"] = t - dt.timedelta(days=6)
                        st.session_state[f"{key_prefix}_end"] = t
                    elif p == "최근 30일":
                        st.session_state[f"{key_prefix}_start"] = t - dt.timedelta(days=29)
                        st.session_state[f"{key_prefix}_end"] = t
                    elif p == "최근 90일":
                        st.session_state[f"{key_prefix}_start"] = t - dt.timedelta(days=89)
                        st.session_state[f"{key_prefix}_end"] = t
                    elif p == "금월":
                        st.session_state[f"{key_prefix}_start"], st.session_state[f"{key_prefix}_end"] = _month_range(t)
                    elif p == "전월":
                        st.session_state[f"{key_prefix}_start"], st.session_state[
                            f"{key_prefix}_end"] = _prev_month_range(t)
                    elif p == "금년":
                        st.session_state[f"{key_prefix}_start"], st.session_state[f"{key_prefix}_end"] = _year_range(t)
                    elif p == "작년":
                        st.session_state[f"{key_prefix}_start"], st.session_state[
                            f"{key_prefix}_end"] = _prev_year_range(t)

                with date_cols[1]:
                    preset = st.selectbox(
                        "기간",
                        ["사용자 지정", "최근 7일", "최근 30일", "최근 90일", "금월", "전월", "금년", "작년"],
                        key=f"{key_prefix}_preset",
                        on_change=sync_filter_dates  # 여기서 콜백 함수를 연결합니다!
                    )

                # (3) start/end 결정 (Session State에서 직접 읽어와 위젯에 반영)
                today = _today()
                start = st.session_state.get(f"{key_prefix}_start", today - dt.timedelta(days=30))
                end = st.session_state.get(f"{key_prefix}_end", today)

                with date_cols[2]:
                    start = st.date_input("시작일", value=start, key=f"{key_prefix}_start")
                with date_cols[3]:
                    end = st.date_input("종료일", value=end, key=f"{key_prefix}_end")

            if start > end:
                st.warning("시작일이 종료일보다 늦습니다. 날짜를 다시 선택하세요.")
            else:
                fdf = _apply_date_filter(fdf, date_col=date_col, start=start, end=end)
        else:
            st.info("날짜 필터를 적용할 수 있는 컬럼을 찾지 못했습니다. (CSV 컬럼명을 확인하세요)")

        # --------------------------------------------------------
        # 3) 분류 필터 및 초기화 버튼 배치 (개선된 UI)
        # --------------------------------------------------------
        # 화면에 표시할 활성 필터들을 모읍니다.
        active_filters = []
        if use_line and "호선" in fdf.columns: active_filters.append("line")
        if use_station and "역명" in fdf.columns: active_filters.append("station")
        if use_type and "사고유형" in fdf.columns: active_filters.append("type")
        if use_status and "조치상태" in fdf.columns: active_filters.append("status")

        # 총 칸 수 계산 (필터 개수 + 초기화 버튼 1개)
        total_items = len(active_filters) + 1
        cols_per_row = 4

        # 아이템 개수에 맞춰 안전하게 여러 줄의 st.columns 격자를 생성합니다.
        rows = [st.columns(cols_per_row) for _ in range((total_items + cols_per_row - 1) // cols_per_row)]

        def get_col(i):
            return rows[i // cols_per_row][i % cols_per_row]

        idx = 0

        if "line" in active_filters:
            with get_col(idx):
                line = st.selectbox("호선", ["전체"] + _get_unique_sorted(fdf, "호선"), key=f"{key_prefix}_line")
            if line != "전체": fdf = fdf[fdf["호선"].astype(str) == line].copy()
            idx += 1

        if "station" in active_filters:
            with get_col(idx):
                station = st.selectbox("역명", ["전체"] + _get_unique_sorted(fdf, "역명"), key=f"{key_prefix}_station")
            if station != "전체": fdf = fdf[fdf["역명"].astype(str) == station].copy()
            idx += 1

        if "type" in active_filters:
            with get_col(idx):
                acc_type = st.selectbox("사고유형", ["전체"] + _get_unique_sorted(fdf, "사고유형"), key=f"{key_prefix}_acc_type")
            if acc_type != "전체": fdf = fdf[fdf["사고유형"].astype(str) == acc_type].copy()
            idx += 1

        if "status" in active_filters:
            with get_col(idx):
                status = st.selectbox("조치상태", ["전체"] + _get_unique_sorted(fdf, "조치상태"), key=f"{key_prefix}_status")
            if status != "전체": fdf = fdf[fdf["조치상태"].astype(str) == status].copy()
            idx += 1

        # ✅ 필터 초기화 버튼을 드롭다운 메뉴 바로 옆에 꽉 차게 배치
        with get_col(idx):
            # 셀렉트박스의 제목(Label) 높이만큼 여백을 주어 버튼을 아래로 딱 맞게 정렬합니다.
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
            if st.button("🔄 초기화", key=f"{key_prefix}_reset", use_container_width=True):
                base_prefix = key_prefix.split('_')[0] if '_' in key_prefix else key_prefix
                for k in list(st.session_state.keys()):
                    # 드롭다운 필터뿐만 아니라 대시보드 공통 기간(날짜) 필터까지 모두 삭제하여 완벽하게 리셋
                    if k.startswith(f"{key_prefix}_") or k.startswith(f"{base_prefix}_common_"):
                        del st.session_state[k]
                st.rerun()

    # --------------------------------------------------------
    # 4) safety_map 확장 필터(옵션)
    # --------------------------------------------------------
    if use_dept or use_issue_type or use_action_type:
        with st.expander("추가 필터(점검/지도)", expanded=False):
            if use_dept and "대상부서" in fdf.columns:
                dept = st.multiselect(
                    "대상부서",
                    _get_unique_sorted(fdf, "대상부서"),
                    default=[],
                    key=f"{key_prefix}_dept",
                )
                if dept:
                    fdf = fdf[fdf["대상부서"].astype(str).isin(dept)].copy()

            if use_issue_type and "지적유형" in fdf.columns:
                issue = st.multiselect(
                    "지적유형",
                    _get_unique_sorted(fdf, "지적유형"),
                    default=[],
                    key=f"{key_prefix}_issue",
                )
                if issue:
                    fdf = fdf[fdf["지적유형"].astype(str).isin(issue)].copy()

            if use_action_type and "조치구분" in fdf.columns:
                action = st.multiselect(
                    "조치구분",
                    _get_unique_sorted(fdf, "조치구분"),
                    default=[],
                    key=f"{key_prefix}_action",
                )
                if action:
                    fdf = fdf[fdf["조치구분"].astype(str).isin(action)].copy()

    return fdf

