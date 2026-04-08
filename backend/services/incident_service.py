# backend/services/incident_service.py
"""
Incident Service (DTRO-Safety-On)

역할
- 3종 CSV 로드 (동향보고/철도사고/안전심사)
- 검색/필터를 위해 DataFrame 제공
- 선택된 1행(row)을 표준 incident_data로 매핑

CSV 경로(기본)
- data/csv/trend.csv
- data/csv/incident.csv
- data/csv/safety_audit.csv (없으면 safety_map.csv도 탐색)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple

import pandas as pd

from core.config import get_settings
from core.logger import get_logger
from shared.io.csv_loader import load_csv

from backend.mappers.trend_mapper import map_trend_row_to_incident_data
from backend.mappers.incident_mapper import map_incident_row_to_incident_data
from backend.mappers.safety_audit_mapper import map_audit_row_to_incident_data

logger = get_logger(__name__)

DataKind = Literal["동향보고", "철도사고", "안전심사"]


@dataclass
class LoadedDataset:
    kind: DataKind
    path: Path
    df: pd.DataFrame


class IncidentService:
    def __init__(self):
        self.settings = get_settings()
        self.csv_dir = self.settings.paths.data_dir / "csv"

    def _resolve_path(self, kind: DataKind) -> Path:
        """
        kind별 기본 파일명 매핑
        """
        if kind == "동향보고":
            return self.csv_dir / "trend.csv"
        if kind == "철도사고":
            return self.csv_dir / "incident.csv"
        # 안전심사: safety_audit.csv 우선, 없으면 safety_map.csv
        p1 = self.csv_dir / "safety_audit.csv"
        p2 = self.csv_dir / "safety_map.csv"
        return p1 if p1.exists() else p2

    def load_dataset(self, kind: DataKind) -> LoadedDataset:
        path = self._resolve_path(kind)
        res = load_csv(path)
        df = res.df

        # 인덱스용 선택 컬럼 생성(초기 MVP)
        df = df.copy()
        df["__row_id__"] = range(len(df))

        logger.info(f"[IncidentService] loaded kind={kind} path={path.name} rows={len(df)}")
        return LoadedDataset(kind=kind, path=path, df=df)

    def map_row_to_incident_data(self, kind: DataKind, row: Dict) -> Dict:
        """
        df의 한 행(dict)을 표준 incident_data로 변환
        """
        if kind == "동향보고":
            return map_trend_row_to_incident_data(row)
        if kind == "철도사고":
            return map_incident_row_to_incident_data(row)
        return map_audit_row_to_incident_data(row)
