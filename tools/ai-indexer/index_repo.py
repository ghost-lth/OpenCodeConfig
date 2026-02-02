#!/usr/bin/env python3
import argparse
import hashlib
import uuid
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import requests


@dataclass(frozen=True)
class Chunk:
    file_path: str
    start: int
    end: int
    text: str


def load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def iter_files(
    root: Path, include_exts: Iterable[str], exclude_dirs: Iterable[str]
) -> Iterable[Path]:
    exclude_set = set(exclude_dirs)
    include_set = set(ext.lower() for ext in include_exts)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_set]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in include_set:
                yield path


def chunk_text(text: str, size: int, overlap: int) -> Iterable[Tuple[int, int, str]]:
    if size <= 0:
        raise ValueError("chunk size must be > 0")
    if overlap < 0:
        raise ValueError("chunk overlap must be >= 0")
    if overlap >= size:
        raise ValueError("chunk overlap must be smaller than chunk size")

    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        yield start, end, text[start:end]
        if end == length:
            break
        start = end - overlap


def sha1_file(path: Path) -> str:
    sha1 = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            sha1.update(block)
    return sha1.hexdigest()


def embed_texts_http(texts: List[str], model: str, base_url: str) -> List[List[float]]:
    response = requests.post(
        f"{base_url}/api/embeddings",
        json={
            "model": model,
            "input": texts,
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    embeddings = [item["embedding"] for item in data.get("data", [])]
    if len(embeddings) != len(texts):
        raise RuntimeError("Embedding count mismatch.")
    return embeddings


def embed_texts_fastembed(texts: List[str], model: str) -> List[List[float]]:
    try:
        from fastembed import TextEmbedding  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "fastembed is required. Run with uvx --with fastembed"
        ) from exc

    embedder = TextEmbedding(model_name=model)
    return [vector.tolist() for vector in embedder.embed(texts)]


def qdrant_upsert(collection: str, points: List[Dict], base_url: str) -> None:
    if not points:
        return
    response = requests.put(
        f"{base_url}/collections/{collection}/points",
        json={"points": points},
        timeout=120,
    )
    response.raise_for_status()


def qdrant_delete_file(collection: str, file_path: str, base_url: str) -> None:
    response = requests.post(
        f"{base_url}/collections/{collection}/points/delete",
        json={
            "filter": {
                "must": [
                    {"key": "file_path", "match": {"value": file_path}},
                ]
            }
        },
        timeout=120,
    )
    response.raise_for_status()


def load_config(path: Path) -> Dict:
    config = load_json(path)
    if not config:
        raise ValueError("Config file is empty or missing.")
    return config


def resolve_state_path(state_dir: str, repo_root: Path, collection: str) -> Path:
    state_dir_path = Path(state_dir).expanduser()
    if not state_dir_path.is_absolute():
        state_dir_path = repo_root / state_dir_path
    return state_dir_path / f"{collection}.state.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Index a repository into Qdrant.")
    parser.add_argument("--config", required=True, help="Path to config JSON")
    parser.add_argument("--root", required=False, help="Repo root override")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)

    repo_path = config.get("RepoPath") or config.get("repo_root")
    if not repo_path:
        raise ValueError("RepoPath is required in config.")

    repo_root = (
        Path(args.root).expanduser().resolve()
        if args.root
        else Path(repo_path).expanduser().resolve()
    )

    collection = config.get("Collection") or config.get("collection")
    if not collection:
        raise ValueError("Collection is required in config.")

    state_dir = (
        config.get("StateDir")
        or config.get("state_dir")
        or "~/.config/opencode/tools/ai-indexer/state"
    )
    state_path = resolve_state_path(state_dir, repo_root, collection)
    state = load_json(state_path)
    state.setdefault("files", {})

    qdrant_url = (
        os.environ.get("QDRANT_URL")
        or config.get("QdrantUrl")
        or config.get("qdrant_url")
        or "http://localhost:6333"
    )
    embed_url = (
        os.environ.get("EMBEDDINGS_URL")
        or config.get("EmbeddingsUrl")
        or config.get("embeddings_url")
        or "http://localhost:11434"
    )

    model = config.get("EmbeddingModel") or config.get("embedding_model")
    if not model:
        raise ValueError("EmbeddingModel is required in config.")

    chunk_size = int(config.get("ChunkSize") or config.get("chunk_size") or 1000)
    chunk_overlap = int(
        config.get("ChunkOverlap") or config.get("chunk_overlap") or 180
    )

    include_exts = config.get("ExtendSuffix") or config.get("include_extensions")
    if not include_exts:
        raise ValueError("ExtendSuffix is required in config.")

    exclude_dirs = config.get("ExcludeDirs") or config.get("exclude_dirs") or []

    files_seen = set()
    for path in iter_files(repo_root, include_exts, exclude_dirs):
        rel_path = path.relative_to(repo_root).as_posix()
        files_seen.add(rel_path)
        digest = sha1_file(path)
        if state["files"].get(rel_path) == digest:
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        chunks = [
            Chunk(rel_path, start, end, chunk)
            for start, end, chunk in chunk_text(text, chunk_size, chunk_overlap)
            if chunk.strip()
        ]
        if not chunks:
            state["files"][rel_path] = digest
            continue

        embedding_mode = (config.get("EmbeddingMode") or "fastembed").lower()
        if embedding_mode == "http":
            embeddings = embed_texts_http([c.text for c in chunks], model, embed_url)
        else:
            embeddings = embed_texts_fastembed([c.text for c in chunks], model)
        points = []
        for chunk, vector in zip(chunks, embeddings):
            point_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{chunk.file_path}:{chunk.start}:{chunk.end}",
                )
            )
            points.append(
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "file_path": chunk.file_path,
                        "start": chunk.start,
                        "end": chunk.end,
                    },
                }
            )
        qdrant_upsert(collection, points, qdrant_url)
        state["files"][rel_path] = digest

    removed = [path for path in state["files"].keys() if path not in files_seen]
    for rel_path in removed:
        qdrant_delete_file(collection, rel_path, qdrant_url)
        state["files"].pop(rel_path, None)

    save_json(state_path, state)
    print("Indexing complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
