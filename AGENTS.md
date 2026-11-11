# AGENTS.md

Instructions for AI coding agents. Two jobs: **(A) set up a news agent for a user's topic**, and **(B) work on the framework itself**. Check which one the user wants.

## What this is

A config-driven framework: it scrapes pages, uses an LLM to pick out items that match a topic, and sends a short message to Telegram, Signal, or the console. A topic is one YAML file in `agents/`. The shared package `newsagent/` is topic-agnostic; a test enforces that it contains no domain-specific terms.

## A. Set up an agent for the user's topic

1. **Ask** what they want to be notified about, how often it changes, and where they want the messages (Telegram, Signal, or just console for now). Ask for no secrets.
2. **Create their repo** from the template (`gh repo create <name> --template anniebritton/news-agent --public --clone`, or they click "Use this template"). Work in their repo.
3. **Find feeds.** Pick 3 to 8 pages that list fresh links on the topic (news indexes, blogs, team/vendor pages). **Check each URL returns 200 using a normal browser User-Agent** (the fetcher sends a Chrome UA). Drop ones that return 403/429 or show only JavaScript shells; say so to the user.
4. **Write `agents/<id>.yaml`** by copying `agents/TEMPLATE.yaml`. Look at `agents/example_launches.yaml` for a finished one.
   - `keywords`: words likely in relevant *link titles*; this cheap pre-filter decides what reaches the LLM. Too narrow misses news; empty sends everything (costly).
   - `prompts.scope.include/exclude`: be concrete about what counts. State the recency window.
   - Decide what `subject` and `group` mean for this topic (e.g. mission / provider) and write the `item_template` and the `task` prompt to match. `prompts.task` must contain `{indexed_text}` and `{date_str}`.
   - Env var names for credentials: `<ID>_TELEGRAM_BOT_TOKEN`, `<ID>_TELEGRAM_CHAT_ID` (or the Signal ones). Config holds the *names*, never the values.
5. **Dry run.** Needs a Gemini API key in the environment: `GEMINI_API_KEY=... python main.py --agent <id> --dry-run`. It prints the message and does not save state. Read the output with the user and tune keywords and scope until the items are what they want. An empty result with no errors can mean the keywords are too narrow; check the logs.
6. **Credentials: never ask the user to paste secrets into the chat, and never write them into files.** Tell them how to create them:
   - Gemini key: aistudio.google.com.
   - Telegram: message @BotFather, `/newbot`, copy the full `123456:ABC...` token. Add the bot to the target chat (as admin for channels), send a message, then read the chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates` (negative for groups/channels). They run these themselves.
   - GitHub secrets: have them run `gh secret set NAME` (the value is entered at their prompt).
7. **Automation (optional).** Copy `.github/workflows/run_agent.yml` to `run_<id>.yml`; change `name`, `AGENT`, and the secret names; uncomment the `schedule` (choose a cron the user agrees with: each run that finds news commits the state file). Run the workflow once via `workflow_dispatch` and confirm the message arrives.
8. **Report** the files you created, what each keyword/scope choice is for, and what the user still has to do (secrets, enabling the schedule).

## B. Work on the framework

```bash
pip install -r requirements.txt
python -m pytest tests/ -v          # no API keys or network needed; run before pushing
python main.py --agent <id> [--dry-run]   # run from the repo root (state paths are relative)
```

- Keep `newsagent/` topic-agnostic. Topic specifics (feeds, prompts, labels) belong in `agents/*.yaml`.
- Inside `newsagent/` use relative imports; `main.py` and tests use `newsagent.*`.
- The extractor's item fields (`subject`, `group`, `summary`, `translated_title`) are the contract between the LLM output and `messages.item_template`. A custom `prompts.output_format` must keep those names.
- New channel: a class decorated `@register("name")` in `newsagent/notifiers/`, taking its channel config dict, implementing `async send(message: Message)`, imported in `notifiers/__init__.py`. Channels receive a `Message`, never pre-formatted text. See `CONTRIBUTING.md`.
- New LLM provider: implement `generate(prompt) -> Optional[str]` and add a branch in `build_provider` (`newsagent/llm.py`).
- Gemini model names change often. Check Google's deprecations page before editing defaults.
- Add a test with every change; fake the LLM and HTTP calls like the existing tests. Note the change in `CHANGELOG.md`.
