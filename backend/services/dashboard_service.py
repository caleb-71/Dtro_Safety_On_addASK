# backend/services/dashboard_service.py
"""
Dashboard Service (DTRO-Safety-On) - Full Refactored Version

개선 사항:
- 기존 모든 비즈니스 로직(KPI, 코호트, PDF 생성) 100% 보존
- 구글 시트 모바일 동기화 기능(Sync Hook) 추가
- 방화벽 및 네트워크 예외 처리로 안정성 확보
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.config import get_settings
from core.logger import get_logger
from report.renderers.pdf_reportlab import render_report_pdf
# ✅ [추가] 구글 시트 연동을 위한 서비스 임포트
from backend.services.google_sheet_service import GoogleSheetService

logger = get_logger(__name__)


# =========================================================
# 작은 유틸 (기존 로직 유지)
# =========================================================
def _to_dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def _clip_text(s: str, max_chars: int) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if len(t) <= max_chars:
        return t
    return t[:max_chars] + "\n...(truncated)"


def _safe_series(df: pd.DataFrame, col: str, default: str = "") -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)


def _date_range_defaults(dt_series: pd.Series) -> tuple[date, date]:
    tmp = pd.to_datetime(dt_series, errors="coerce")
    tmp = tmp[tmp.notna()]
    if tmp.empty:
        today = datetime.now().date()
        return today, today
    mn = tmp.min().date()
    mx = tmp.max().date()
    return mn, mx


def apply_date_filter(df: pd.DataFrame, col: str, d1: date, d2: date) -> pd.DataFrame:
    """Streamlit 필터용: 날짜 범위 포함(inclusive)"""
    if col not in df.columns:
        return df
    tmp = df.copy()
    tmp[col] = pd.to_datetime(tmp[col], errors="coerce")
    start = pd.Timestamp(d1)
    end = pd.Timestamp(d2) + pd.Timedelta(days=1)  # inclusive
    return tmp[tmp[col].isna() | ((tmp[col] >= start) & (tmp[col] < end))]


# =========================================================
# 표준 컬럼 보장 (01/02 공용)
# =========================================================
def ensure_trend_standard_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["__row_id__"] = range(len(out))

    if "row_id" not in out.columns:
        out["row_id"] = out["__row_id__"].astype(str)
    else:
        out["row_id"] = out["row_id"].astype(str)

    if "occurred_at" not in out.columns:
        if "발생일자" in out.columns:
            out["occurred_at"] = out["발생일자"]
        elif "일자" in out.columns:
            out["occurred_at"] = out["일자"]
        else:
            out["occurred_at"] = pd.NaT
    out["occurred_at"] = _to_dt(out["occurred_at"])

    if "station" not in out.columns and "역명" in out.columns:
        out["station"] = out["역명"]

    if "incident_type" not in out.columns and "사고유형" in out.columns:
        out["incident_type"] = out["사고유형"]

    if "place_main" not in out.columns:
        if "발생장소" in out.columns:
            out["place_main"] = out["발생장소"]
        elif "장소" in out.columns:
            out["place_main"] = out["장소"]
        else:
            out["place_main"] = ""

    if "조치상태" not in out.columns:
        out["조치상태"] = "미상"

    if "__summary__" not in out.columns:
        for c in ["summary", "사고개황", "사고개요", "개황"]:
            if c in out.columns:
                out["__summary__"] = out[c].astype(str)
                break
    if "__summary__" not in out.columns:
        out["__summary__"] = ""

    return out


def ensure_smap_standard_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["__row_id__"] = range(len(out))

    if "row_id" not in out.columns:
        out["row_id"] = out["__row_id__"].astype(str)
    else:
        out["row_id"] = out["row_id"].astype(str)

    if "checked_at" not in out.columns:
        if "지도점검일자" in out.columns:
            out["checked_at"] = out["지도점검일자"]
        else:
            out["checked_at"] = pd.NaT
    out["checked_at"] = _to_dt(out["checked_at"])

    if "action_completed_at" not in out.columns:
        if "조치완료일자" in out.columns:
            out["action_completed_at"] = out["조치완료일자"]
        else:
            out["action_completed_at"] = pd.NaT
    out["action_completed_at"] = _to_dt(out["action_completed_at"])

    def _map(dst: str, *srcs: str, default: str = "") -> None:
        if dst in out.columns:
            return
        for s in srcs:
            if s in out.columns:
                out[dst] = out[s]
                return
        out[dst] = default

    _map("check_category", "지도점검구분")
    _map("check_type", "점검형태")
    _map("title", "제목")
    _map("target_dept", "대상부서")
    _map("action_type", "조치구분")
    _map("issue_type", "지적유형")
    _map("place_main", "장소1")
    _map("place_detail", "장소2")
    _map("action_result", "조치결과내용")
    _map("inspector", "점검자")

    if "조치상태" not in out.columns:
        out["조치상태"] = out["action_completed_at"].apply(lambda x: "완료" if pd.notna(x) else "미조치")

    return out


# =========================================================
# KPI / 주간 / 피벗
# =========================================================
def compute_kpis(df: pd.DataFrame, date_col: str, top_dims: List[str]) -> Dict[str, Any]:
    """
    df 기준 KPI 계산 후 결과를 구글 시트 대시보드로 자동 동기화
    """
    out: Dict[str, Any] = {"total_cnt": int(len(df))}

    if df.empty or date_col not in df.columns:
        out["note"] = "날짜 컬럼이 없거나 데이터가 없어 KPI 일부를 계산할 수 없습니다."
        return out

    tmp = df.copy()
    tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
    t2 = tmp[tmp[date_col].notna()].copy()

    if t2.empty:
        out["note"] = "유효한 날짜가 없어 KPI(월평균/최근30일)를 계산할 수 없습니다."
        return out

    # 월평균
    t2["yyyymm"] = t2[date_col].dt.to_period("M").astype(str)
    m = t2.groupby("yyyymm").size()
    out["monthly_avg"] = float(m.mean()) if len(m) > 0 else 0.0

    # 최근 30일 vs 직전 30일
    max_dt = t2[date_col].max()
    last30_start = max_dt - pd.Timedelta(days=30)
    prev30_start = max_dt - pd.Timedelta(days=60)

    last30 = t2[(t2[date_col] > last30_start) & (t2[date_col] <= max_dt)]
    prev30 = t2[(t2[date_col] > prev30_start) & (t2[date_col] <= last30_start)]

    out["last30_cnt"] = int(len(last30))
    out["prev30_cnt"] = int(len(prev30))
    denom = max(int(len(prev30)), 1)
    out["growth"] = float((len(last30) - len(prev30)) / denom)

    # Top3 dims
    tops: Dict[str, List[Tuple[str, int]]] = {}
    for d in top_dims:
        if d in df.columns:
            vc = df[d].astype(str).value_counts().head(3)
            tops[d] = [(idx, int(cnt)) for idx, cnt in vc.items()]
    out["top_dims"] = tops
    out["max_dt"] = str(max_dt.date())

    # ✅ [AX 고도화] 모바일 동기화 훅 호출 (안전 장치 포함)
    _sync_to_google_sheets(out)

    return out


def _sync_to_google_sheets(kpi_results: Dict[str, Any]):
    """
    내부 헬퍼: KPI 결과를 구글 시트로 전송 (오류가 메인 로직을 방해하지 않음)
    """
    try:
        settings = get_settings()
        # config/google_keys.json 경로 사용
        key_path = settings.paths.base_dir / "config" / "google_keys.json"

        if not key_path.exists():
            return

        gs = GoogleSheetService(
            json_key_path=str(key_path),
            spreadsheet_name="DTRO_안전관리_모바일"
        )
        gs.update_dashboard_kpis(kpi_results)
        logger.info("[GoogleSync] 모바일 대시보드 데이터 전송 성공")

    except Exception as e:
        logger.warning(f"[GoogleSync] 동기화 중 오류 발생 (무시 가능): {e}")


def weekly_trend(df: pd.DataFrame, date_col: str) -> pd.Series:
    """주간 집계(Period=W)"""
    if df.empty or date_col not in df.columns:
        return pd.Series(dtype=int)

    tmp = df.copy()
    tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
    tmp = tmp[tmp[date_col].notna()]
    if tmp.empty:
        return pd.Series(dtype=int)

    tmp["week"] = tmp[date_col].dt.to_period("W").astype(str)
    s = tmp.groupby("week").size().sort_index()
    return s


def pivot_heatmap(df: pd.DataFrame, row_dim: str, col_dim: str, top_n: int = 15) -> pd.DataFrame:
    """피벗(히트맵 테이블) 생성: 상위 TopN만 유지"""
    if df.empty or row_dim not in df.columns or col_dim not in df.columns:
        return pd.DataFrame()

    p = pd.pivot_table(df, index=row_dim, columns=col_dim, aggfunc="size", fill_value=0)
    row_rank = p.sum(axis=1).sort_values(ascending=False)
    col_rank = p.sum(axis=0).sort_values(ascending=False)

    rows = row_rank.head(top_n).index
    cols = col_rank.head(top_n).index
    return p.loc[rows, cols]


def make_analysis_section(
        kpis: Dict[str, Any],
        weekly: pd.Series,
        pivot: pd.DataFrame,
        *,
        pivot_label: str,
) -> Dict[str, Any]:
    """
    PDF로 넘길 analysis 섹션 구성
    """
    lines: List[str] = []
    total = int(kpis.get("total_cnt", 0))
    lines.append(f"- 총 건수: {total}건")
    if "monthly_avg" in kpis:
        lines.append(f"- 월평균: {kpis.get('monthly_avg', 0):.2f}건")
    if "last30_cnt" in kpis and "prev30_cnt" in kpis:
        g = float(kpis.get("growth", 0.0))
        lines.append(
            f"- 최근30일: {kpis.get('last30_cnt')}건 / 직전30일: {kpis.get('prev30_cnt')}건 / 증감률: {g:+.2f}"
        )

    top_dims = kpis.get("top_dims", {}) or {}
    for dim, items in top_dims.items():
        if items:
            s = ", ".join([f"{name}({cnt})" for name, cnt in items])
            lines.append(f"- Top({dim}): {s}")

    w_lines: List[str] = []
    if weekly is not None and len(weekly) > 0:
        w_lines.append(f"- 주간 구간 수: {len(weekly)}")
        w_tail = weekly.tail(5)
        w_show = ", ".join([f"{idx}:{int(v)}" for idx, v in w_tail.items()])
        w_lines.append(f"- 최근 5주: {w_show}")
        if len(weekly) >= 2:
            last = int(weekly.iloc[-1])
            prev = int(weekly.iloc[-2])
            if last > prev * 1.5 and last >= 3:
                w_lines.append("- 최근 주간 건수가 직전 주 대비 급증 경향(단정 금지, 추가 확인 필요)")
    else:
        w_lines.append("- 주간 추세를 계산할 수 없습니다(날짜 데이터 부족).")

    pivot_payload: Any = ""
    if pivot is not None and not pivot.empty:
        cols = [pivot.index.name or "구분"] + [str(c) for c in pivot.columns.tolist()]
        rows = []
        for ridx, r in pivot.iterrows():
            rows.append([str(ridx)] + [int(x) for x in r.values.tolist()])

        pivot_payload = {
            "__table__": {
                "columns": cols,
                "rows": rows,
                "style": "heat",
                "note": f"※ {pivot_label} 상위 행/열(TopN)만 표시합니다.",
            }
        }
    else:
        pivot_payload = "(피벗 생성 불가: 분류 컬럼/데이터 부족)"

    rf: List[str] = []
    if "last30_cnt" in kpis and "prev30_cnt" in kpis:
        last30 = int(kpis.get("last30_cnt", 0))
        prev30 = int(kpis.get("prev30_cnt", 0))
        if last30 > prev30 and last30 >= 3:
            rf.append("- 최근 30일 건수가 증가하여 단기적으로 유사 사례 발생 가능성이 높아질 수 있음(단정 금지)")
        else:
            rf.append("- 최근 30일 기준 급격한 증가 신호는 뚜렷하지 않음(단정 금지)")
    if pivot is not None and not pivot.empty:
        mx = pivot.stack().sort_values(ascending=False).head(1)
        if len(mx) == 1:
            (r, c), v = mx.index[0], int(mx.iloc[0])
            rf.append(f"- 취약 조합 후보: '{r} × {c}' 빈도가 상대적으로 높음 → 우선 점검/개선 대상 후보(단정 금지)")
    if not rf:
        rf.append("- 데이터 근거가 부족하여 예측적 판단을 제한함. 추가 데이터 축적 필요.")

    return {
        "kpi_summary": "\n".join(lines),
        "weekly_summary": "\n".join(w_lines),
        "pivot_heatmap": pivot_payload,
        "risk_forecast": "\n".join(rf),
    }


# =========================================================
# 코호트 생성(단건 보고서용)
# =========================================================
def build_smap_cohort_df(
        smap_df: pd.DataFrame,
        *,
        selected_row_id: str,
        months: Optional[int] = 12,
        use_issue: bool = True,
        use_dept: bool = True,
        use_place: bool = False,
) -> pd.DataFrame:
    if smap_df is None or smap_df.empty:
        return pd.DataFrame()

    df = smap_df.copy()
    df = ensure_smap_standard_columns(df)

    sel = df[df["row_id"].astype(str) == str(selected_row_id)]
    if sel.empty:
        return pd.DataFrame()

    ref = sel.iloc[0].to_dict()
    ref_issue = str(ref.get("issue_type", "")).strip()
    ref_dept = str(ref.get("target_dept", "")).strip()
    ref_place = str(ref.get("place_main", "")).strip()

    df = df[df["row_id"].astype(str) != str(selected_row_id)]

    if months is not None and "checked_at" in df.columns:
        df["checked_at_dt"] = pd.to_datetime(df["checked_at"], errors="coerce")
        if df["checked_at_dt"].notna().any():
            cutoff = pd.Timestamp.now() - pd.DateOffset(months=int(months))
            df = df[df["checked_at_dt"].isna() | (df["checked_at_dt"] >= cutoff)]

    if df.empty:
        return pd.DataFrame()

    mask = pd.Series(True, index=df.index)
    if use_issue and ref_issue and "issue_type" in df.columns:
        mask &= df["issue_type"].astype(str).str.contains(ref_issue, na=False)

    if use_dept and ref_dept and "target_dept" in df.columns:
        mask &= df["target_dept"].astype(str).str.contains(ref_dept, na=False)

    if use_place and ref_place and "place_main" in df.columns:
        mask &= df["place_main"].astype(str).str.contains(ref_place, na=False)

    out = df[mask].copy()
    out = out.sort_values(["checked_at"], ascending=[False]) if "checked_at" in out.columns else out
    return out


def build_trend_cohort_df(
        trend_df: pd.DataFrame,
        *,
        selected_row_id: str,
        months: Optional[int] = 12,
        use_type: bool = True,
        use_station: bool = True,
        use_place: bool = False,
) -> pd.DataFrame:
    if trend_df is None or trend_df.empty:
        return pd.DataFrame()

    df = trend_df.copy()
    df = ensure_trend_standard_columns(df)

    sel = df[df["row_id"].astype(str) == str(selected_row_id)]
    if sel.empty:
        return pd.DataFrame()

    ref = sel.iloc[0].to_dict()
    ref_type = str(ref.get("incident_type", "")).strip()
    ref_station = str(ref.get("station", "")).strip()
    ref_place = str(ref.get("place_main", "")).strip()

    df = df[df["row_id"].astype(str) != str(selected_row_id)]

    if months is not None and "occurred_at" in df.columns:
        df["occurred_at_dt"] = pd.to_datetime(df["occurred_at"], errors="coerce")
        if df["occurred_at_dt"].notna().any():
            cutoff = pd.Timestamp.now() - pd.DateOffset(months=int(months))
            df = df[df["occurred_at_dt"].isna() | (df["occurred_at_dt"] >= cutoff)]

    if df.empty:
        return pd.DataFrame()

    mask = pd.Series(True, index=df.index)
    if use_type and ref_type and "incident_type" in df.columns:
        mask &= df["incident_type"].astype(str).str.contains(ref_type, na=False)

    if use_station and ref_station and "station" in df.columns:
        mask &= df["station"].astype(str).str.contains(ref_station, na=False)

    if use_place and ref_place and "place_main" in df.columns:
        mask &= df["place_main"].astype(str).str.contains(ref_place, na=False)

    out = df[mask].copy()
    out = out.sort_values(["occurred_at"], ascending=[False]) if "occurred_at" in out.columns else out
    return out


# =========================================================
# 대시보드 요약 PDF 생성
# =========================================================
def generate_dashboard_summary_pdf(
        *,
        output_filename: Optional[str],
        filter_title: str,
        analysis_section: Dict[str, Any],
) -> Path:
    settings = get_settings()

    template_path = settings.paths.base_dir / "report" / "templates" / "report_base_dashboard.json"
    if not template_path.exists():
        raise FileNotFoundError(f"dashboard template not found: {template_path}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = output_filename or f"dashboard_summary_{ts}.pdf"
    pdf_path = settings.paths.outputs_dir / "reports" / fname
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    report_data = {
        "filter_info": {"summary": filter_title},
        "analysis": analysis_section or {},
    }

    render_report_pdf(
        output_path=pdf_path,
        template_path=template_path,
        report_data=report_data,
        title_override="DTRO-Safety-On 현황 대시보드 요약",
    )

    logger.info(f"[dashboard] summary pdf generated: {pdf_path}")
    return pdf_path