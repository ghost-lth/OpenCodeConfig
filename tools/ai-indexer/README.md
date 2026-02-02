AI indexing helper for local repositories.

Usage is configured per-project via a JSON config file.

Quick start:
1) Copy config-example.json to your repo or ~/.config/opencode
2) Edit repo_root, collection, embedding model, and file filters
3) Run: python3 index_repo.py --config /path/to/config.json

State:
The indexer writes a per-collection state file into state_dir so it can
incrementally re-index only changed files.
