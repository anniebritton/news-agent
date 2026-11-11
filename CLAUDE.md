# CLAUDE.md

Config-driven framework for news agents that notify via pluggable channels (Telegram, Signal, console). Keep it topic-agnostic: no
domain-specific code, prompts, or examples in the shared package (a test enforces this).

## Commands

```bash
pip install -r requirements.txt
python -m pytest tests/ -v          # run before pushing
python main.py --agent <id>         # run from the repo root
```

## Adding an agent

1. Copy `agents/TEMPLATE.yaml` to `agents/<id>.yaml` and fill it in.
2. Copy `.github/workflows/run_agent.yml` to `run_<id>.yml`; change the name, `AGENT`, and secret names.
3. Add `GEMINI_API_KEY` and the secrets for each configured notify channel as repo secrets.

## Gotchas

- State paths are relative (`state/<id>_processed_news.json`); run from the repo root.
- `prompts.task` must contain `{indexed_text}` and `{date_str}` (validated at load).
- The extractor's item fields (`subject`, `group`, `summary`, `translated_title`) are the contract between
  prompt output and `messages.item_template`; custom `output_format` prompts must keep those names.
- New channels register via `@register("name")` in `newsagent/notifiers/`; they receive a `Message`, never pre-formatted text.
- Inside `newsagent/` use relative imports; `main.py` and tests use `newsagent.*`.
