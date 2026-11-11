"""CLI entry point: python main.py --agent <id>   (or set the AGENT env var)."""
import argparse
import asyncio
import logging
import os
import sys

from newsagent import NewsAgent, load_config
from newsagent.notifiers import build_notifiers
from newsagent import Message

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def run(agent_id: str):
    config = load_config(agent_id)
    messages = config.get("messages", {})
    label = agent_id.upper()
    header = messages.get("header", f"{label} UPDATES")
    status = messages.get("status_label", f"{label} STATUS")
    error = messages.get("error_label", f"{label} AGENT ERROR")

    agent = NewsAgent(config)
    notifiers = build_notifiers(config)

    async def broadcast(message: Message):
        for n in notifiers:  # one failing channel must not block the rest
            try:
                await n.send(message)
            except Exception as e:
                logger.error(f"{type(n).__name__} failed: {e}")

    try:
        lines, window_hours = await agent.run_round()
        if not lines:
            span = f"{window_hours:.1f} hours" if window_hours >= 1.0 else f"{int(window_hours * 60)} minutes"
            await broadcast(Message(header=f"ℹ️ {status}", footer=f"No new updates found in the last {span}."))
            return
        await broadcast(Message(header=header, lines=lines))
    except Exception as e:
        logger.error(f"Error in main loop: {e}")
        await broadcast(Message(header=f"❌ {error}", footer=f"Critical failure in news processor: {e}"))
    finally:
        await agent.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Config-driven news agent")
    parser.add_argument("--agent", default=os.getenv("AGENT", ""), help="Agent ID (file name in agents/)")
    args = parser.parse_args()
    if not args.agent:
        print("Error: --agent <id> is required (or set AGENT env var)", file=sys.stderr)
        sys.exit(1)
    asyncio.run(run(args.agent))
