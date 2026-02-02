# Global Agent Configuration

## Terminal Environment
- Shell: zsh
- Terminal Emulator: Kitty
- Platform: macOS (darwin)

## Installed Tools
- fzf
- zoxide
- Neovim
- Homebrew
- Docker

## Instructions
When starting a session, you already know the terminal environment without needing to run commands to detect it. Use the terminal environment information provided above.

## Storage Preference
Prefer storing caches, SDKs, repos, and large artifacts on /Volumes/ExternalSSD. Avoid writing new large data to the internal drive unless explicitly requested.

## Search Web Trigger
Use the searchWeb MCP when the user asks for online-only, current, or time-sensitive information (e.g., weather, news, prices, releases), or explicitly requests web search.

### Always Search
- Run `qdrant_qdrant-find` at the start of every conversation
- Run `qdrant_qdrant-find` before making decisions that could benefit from prior context
- Run `qdrant_qdrant-find` when the user references something from a previous session

### Store Summaries
- Store memory when it is likely useful across sessions
- Always store when the user says "remember" or requests a "session summary"
- When storing, update a rolling session summary (single entry)
