# Global Agent Configuration

## Environment (Authoritative)
- Shell: zsh
- Terminal Emulator: Kitty
- Platform: macOS (darwin)

## Installed Tools
- fzf
- zoxide
- Neovim
- Homebrew
- Docker

## Execution Defaults
- Do not probe the terminal environment; treat the values above as authoritative.
- Store caches, SDKs, repos, and large artifacts on `/Volumes/ExternalSSD`.
- Avoid writing large data to the internal drive unless explicitly requested.

## Web Search Policy
- Use `searchWeb` for online-only, current, or time-sensitive info, or when explicitly requested.
- For GitHub repository details, prefer GitHub API or `gh` CLI; use `searchWeb` only if those are unavailable.

## Memory Retrieval
- Run `qdrant_qdrant-find` at the start of every conversation.
- Run `qdrant_qdrant-find` before decisions that could benefit from prior context.
- Run `qdrant_qdrant-find` when the user references a prior session.
- When working inside `/Volumes/ExternalSSD/repo/work/picasso`, use `qdrantPicasso` for memory lookup.

## Memory Storage
- Store memory when it is likely useful across sessions.
- Always store when the user says "remember" or requests a "session summary".
- Maintain a rolling session summary (single entry).

## MCP Usage
- When the project is C#, prefer using the `csharpLsp` MCP for LSP queries.
