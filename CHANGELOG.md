# Changelog

## Unreleased

- Replaced `CLAUDE.md` with `AGENTS.md`, adding a setup workflow for AI coding agents.
- Added `--dry-run` (prints to the console, does not save state).
- Added `example_launches` agent (rocket launch schedule changes); the example workflow now runs it.
- Added CONTRIBUTING, issue/PR templates, Dependabot, and a PR test workflow.
- Scheduled runs in the example workflow are now commented out by default.

## 0.2.0

- Migrated from `google-generativeai` (end of life 2025-11-30) to `google-genai`.
- Updated default Gemini models to `gemini-3.8-flash` / `gemini-3.6-flash`
  (`gemini-2.0-flash` was shut down 2026-06-01).
- Updated GitHub Actions to `checkout@v7` and `setup-python@v7`.

## 0.1.0

- Initial release: config-driven agents, pluggable notification channels
  (Telegram, Signal, console), Gemini extraction, per-agent state.
