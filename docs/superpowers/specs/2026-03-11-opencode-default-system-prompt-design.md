# opencode default system prompt (Open Source prompts/Codex CLI)

## overview
- Goal: replace opencode default system prompt with a fixed prompt sourced from `Open Source prompts/Codex CLI/openai-codex-cli-system-prompt-20250820.txt`.
- Scope: fixed replacement only; no runtime selection or prompt composition.
- Safety: keep existing default as fallback if the new prompt cannot be loaded.

## review and approval
- Design sections were reviewed and approved in conversation before implementation.

## chosen source
- Repository: `x1xhlol/system-prompts-and-models-of-ai-tools`
- Path: `Open Source prompts/Codex CLI/openai-codex-cli-system-prompt-20250820.txt`
- Rationale: CLI-oriented prompt is closest to opencode’s usage model.

## components
- Prompt source config: stores the selected source path and version; used by the loader.
  - Interface: read-only config values (path + version).
  - Dependency: a local file copy of the selected prompt.
- Loader: reads the selected prompt and exposes it as the system prompt.
  - Interface: returns prompt text or a failure signal.
  - Dependency: filesystem access for the selected prompt path.
- Fallback: returns opencode’s original default when the loader fails.
  - Interface: default prompt string.
  - Dependency: opencode’s current default prompt source.

## data flow
1. Startup reads selected prompt file.
2. If read succeeds, use it as system prompt.
3. If read fails, revert to opencode default prompt and continue normal flow.

## error handling
- Missing file or read error: log a warning, fall back to default.
- Empty content: treat as failure, fall back to default.
- No truncation or transformation in this phase.

## testing
- Load success: verify prompt is used.
- Load failure: verify fallback to default prompt.
- Empty content: verify fallback to default prompt.
- Missing file: verify warning + fallback to default prompt.
- No behavioral regressions for existing tool calls.

## constraints
- Fixed replacement only.
- No runtime prompt selection.
- No prompt merging/composition.

## non-goals
- No dynamic prompt selection.
- No prompt merging/composition.
