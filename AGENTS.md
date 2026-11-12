# AGENTS.md

Instructions for AI coding agents. Two jobs: **(A) set up a news agent for a user's topic**, and **(B) work on the framework itself**. Check which one the user wants.

## What this is

A config-driven framework: it scrapes pages, uses an LLM to pick out items that match a topic, and sends a short message to Telegram, Signal, or the console. A topic is one YAML file in `agents/`. The shared package `newsagent/` is topic-agnostic; a test enforces that it contains no domain-specific terms.

## Rules that apply to everything you do

- **Secrets never go through you.** Do not ask the user to paste an API key, bot token, or chat id into the chat. Do not write one into a file, a command line, or shell history (no `KEY=... python ...`). The user exports secrets in their own terminal (`export GEMINI_API_KEY=...`) or sets them as GitHub secrets with `gh secret set NAME` (the value is typed at the user's prompt). Config files hold env var *names*, never values.
- **Do not invent items, facts, or URLs.** Everything in a config (feeds, keywords, scope) must come from pages you actually fetched. If something isn't on the page, it isn't a result.
- **Id, not filename.** `--agent <id>` takes the id (`fda_shortages`), not `fda_shortages.yaml`. (The CLI tolerates the extension, but the id is what names the state file.)

## A. Set up an agent for the user's topic

1. **Ask** what they want to be notified about, how often it changes, and where the messages should go (Telegram, Signal, or just the console for now). Also ask whether the repo they create should be **public or private**; their state files and config live in it.
2. **Create their repo** from the template (`gh repo create <name> --template anniebritton/news-agent --private|--public --clone`, as they chose). Work in their repo.
3. **Find feeds, and verify them with the tool, not just with curl.** Feeds must be **HTML pages that contain real links** (news indexes, blog homepages, vendor/agency pages). **RSS/Atom feeds are NOT supported**: they return 200 but yield zero links. Pages that render their links with JavaScript show nothing either. Pick 3 to 8 candidates, write a first `agents/<id>.yaml` (step 4), then run:
   ```bash
   python main.py --agent <id> --check
   ```
   `--check` needs **no API key** and writes nothing. It prints, per feed, whether it was fetched and how many links it produced, then the candidate links that would go to the LLM. Drop feeds that show `NO` (blocked, 403/429) or `0` links. If a page returns 200 to curl but 0 links here, it's JavaScript or RSS: drop it.
4. **Write `agents/<id>.yaml`** by copying `agents/TEMPLATE.yaml`; see `agents/example_launches.yaml` for a finished one.
   - `keywords`: words likely in the **link titles** on those pages. This cheap pre-filter decides what reaches the LLM. **List-style pages** (a database of drug names, a table of products) have link text that is just a name, so topic words like "shortage" match nothing, and generic words match everything. Check the count `--check` reports and tune.
   - `prompts.scope.include/exclude`: be concrete about what counts.
   - Decide what `subject` and `group` mean for this topic (e.g. mission / provider) and write `item_template` and the `task` prompt to match. `prompts.task` must contain `{indexed_text}` and `{date_str}`. **Literal braces must be doubled** (`{{` and `}}`, e.g. in a JSON example); config validation tells you if you forget.
   - **Time window semantics.** `time_window_hours` skips items whose *stated* publication date (the LLM reports it when the article gives one) is older than the window; later runs use the time since the last run. Items with no stated date are not age-filtered; they are deduplicated by URL. The `Anything older than N hours` line in `scope.exclude` is only a hint to the LLM. Don't promise the user more than that.
   - Env var names for credentials: `<ID>_TELEGRAM_BOT_TOKEN`, `<ID>_TELEGRAM_CHAT_ID` (or the Signal ones). Config holds the *names*.
5. **Prevent a first-run flood.** On the first real run everything currently on the pages counts as new. For list-style pages that can be hundreds of items. Before the first real run, record what's already there:
   ```bash
   python main.py --agent <id> --seed
   ```
   `--seed` needs no API key, calls no LLM, sends nothing, and writes `state/<id>_processed_news.json`. Commit that file so scheduled runs start from it. After seeding, the agent only reports links that appear later.
6. **Dry run.** The LLM needs `GEMINI_API_KEY`. **Ask the user to run the dry run in their own terminal**, with the key exported there (never inline, never pasted to you): `python main.py --agent <id> --dry-run`. It prints the message and saves nothing. Use `--limit 5` to cap cost while tuning. If you have no key, stop after `--check` and tell the user the dry run is pending; do not fake output. Read the real output with the user and tune keywords and scope. An empty result without errors can mean the keywords are too narrow.
7. **Credentials, created by the user:**
   - Gemini key: aistudio.google.com.
   - Telegram: message @BotFather, `/newbot`, copy the full `123456:ABC...` token. Add the bot to the target chat (as admin for channels) and send it a message. To find the chat id the user opens `https://api.telegram.org/bot<TOKEN>/getUpdates` **themselves, in their own terminal or browser, and does not paste the output to you** (it contains personal data and the token is in the URL). Chat ids are negative for groups/channels.
   - GitHub secrets: `gh secret set NAME` for `GEMINI_API_KEY` and each channel secret.
8. **Automation (optional).** Copy `.github/workflows/run_agent.yml` to `run_<id>.yml`; change `name`, `AGENT`, and the secret names, and check every env var name matches the YAML exactly. Uncomment the `schedule` with a cron the user agrees to. How it works, so you can explain it:
   - The run commits `state/<id>_processed_news.json` back to the repo (that's how it remembers what it already sent). The commit uses `[skip ci]`.
   - The workflow uses a concurrency group and rebases before pushing, so overlapping runs queue instead of racing.
   - **A green run means the program finished, not that a message was necessarily useful.** The program exits non-zero when no channel accepts the message or the LLM fails for every batch, and in that case it does *not* save state, so the next run retries the same items. A frequent schedule creates many small state commits.
   - Run it once with `workflow_dispatch` and have the user confirm the message arrived.
9. **Report** the files you created, what each keyword/scope choice is for, and what the user still has to do (secrets, seeding, enabling the schedule).

## B. Work on the framework

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v                # no API keys or network needed; run before pushing
python main.py --agent <id> [--check | --seed | --dry-run] [--limit N]   # run from the repo root
```

- Keep `newsagent/` topic-agnostic. Topic specifics (feeds, prompts, labels) belong in `agents/*.yaml`.
- Inside `newsagent/` use relative imports; `main.py` and tests use `newsagent.*`.
- **State is written only by `NewsAgent.commit()`, and `main.py` calls it only after a channel accepted the message.** Never mark URLs or IDs seen earlier (that was a bug class: a failed LLM call or a failed send used to lose items silently). The scraper only reads state.
- **Network safety lives in `newsagent/fetcher.py`**: http(s) only, non-public addresses refused on every redirect hop, 2 MB streamed cap, text content types only, failures return `None`. Don't bypass `fetch_url`. Known limitation: DNS rebinding.
- **Never log or send exception text that might contain a secret.** Use `newsagent.redact.redact`. The channel only ever gets fixed generic error text.
- Dedupe keys on the canonical source URL (`newsagent/urls.py`), not on LLM-written titles.
- The extractor's item fields (`subject`, `group`, `summary`, `translated_title`, optional `published`) are the contract between LLM output and `messages.item_template`. A custom `prompts.output_format` must keep those names.
- New channel: a class decorated `@register("name")` in `newsagent/notifiers/`, taking its channel config dict, implementing `async send(message: Message) -> bool` (True only if fully delivered), imported in `notifiers/__init__.py`. Channels receive a `Message`, never pre-formatted text; render links through `safe_url`. See `CONTRIBUTING.md`.
- New LLM provider: implement `generate(prompt) -> Optional[str]` and add a branch in `build_provider` (`newsagent/llm.py`).
- Gemini model names change often. Check Google's deprecations page before editing defaults.
- Add a test with every change, and make sure it fails without the change; fake the LLM and HTTP calls like the existing tests. Note the change in `CHANGELOG.md`.
