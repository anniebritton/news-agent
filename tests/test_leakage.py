import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_sports_or_betting_terms_in_repo():
    banned = ["betting", "liiga", "hockey", "goaltender", "sportsbook", "wager", "odds"]
    skip_dirs = {".git", "__pycache__", ".pytest_cache", "venv", ".venv"}
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in files:
            if fn == "test_leakage.py":
                continue
            path = os.path.join(dirpath, fn)
            try:
                text = open(path, encoding="utf-8").read().lower()
            except (UnicodeDecodeError, OSError):
                continue
            for term in banned:
                assert term not in text, f"'{term}' found in {os.path.relpath(path, ROOT)}"
