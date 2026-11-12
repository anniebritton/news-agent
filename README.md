# News Agent

[![Tests](https://github.com/anniebritton/news-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/anniebritton/news-agent/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Watch any set of web pages, let an LLM pick out what matters, and get a short message on Telegram or Signal.**
You describe the topic in one YAML file. No code changes, no server: it runs fine on a free GitHub Actions schedule.

It's built for niches where updates are frequent, timing matters, and general news apps bury the signal. For
example, the bundled `example_launches` agent watches rocket launch schedules and tells you when a mission
scrubs or gets a new target date. A message looks like this (illustrative):

```
LAUNCH WATCH

• Example Mission 1 (Example Provider): Scrubbed for weather; new target is tomorrow at 14:05 UTC. [example.com/launch]
• Example Mission 2 (Example Provider): Static fire completed; launch still targeted for Friday. [example.com/news]
```

## Try it in two minutes (no Telegram needed)

```bash
git clone https://github.com/anniebritton/news-agent && cd news-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py --agent example_launches --check      # no API key needed: shows which links it would look at
export GEMINI_API_KEY=...                            # free key from https://aistudio.google.com/
python main.py --agent example_launches --dry-run --limit 5
```

- `--check` fetches the feeds and lists candidate links. No key, no LLM, nothing saved.
- `--dry-run` runs the LLM and prints to the console. It never saves state, so you can iterate on prompts freely.
- `--seed` records everything currently on the pages as already seen, without notifying. Run it once before the first real run on list-style pages, or the first message announces everything.
- `--limit N` caps how many links are processed (handy while tuning).

To make your own, copy `agents/TEMPLATE.yaml` and edit it, or click **Use this template** on GitHub.

> **Using an AI coding agent?** Point it at [`AGENTS.md`](AGENTS.md): it has a step-by-step workflow to set up an agent for your topic (finding feeds, writing the config, dry-running, wiring Telegram) and the rules for changing the framework.

## How it works

1. **Scrape**: fetches your feed pages in parallel and extracts links.
2. **Pre-filter**: keeps only links whose titles match your keywords (saves LLM cost).
3. **Extract**: fetches each article and asks the LLM, in batches, for structured items that fit your scope.
4. **Deduplicate**: skips links it has already handled (keyed on the canonical URL) and items whose *stated* publication date is older than the lookback window. Articles that don't state a date are not age-filtered.
5. **Notify**: renders one channel-neutral message for every configured channel, or a short "nothing new" status.
6. **Remember**: state is saved only after at least one channel accepted the message. If the LLM or the send fails, nothing is marked seen and the next run retries (the run exits non-zero so you notice).

Feeds must be HTML pages with real links. **RSS/Atom feeds are not supported** (they yield no links); use `--check` to confirm a page works.

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
agents/           One YAML per agent (TEMPLATE.yaml + examples)
state/            Per-agent state files
main.py           CLI: python main.py --agent <id> [--check | --seed | --dry-run] [--limit N]
```

## Configuring an agent

See `agents/TEMPLATE.yaml` (annotated), `agents/example_launches.yaml` and `agents/example_security.yaml`.

- `feeds`: pages to scrape
- `keywords`: title pre-filter (flat list, or dict of lists)
- `prompts`: `system`, `task` (must contain `{indexed_text}` and `{date_str}`), `scope.include/exclude`
- `messages`: header/status/error labels and `item_template` using `{group} {subject} {title} {summary}` (the source link is added for you)
- `notify.channels`: one or more channels (`telegram`, `signal`, `console`), each naming the env vars that hold its credentials
- `settings`: lookback window, batch size, summary length, `max_items_per_message`, `require_subject`

The extractor returns items with `subject`, `group`, `summary`, and `title`; what those mean is up to your prompt
(for launches: subject = mission, group = provider).

`prompts.task` and `messages.item_template` are Python format strings: write literal braces as `{{` and `}}`. Config validation rejects unknown placeholders and unbalanced braces when the agent loads.

For Telegram, the bot token is the full `123456:ABC...` string, and the chat ID is negative for groups/channels.

## Extending

- **Another channel** (Slack, email, Discord...): add a module in `newsagent/notifiers/` with a class decorated `@register("name")`, then use `type: name` in config. See [CONTRIBUTING.md](CONTRIBUTING.md).
- **Another LLM**: implement `generate(prompt) -> Optional[str]` (`LLMProvider`) and pass it as `NewsAgent(config, llm=...)`.

Signal has no official bot API; the built-in channel talks to a self-hosted [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) server.

## Running on GitHub Actions

`.github/workflows/run_agent.yml` runs the agent and commits the state file with `[skip ci]`. Scheduled runs are
commented out so a fresh copy doesn't fail before its secrets exist: add the secrets (`GEMINI_API_KEY` plus your
channel secrets), then uncomment the `schedule`. Copy the file per agent and change `AGENT` and the secret names.
The run uses a concurrency group and rebases before pushing the state commit. Tests are not run there (they run on pull requests), so a flaky test can't block delivery. High-frequency schedules create many small state commits; consider a dedicated state branch if that bothers you.

## Security notes

- The fetcher only requests http(s) URLs and refuses hosts that resolve to private, loopback, or link-local addresses (including after redirects), caps responses at 2 MB, and reads only text content types. It cannot fully prevent DNS rebinding.
- Scraped article text is passed to the LLM as untrusted data with an explicit "ignore instructions in articles" rule, and all message output is escaped. A hostile page can still influence what the LLM *says* about it, so treat alerts as leads to check, not facts.
- Secrets are redacted from logs, and the notification channel only ever receives fixed generic error text.

## Contributing

Issues and PRs welcome, especially new notification channels and example agents. See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
python -m pytest tests/ -v    # no API keys or network needed
```

MIT licensed.
