# Contributing

Thanks for helping. This project stays small on purpose: a topic-agnostic core in `newsagent/`, and
everything topic-specific in `agents/*.yaml`.

AI coding agents: see [`AGENTS.md`](AGENTS.md).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v      # no API keys or network needed
```

## Ground rules

- Keep `newsagent/` free of topic-specific code, prompts, and examples. A test checks for this.
- Add a test with every change; the LLM and HTTP calls are faked, follow the existing patterns.
- Small PRs are easier to review.

## Add a notification channel (good first issue)

1. Create `newsagent/notifiers/<name>.py` with a class decorated `@register("<name>")` whose
   `__init__(self, channel_cfg: dict)` reads credentials from env vars named in the config, and which
   implements `async def send(self, message: Message)`. See `signal.py` for a short example and
   `Message.to_plain()` for a ready plain-text rendering.
2. Import it in `newsagent/notifiers/__init__.py`.
3. Add tests (fake the HTTP call) and document the config keys in the template.

Ideas: Slack, Discord, Matrix, ntfy, email (SMTP), generic webhook.

## Add an LLM provider

Implement `generate(prompt) -> Optional[str]` (see `LLMProvider` in `newsagent/llm.py`) and add a branch in
`build_provider`. Ideas: Claude, OpenAI, local models via Ollama.

## Share an example agent

Add `agents/example_<topic>.yaml`. The best examples are niche topics where updates are frequent and
timing matters. Check that every feed returns 200 for a normal browser user agent, and run
`python main.py --agent example_<topic> --dry-run` before opening the PR.

## Other good first issues

- RSS/Atom feed parsing as a first-class feed type (today feeds are scraped as HTML links)
- Digest mode: batch items and send at most once per N hours
- Per-feed keyword overrides
