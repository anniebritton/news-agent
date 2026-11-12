"""CLI entry point: python main.py --agent <id> [--dry-run | --check | --seed] [--limit N]

Exit codes: 0 ok, 1 delivery/LLM failure, 2 configuration problem, 3 corrupt state file.
"""
import argparse
import asyncio
import logging
import os
import sys

from newsagent import ConfigError, Message, NewsAgent, StateCorruptError, load_config
from newsagent.config import normalize_agent_id
from newsagent.notifiers import ConsoleNotifier, build_notifiers
from newsagent.redact import redact

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

GENERIC_ERROR = "Critical failure in the news processor. See the run logs for details."
LLM_ERROR = "The LLM failed for every batch this run. Nothing was lost; the same items will be retried next run."


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
        return 1
    return 0


async def run(agent_id: str, dry_run: bool = False, check: bool = False, seed: bool = False, limit=None) -> int:
    agent_id = normalize_agent_id(agent_id)
    try:
        config = load_config(agent_id)
        agent = NewsAgent(config)
    except (FileNotFoundError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except StateCorruptError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    messages = config.get("messages") or {}
    label = agent_id.upper()
    header = messages.get("header", f"{label} UPDATES")
    status = messages.get("status_label", f"{label} STATUS")
    error = messages.get("error_label", f"{label} AGENT ERROR")

    try:
        if check:
            return await run_check(agent, limit)
        if seed:
            count = await agent.seed(limit)
            print(f"Seeded {count} link(s) as already seen. The next run will only report newer items.")
            return 0

        agent.ensure_llm()  # friendly error if the API key is missing
        notifiers = [ConsoleNotifier()] if dry_run else build_notifiers(config)
        try:
            result = await agent.run_round(limit)
            if result.all_failed:
                await broadcast(notifiers, Message(header=f"❌ {error}", footer=LLM_ERROR))
                return 1
            if result.lines:
                message = Message(header=header, lines=result.lines)
                if result.deferred:
                    message.footer = f"{result.deferred} more item(s) will follow in the next run."
            else:
                span = (f"{result.window_hours:.1f} hours" if result.window_hours >= 1.0
                        else f"{int(result.window_hours * 60)} minutes")
                message = Message(header=f"ℹ️ {status}", footer=f"No new updates found in the last {span}.")
            delivered = await broadcast(notifiers, message)
        except Exception as e:
            logger.error(f"Error in main loop: {redact(e)}")
            await broadcast(notifiers, Message(header=f"❌ {error}", footer=GENERIC_ERROR))
            return 1

        if dry_run:
            return 0  # dry runs never touch state
        if delivered == 0 and result.lines:
            logger.error("No channel accepted the message; state NOT saved, items will be retried.")
            return 1
        agent.commit()  # nothing lost if there was nothing to deliver, so commit even if only the status failed
        return 0 if delivered else 1
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        await agent.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Config-driven news agent")
    parser.add_argument("--agent", default=os.getenv("AGENT", ""), help="Agent id (the file name in agents/, without .yaml)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print results to the console; do not notify or save state")
    mode.add_argument("--check", action="store_true", help="Fetch feeds and list candidate links. No LLM, no API key, no state writes")
    mode.add_argument("--seed", action="store_true", help="Record current links as seen without notifying (avoids a first-run flood)")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N candidate links")
    args = parser.parse_args()
    if not args.agent:
        print("Error: --agent <id> is required (or set AGENT env var)", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(run(args.agent, args.dry_run, args.check, args.seed, args.limit)))
