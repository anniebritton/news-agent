"""CLI entry point: python main.py --agent <id> [--dry-run | --check | --seed | --reset-state] [--limit N]

Exit codes:
  0  ok
  1  failure: no channel delivered, the LLM failed for every batch, or every feed was unreachable
  2  configuration problem (bad config, missing secret/API key)
  3  corrupt state file (recover with --reset-state)
  4  partial delivery: at least one channel delivered and at least one failed (state WAS saved)

Delivery semantics: state is saved once at least one channel accepted the message (at-least-once
per run). A channel that failed while another succeeded does not get that message again. If the
process crashes after sending but before saving, the next run re-sends the same items (duplicates
are possible, loss is not).
"""
import argparse
import asyncio
import logging
import os
import sys

from newsagent import ConfigError, Message, NewsAgent, StateCorruptError, load_config
from newsagent.config import normalize_agent_id
from newsagent.errors import FeedsUnreachable
from newsagent.notifiers import ConsoleNotifier, build_notifiers
from newsagent.redact import redact
from newsagent.state import reset_state_file, state_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_STATE, EXIT_PARTIAL = 0, 1, 2, 3, 4

GENERIC_ERROR = "Critical failure in the news processor. See the run logs for details."
LLM_ERROR = "The LLM failed for every batch this run. Nothing was lost; the same items will be retried next run."
FEEDS_ERROR = ("None of the configured feeds could be fetched (network problem, blocking, or wrong URLs). "
               "This is an outage, not a quiet day. Nothing was lost.")


def feeds_note(result) -> str:
    if not result.feeds_failed:
        return ""
    return f"Note: {result.feeds_failed} of {result.feeds_total} feeds could not be fetched; results may be incomplete."


async def broadcast(notifiers, message: Message) -> int:
    """Send to every channel (one failing must not block the rest). Returns how many accepted it."""
    delivered = 0
    for n in notifiers:
        try:
            if await n.send(message):
                delivered += 1
            else:
                logger.error(f"{type(n).__name__} did not deliver the message")
        except Exception as e:
            logger.error(f"{type(n).__name__} failed: {redact(e)}")
    return delivered


