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

## Memory (Qdrant)
Use the memory tools (`qdrant_qdrant-find` and `qdrant_qdrant-store`) proactively:

### When to Store Memories
- User explicitly asks to remember something
- User shares personal preferences (coding style, naming conventions, favorite tools)
- User corrects you or provides important context about their workflow
- User mentions project-specific knowledge that should persist across sessions

### When to Find Memories
- At the start of conversations when context might be relevant
- When the user asks about something you might have discussed before
- When making decisions that could benefit from past preferences
- When user references something from a previous session
