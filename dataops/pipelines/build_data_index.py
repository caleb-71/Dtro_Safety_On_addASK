# dataops/pipelines/build_data_index.py
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple

from core.config import get_settings
from core.logger import get_logger
from backend.integrations.ollama_client import OllamaClient
from rag.vectorstores.faiss_store import DataFaissVectorStore, DataChunkMeta, sha256_file

logger = get_logger(__name__)


def _scan_meta_jsonls(project_root: Path) -> List[Path]:
    """
    data/meta/**/meta.jsonl 재귀 스캔
    """
    meta_root = project_root / "data" / "meta"
    if not meta_root.exists():
        return []
    return sorted([p for p in meta_root.rglob("meta.jsonl") if p.is_file()])


def _compute_checksums(files: List[Path], base_dir: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p in files:
        rel = str(p.resolve().relative_to(base_dir.resolve())).replace("\\", "/")
        out[rel] = sha256_file(p)
    return out


def _has_changes(old: Dict[str, str], new: Dict[str, str]) -> Tuple[bool, Dict[str, int]]:
    old_keys = set(old.keys())
    new_keys = set(new.keys())
    added = len(new_keys - old_keys)
    removed = len(old_keys - new_keys)
    modified = sum(1 for k in (old_keys & new_keys) if old.get(k) != new.get(k))
    changed = (added + removed + modified) > 0
    return changed, {"added": added, "modified": modified, "removed": removed}


def _reset_index_files(store: DataFaissVectorStore) -> None:
    for p in [store.faiss_path, store.meta_path, store.checksum_path]:
        if p.exists():
            p.unlink()
            logger.info(f"[DataIndex] removed old file: {p}")


def _batch(lst: List[str], batch_size: int) -> List[List[str]]:
    return [lst[i:i + batch_size] for i in range(0, len(lst), batch_size)]


def build_data_index(force: bool = False, embed_batch_size: int = 16) -> None:
    settings = get_settings()
    project_root = Path(__file__).resolve().parents[2]  # dataops/pipelines/ -> project root

    meta_files = _scan_meta_jsonls(project_root)
    logger.info(f"[BuildDataIndex] found meta_jsonl files={len(meta_files)}")

    if not meta_files:
        logger.warning("[BuildDataIndex] No meta.jsonl found under data/meta/**/meta.jsonl")
        return

    # 체크섬 계산
    base_dir = project_root
    new_checksums = _compute_checksums(meta_files, base_dir)

    store = DataFaissVectorStore()
    old_checksums = store.load_checksums()
    changed, stat = _has_changes(old_checksums, new_checksums)

    has_index_files = store.faiss_path.exists() and store.meta_path.exists()
    logger.info(f"[BuildDataIndex] has_index_files={has_index_files} force={force} changed={changed} stat={stat}")

    if (not force) and has_index_files and (not changed):
        logger.info("[BuildDataIndex] No changes detected. Skip indexing.")
        return

    logger.info("[BuildDataIndex] Rebuild data index (full) started...")
    _reset_index_files(store)

    # Ollama 준비
    client = OllamaClient()
    if not client.ping():
        raise RuntimeError("Ollama server not reachable. Check Ollama is running and base_url is correct.")

    probe = client.embed("dimension probe")
    if not probe.embeddings or not probe.embeddings[0]:
        raise RuntimeError("Embedding probe failed. Check embed_model in settings.yaml and Ollama models.")
    dim = len(probe.embeddings[0])
    logger.info(f"[BuildDataIndex] embedding dim={dim}")

    store.load_or_create(dim=dim)

    total = 0
    t0_all = time.time()

    # 모든 meta.jsonl 합쳐서 처리
    for f_i, meta_path in enumerate(meta_files, start=1):
        logger.info(f"[BuildDataIndex] ({f_i}/{len(meta_files)}) reading {meta_path}")

        lines = meta_path.read_text(encoding="utf-8").splitlines()
        lines = [ln.strip() for ln in lines if ln.strip()]
        if not lines:
            continue

        objs = []
        for ln in lines:
            try:
                objs.append(json.loads(ln))
            except Exception:
                continue

        texts: List[str] = [str(o.get("text", "")) for o in objs]
        metas: List[DataChunkMeta] = []

        for o in objs:
            chunk_id = str(o.get("id", ""))
            row_id = str(o.get("row_id", ""))
            dataset = str(o.get("dataset", "data"))
            text = str(o.get("text", ""))
            md = o.get("metadata", {}) or {}
            source_path = str(md.get("raw_ref", ""))

            title_hint = ""
            for k in ["title", "summary", "issue_type", "incident_type"]:
                if k in md and str(md.get(k)).strip():
                    title_hint = str(md.get(k)).strip()
                    break
            doc_title = f"{dataset} | {title_hint}" if title_hint else dataset

            metas.append(
                DataChunkMeta(
                    chunk_id=chunk_id,
                    doc_id=row_id,
                    doc_title=doc_title,
                    category=dataset,
                    source_path=source_path,
                    page_no=0,
                    text=text,
                    char_len=len(text),
                )
            )

        batches = _batch(texts, embed_batch_size)
        meta_batches = [metas[i:i + embed_batch_size] for i in range(0, len(metas), embed_batch_size)]

        for b_i, (text_batch, meta_batch) in enumerate(zip(batches, meta_batches), start=1):
            emb_res = client.embed(text_batch)
            store.add(
                embeddings=emb_res.embeddings,
                metas=meta_batch,        # type: ignore[arg-type]
                append_meta=True,
                save_index=False,
            )
            logger.info(
                f"[BuildDataIndex] file={meta_path.parent.name} batch={b_i}/{len(batches)} "
                f"added={len(meta_batch)} ntotal={store.ntotal}"
            )

        total += len(metas)
        time.sleep(0.05)

    store.save()
    store.save_checksums(new_checksums)

    dt = time.time() - t0_all
    logger.info(f"[BuildDataIndex] DONE. total_rows={total} ntotal={store.ntotal} elapsed={dt:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="DTRO-Safety-On - Build DATA RAG Index (FAISS)")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if no changes detected.")
    parser.add_argument("--embed-batch-size", type=int, default=16, help="Embedding batch size (default=16).")
    args = parser.parse_args()

    build_data_index(force=bool(args.force), embed_batch_size=int(args.embed_batch_size))


if __name__ == "__main__":
    main()
