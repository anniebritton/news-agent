# News Agent

A small, config-driven framework for agents that watch web pages, use an LLM to pick out and summarize
what matters to you, and send the result to a messaging channel (Telegram, Signal, or your own). Point it at any topic (security advisories, product
releases, regulatory filings, local news...) by writing one YAML file. No code changes needed.

## How it works

1. **Scrape**: fetches all configured feed pages in parallel and extracts links.
2. **Pre-filter**: keeps only links whose titles match your keywords (saves LLM cost).
3. **Extract**: fetches each article and asks the LLM, in batches, for structured items that fit your scope.
4. **Deduplicate**: skips anything already seen or older than the lookback window (state is persisted per agent).
5. **Notify**: renders one channel-neutral message for every configured channel, or a short "nothing new" status.

## Layout

```
newsagent/
  agent.py        Orchestrator (NewsAgent)
  scraper.py      Link discovery + keyword pre-filter
  extractor.py    LLM batch extraction into structured items
  llm.py          LLMProvider protocol + Gemini implementation
  fetcher.py      HTTP fetch + HTML -> text
  state.py        Atomic JSON state (processed IDs, seen URLs, last run)
  config.py       Loading/validation
  messages.py     Channel-neutral Message/Line model
  notifiers/      Notifier registry + Telegram, Signal, console
agents/           One YAML per agent (TEMPLATE.yaml, example_security.yaml)
state/            Per-agent state files
main.py           CLI: python main.py --agent <id>
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp agents/TEMPLATE.yaml agents/myagent.yaml    # edit it
GEMINI_API_KEY=... MYAGENT_TELEGRAM_BOT_TOKEN=... MYAGENT_TELEGRAM_CHAT_ID=... \
  python main.py --agent myagent
```

The bot token is the full `123456:ABC...` string. The chat ID is negative for groups/channels, positive for direct chats.

## Configuring an agent

See `agents/TEMPLATE.yaml` (annotated) and `agents/example_security.yaml` (working example). The main knobs:

- `feeds`: pages to scrape
- `keywords`: title pre-filter (flat list, or dict of lists)
- `prompts`: `system`, `task` (must contain `{indexed_text}` and `{date_str}`), `scope.include/exclude`
- `messages`: header/status/error labels and `item_template` using `{group} {subject} {title} {summary}` (the source link is added for you`
- `notify.channels`: one or more channels (`telegram`, `signal`, `console`), each naming the env vars that hold its credentials
- `settings`: lookback window, batch size, summary length, `require_subject`

The extractor returns items with `subject`, `group`, `summary`, and `title`; what those mean is up to your prompt
(e.g. subject = product, group = vendor).

## Extending

- **Another LLM**: implement `generate(prompt) -> Optional[str]` (see `LLMProvider`) and pass it as `NewsAgent(config, llm=...)`, or add a branch in `build_provider`.
- **Another channel** (Slack, email, Discord...): add a module in `newsagent/notifiers/` with a class decorated `@register("name")` that takes its channel config dict and implements `async send(message: Message)`, import it in `notifiers/__init__.py`, then use `type: name` in config. `Message.to_plain()` gives a ready-made plain-text rendering.

Signal has no official bot API; the built-in channel talks to a self-hosted [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) server.

## Running on GitHub Actions

`.github/workflows/run_agent.yml` runs tests, runs the agent on a schedule, and commits the state file with
`[skip ci]`. Copy it per agent, change the `AGENT` env var, secret names, and name. Add `GEMINI_API_KEY` plus
the secrets for the agent's channels as repo secrets.

## Tests

```bash
python -m pytest tests/ -v
```

No API keys or network needed; the LLM and HTTP calls are faked.
