# Changelog

## Unreleased

### Fixed (from an adversarial review)
- **Silent loss:** URLs are marked seen only after the LLM processed them, and state is saved only after a channel accepted the message. A failed LLM call or failed send now retries next run instead of dropping the news.
- The run exits non-zero when no channel accepts a message or the LLM fails for every batch.
- Telegram messages over 4096 characters are split on line boundaries instead of being rejected; a cap (`max_items_per_message`) carries extra items to the next run.
- **Fetcher safety:** http(s) only, private/loopback/link-local/metadata addresses refused (including via redirects), 2 MB streamed cap, text content types only. A failed fetch returns `None` instead of "Error: ..." text that leaked into prompts.
- Link regex bounded (was catastrophic on hostile input); links with parentheses survive; titles can no longer swallow earlier link text.
- One malformed LLM item no longer drops the whole batch.
- `time_window_hours` is now actually enforced (the LLM reports `published`); future/garbage `last_run_at` handled.
- Dedupe keys on the canonical URL instead of a 50-character title slug.
- State keeps the *newest* entries when truncated; a corrupt state file is quarantined and aborts the run instead of silently resetting.
- Config validation rejects unknown placeholders and unbalanced braces; null `agent:`/`notify:` blocks give friendly errors.
- Prompt injection hardening: article text is delimited and the extractor adds an "ignore instructions in articles" rule.
- Secrets redacted from logs; the channel only receives fixed generic error text; `hostname` (not `netloc`) in link labels; link schemes allowlisted when rendering.
- Workflow: concurrency group, rebase-and-retry push, no tests in the run job, credentials only in the commit step, dependency bounds.

### Fixed (second review round)
- Prompt-injection delimiter escape: URLs are percent-encoded (also in `canonical_url`), angle brackets (ASCII, full-width, small-form) are stripped from titles/content, and article delimiters are random per run.
- Corrupt state no longer blocks scheduled runs forever: `--reset-state` and a `reset_state` workflow input; recovery documented.
- A total feed outage is reported as an outage (exit 1, clock not advanced), not as "no new updates"; partial outages add a note to the message.
- Partial multi-channel delivery exits 4 and logs loudly; delivery semantics documented.
- Telegram chunker never slices rendered HTML: text is truncated before escaping, a link that can't fit is dropped, lengths are counted in UTF-16 units.
- Missing channel secrets fail fast (exit 2) before any LLM call; the workflow's `git pull --rebase` is inside the retry loop; branch-protection note.
- Config validation for `settings.*` and unknown channel types; `--limit` must be >= 1.
- Percent-encoded tokens redacted; unexpected crashes print one redacted line; NAT64 / 6to4 / Teredo ranges blocked; per-URL wall-clock fetch deadline; `google-genai` floor loosened to 1.0.
- Docs: `keywords: []` (not "remove the key"); honest description of what `test_leakage.py` checks.

### Added
- `--reset-state`.
- `--check` (list candidate links, no API key), `--seed` (record current links without notifying), `--limit N`; missing API key is a friendly error.
- Test suite grew from 33 to 193 tests, each fix verified by reverting it and watching a test fail.

### Changed
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
