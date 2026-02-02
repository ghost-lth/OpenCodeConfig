import json
import sys
from pathlib import Path

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import index_repo  # type: ignore[import-not-found]


def test_chunk_text_splits_with_overlap():
    text = "abcdefghijklmnopqrstuvwxyz"
    chunks = list(index_repo.chunk_text(text, size=10, overlap=3))

    assert chunks[0] == (0, 10, "abcdefghij")
    assert chunks[1] == (7, 17, "hijklmnopq")
    assert chunks[2] == (14, 24, "opqrstuvwx")
    assert chunks[3] == (21, 26, "vwxyz")


@pytest.mark.parametrize(
    ("size", "overlap"),
    [
        (0, 0),
        (10, -1),
        (10, 10),
    ],
)
def test_chunk_text_rejects_invalid(size, overlap):
    with pytest.raises(ValueError):
        list(index_repo.chunk_text("text", size=size, overlap=overlap))


def test_iter_files_respects_filters(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "skip").mkdir()
    (tmp_path / "keep" / "a.cs").write_text("one", encoding="utf-8")
    (tmp_path / "keep" / "b.md").write_text("two", encoding="utf-8")
    (tmp_path / "skip" / "c.cs").write_text("three", encoding="utf-8")
    (tmp_path / "keep" / "d.txt").write_text("four", encoding="utf-8")

    files = list(
        index_repo.iter_files(
            tmp_path, include_exts=[".cs", ".md"], exclude_dirs=["skip"]
        )
    )
    rel_paths = sorted(path.relative_to(tmp_path).as_posix() for path in files)

    assert rel_paths == ["keep/a.cs", "keep/b.md"]


def test_resolve_state_path_handles_relative(tmp_path):
    state_path = index_repo.resolve_state_path(".state", tmp_path, "collection")

    assert state_path == tmp_path / ".state" / "collection.state.json"


def test_main_indexes_and_cleans_removed_files(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "a.cs").write_text("class A {}", encoding="utf-8")
    (repo_root / "b.md").write_text("docs", encoding="utf-8")

    state_dir = tmp_path / "state"
    config_path = tmp_path / "config.json"
    config = {
        "RepoPath": repo_root.as_posix(),
        "Collection": "test-collection",
        "EmbeddingModel": "bge-small",
        "ChunkSize": 10,
        "ChunkOverlap": 2,
        "StateDir": state_dir.as_posix(),
        "ExtendSuffix": [".cs", ".md"],
        "ExcludeDirs": [],
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    captured = {"upserts": [], "deletes": []}

    def fake_embed(texts, model):
        return [[0.0, 0.0, 0.0] for _ in texts]

    def fake_upsert(collection, points, base_url):
        captured["upserts"].append((collection, points))

    def fake_delete(collection, file_path, base_url):
        captured["deletes"].append((collection, file_path))

    monkeypatch.setattr(index_repo, "embed_texts_fastembed", fake_embed)
    monkeypatch.setattr(index_repo, "qdrant_upsert", fake_upsert)
    monkeypatch.setattr(index_repo, "qdrant_delete_file", fake_delete)

    monkeypatch.setattr(
        sys, "argv", ["index_repo.py", "--config", config_path.as_posix()]
    )
    assert index_repo.main() == 0
    assert captured["upserts"]
    assert isinstance(captured["upserts"][0][1][0]["id"], str)

    (repo_root / "b.md").unlink()
    monkeypatch.setattr(
        sys, "argv", ["index_repo.py", "--config", config_path.as_posix()]
    )
    assert index_repo.main() == 0

    assert captured["deletes"] == [("test-collection", "b.md")]
