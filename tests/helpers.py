import json
from datetime import datetime, timedelta

from newsagent import NewsAgent


class FakeLLM:
    """response: a string, None, or a callable(prompt) -> str|None."""
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.response(prompt) if callable(self.response) else self.response


def cfg(**over):
    base = {
        "agent": {"id": "t", "name": "Test"},
        "feeds": ["https://feed.test/"],
        "keywords": ["alpha"],
        "prompts": {"system": "s", "task": "{indexed_text} {date_str}", "scope": {"include": ["x"]}},
        "notify": {"channels": [{"type": "telegram", "bot_token_env": "T_TOKEN", "chat_id_env": "T_CHAT"}]},
        "settings": {"batch_size": 2},
    }
    base.update(over)
    return base


def llm_items(*entries):
    """entries: (index, title) tuples or full dicts."""
    out = []
    for e in entries:
        if isinstance(e, dict):
            out.append(e)
        else:
            out.append({"index": e[0], "translated_title": e[1], "subject": "Acme", "group": "G", "summary": "Happened"})
    return json.dumps(out)


def hours_ago(h):
    return (datetime.now() - timedelta(hours=h)).isoformat(timespec="seconds")


FEED_PAGE = "[Alpha one](https://a.test/1) [Alpha two](https://a.test/2)"


def patch_pages(agent, pages=None, fail=()):
    """Replace the agent's network fetch. Feed page defaults to two alpha links."""
    pages = {"https://feed.test/": FEED_PAGE, **(pages or {})}

    async def fetch(url):
        if url in fail:
            return None
        return pages.get(url, "article body")
    agent.fetcher.fetch_url = fetch
    return agent


def make_agent(tmp_path, llm, pages=None, fail=(), **over):
    return patch_pages(NewsAgent(cfg(**over), llm=llm, state_dir=str(tmp_path)), pages, fail)