async def run_check(agent: NewsAgent, limit) -> int:
    reports, candidates = await agent.check(limit)
    print(f"{'feed':70} {'fetched':8} {'links':>6} {'match':>6} {'new':>5}")
    for r in reports:
        print(f"{r.url[:70]:70} {'yes' if r.fetched else 'NO':8} {r.links:>6} {r.matched:>6} {r.new:>5}")
    print(f"\n{len(candidates)} candidate link(s) would be sent to the LLM:")
    for c in candidates:
        print(f"  - {c['title'][:90]}\n    {c['url']}")
    if not any(r.links for r in reports):
        print("\nNo links were extracted from any feed. Pages must be HTML with real <a href> links; "
              "RSS/Atom feeds are not supported, and JavaScript-rendered pages show nothing.", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def do_reset_state(agent_id: str) -> int:
    try:
        load_config(agent_id)
    except (FileNotFoundError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_CONFIG
    path = state_path(agent_id)
    backup = reset_state_file(path)
    print(f"State reset: wrote empty state to {path}" + (f" (previous file moved to {backup})" if backup else ""))
    print("The next run will treat everything currently on the pages as new. To avoid that, run "
          f"`python main.py --agent {agent_id} --seed` first. In CI the old file is still in git history.")
    return EXIT_OK


async def run(agent_id: str, dry_run: bool = False, check: bool = False, seed: bool = False,
              limit=None, reset_state: bool = False) -> int:
    agent_id = normalize_agent_id(agent_id)
    if reset_state:
        return do_reset_state(agent_id)
    try:
        config = load_config(agent_id)
        agent = NewsAgent(config)
    except (FileNotFoundError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_CONFIG
    except StateCorruptError as e:
        print(f"error: {e}\nRecover with: python main.py --agent {agent_id} --reset-state", file=sys.stderr)
        return EXIT_STATE

    messages = config.get("messages") or {}
    label = agent_id.upper()
    header = messages.get("header", f"{label} UPDATES")
    status = messages.get("status_label", f"{label} STATUS")
    error = messages.get("error_label", f"{label} AGENT ERROR")

    try:
        if check:
            return await run_check(agent, limit)
        if seed:
            try:
                count = await agent.seed(limit)
            except FeedsUnreachable as e:
                print(f"error: {e}", file=sys.stderr)
                return EXIT_FAILED
            print(f"Seeded {count} link(s) as already seen. The next run will only report newer items.")
            return EXIT_OK

        notifiers = [ConsoleNotifier()] if dry_run else build_notifiers(config)
        if not dry_run:  # fail fast, before paying for any LLM call
            problems = [p for n in notifiers for p in getattr(n, "missing", lambda: [])()]
            if problems:
                print("error: required notification secrets are not set: " + ", ".join(problems), file=sys.stderr)
                return EXIT_CONFIG
        agent.ensure_llm()  # friendly error if the API key is missing

        try:
            result = await agent.run_round(limit)
            if result.all_feeds_failed:
                await broadcast(notifiers, Message(header=f"⚠️ {error}", footer=FEEDS_ERROR))
                return EXIT_FAILED
            if result.all_failed:
                await broadcast(notifiers, Message(header=f"❌ {error}", footer=LLM_ERROR))
                return EXIT_FAILED
            note = feeds_note(result)
            if result.lines:
                message = Message(header=header, lines=result.lines)
                footer = [t for t in (f"{result.deferred} more item(s) will follow in the next run."
                                      if result.deferred else "", note) if t]
                message.footer = "\n".join(footer) or None
            else:
                span = (f"{result.window_hours:.1f} hours" if result.window_hours >= 1.0
                        else f"{int(result.window_hours * 60)} minutes")
                message = Message(header=f"ℹ️ {status}",
                                  footer="\n".join(t for t in (f"No new updates found in the last {span}.", note) if t))
            delivered = await broadcast(notifiers, message)
        except Exception as e:
            logger.error(f"Error in main loop: {redact(e)}")
            await broadcast(notifiers, Message(header=f"❌ {error}", footer=GENERIC_ERROR))
            return EXIT_FAILED

        if dry_run:
            return EXIT_OK  # dry runs never touch state
        if delivered == 0 and result.lines:
            logger.error("No channel accepted the message; state NOT saved, items will be retried.")
            return EXIT_FAILED
        agent.commit()  # nothing lost if there was nothing to deliver, so commit even if only the status failed
        if delivered == 0:
            return EXIT_FAILED
        if delivered < len(notifiers):
            logger.error(f"PARTIAL DELIVERY: {len(notifiers) - delivered} of {len(notifiers)} channel(s) did not "
                         "receive this message and will not get it again (state was saved because another channel did).")
            return EXIT_PARTIAL
        return EXIT_OK
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_CONFIG
    finally:
        await agent.close()


def positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Config-driven news agent")
    parser.add_argument("--agent", default=os.getenv("AGENT", ""), help="Agent id (the file name in agents/, without .yaml)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print results to the console; do not notify or save state")
    mode.add_argument("--check", action="store_true", help="Fetch feeds and list candidate links. No LLM, no API key, no state writes")
    mode.add_argument("--seed", action="store_true", help="Record current links as seen without notifying (avoids a first-run flood)")
    mode.add_argument("--reset-state", action="store_true", help="Recover from a corrupt state file: move it aside and start with empty state")
    parser.add_argument("--limit", type=positive_int, default=None, help="Process at most N candidate links (N >= 1)")
    return parser


def cli(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.agent:
        print("Error: --agent <id> is required (or set AGENT env var)", file=sys.stderr)
        return EXIT_CONFIG
    try:
        return asyncio.run(run(args.agent, args.dry_run, args.check, args.seed, args.limit, args.reset_state))
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # one redacted line instead of a traceback that might carry a secret
        print(f"error: {type(e).__name__}: {redact(e)}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(cli())
