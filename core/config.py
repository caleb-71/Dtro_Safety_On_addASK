# core/config.py
"""
DTRO-Safety-On 설정 로더

- config/settings.yaml을 읽어서 "단일 설정 객체"로 제공
- 경로(Path)들을 절대경로로 정규화하여, 어디서 실행해도 동일하게 동작
- 이후 모든 모듈은 settings를 통해 모델명/경로/파라미터를 참조

사용 예:
from core.config import get_settings
settings = get_settings()
print(settings.ollama.llm_model)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml


# =========================
# Dataclasses (Settings Schema)
# =========================

@dataclass(frozen=True)
class AppSettings:
    name: str
    env: str


@dataclass(frozen=True)
class PathSettings:
    base_dir: Path
    data_dir: Path
    docs_dir: Path
    index_dir: Path
    outputs_dir: Path
    logs_dir: Path


@dataclass(frozen=True)
class OllamaSettings:
    base_url: str
    llm_model: str
    embed_model: str
    timeout_sec: int


@dataclass(frozen=True)
class RagSettings:
    chunk_size: int
    chunk_overlap: int
    top_k: int
    min_chunk_chars: int


@dataclass(frozen=True)
class IndexSettings:
    faiss_index_file: str
    meta_file: str
    checksum_file: str


@dataclass(frozen=True)
class Settings:
    app: AppSettings
    paths: PathSettings
    ollama: OllamaSettings
    rag: RagSettings
    index: IndexSettings


# =========================
# Loader
# =========================

_SETTINGS_CACHE: Settings | None = None


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"settings.yaml not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError("settings.yaml must be a YAML mapping (dict).")

    return data


def _to_abs(base_dir: Path, maybe_rel: str) -> Path:
    """
    settings.yaml의 상대경로를 프로젝트 루트(base_dir) 기준 절대경로로 변환
    """
    p = Path(maybe_rel)
    return p if p.is_absolute() else (base_dir / p).resolve()


def _ensure_dirs(*dirs: Path) -> None:
    """
    실행 중 필요한 폴더가 없으면 자동 생성.
    - data/index, data/outputs/logs 등
    """
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def get_project_root() -> Path:
    """
    프로젝트 루트 탐색:
    - core/config.py 기준으로 상위 폴더를 루트로 간주
    """
    return Path(__file__).resolve().parents[1]


def get_settings() -> Settings:
    """
    settings.yaml을 읽어 Settings 객체로 반환 (캐싱)
    """
    global _SETTINGS_CACHE
    if _SETTINGS_CACHE is not None:
        return _SETTINGS_CACHE

    base_dir = get_project_root()
    settings_path = base_dir / "config" / "settings.yaml"
    raw = _read_yaml(settings_path)

    # --- app ---
    app_raw = raw.get("app", {})
    app = AppSettings(
        name=str(app_raw.get("name", "DTRO-Safety-On")),
        env=str(app_raw.get("env", "local")),
    )

    # --- paths ---
    paths_raw = raw.get("paths", {})
    data_dir = _to_abs(base_dir, str(paths_raw.get("data_dir", "data")))
    docs_dir = _to_abs(base_dir, str(paths_raw.get("docs_dir", "data/docs")))
    index_dir = _to_abs(base_dir, str(paths_raw.get("index_dir", "data/index")))
    outputs_dir = _to_abs(base_dir, str(paths_raw.get("outputs_dir", "data/outputs")))
    logs_dir = _to_abs(base_dir, str(paths_raw.get("logs_dir", "data/outputs/logs")))

    paths = PathSettings(
        base_dir=base_dir,
        data_dir=data_dir,
        docs_dir=docs_dir,
        index_dir=index_dir,
        outputs_dir=outputs_dir,
        logs_dir=logs_dir,
    )

    # --- ollama ---
    ollama_raw = raw.get("ollama", {})
    ollama = OllamaSettings(
        base_url=str(ollama_raw.get("base_url", "http://localhost:11434")),
        llm_model=str(ollama_raw.get("llm_model", "llama3.1:8b")),
        embed_model=str(ollama_raw.get("embed_model", "nomic-embed-text")),
        timeout_sec=int(ollama_raw.get("timeout_sec", 120)),
    )

    # --- rag ---
    rag_raw = raw.get("rag", {})
    rag = RagSettings(
        chunk_size=int(rag_raw.get("chunk_size", 1000)),
        chunk_overlap=int(rag_raw.get("chunk_overlap", 150)),
        top_k=int(rag_raw.get("top_k", 5)),
        min_chunk_chars=int(rag_raw.get("min_chunk_chars", 200)),
    )

    # --- index ---
    index_raw = raw.get("index", {})
    index = IndexSettings(
        faiss_index_file=str(index_raw.get("faiss_index_file", "faiss.index")),
        meta_file=str(index_raw.get("meta_file", "meta.jsonl")),
        checksum_file=str(index_raw.get("checksum_file", "checksum.json")),
    )

    # 필요한 폴더 자동 생성
    _ensure_dirs(paths.data_dir, paths.docs_dir, paths.index_dir, paths.outputs_dir, paths.logs_dir)

    _SETTINGS_CACHE = Settings(
        app=app,
        paths=paths,
        ollama=ollama,
        rag=rag,
        index=index,
    )
    return _SETTINGS_CACHE
