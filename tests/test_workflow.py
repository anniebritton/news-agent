import os
import re

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    path = os.path.join(ROOT, ".github", "workflows", name)
    text = open(path).read()
    return text, yaml.safe_load(text)


def test_run_workflow_structure():
    text, wf = load("run_agent.yml")
    triggers = wf.get(True) or wf.get("on")                       # YAML parses a bare `on:` as True
    assert "reset_state" in triggers["workflow_dispatch"]["inputs"]
    assert wf["concurrency"]["cancel-in-progress"] is False
    steps = wf["jobs"]["run-agent"]["steps"]
    names = [s.get("name", "") for s in steps]
    assert "Reset state (recovery)" in names and "Run agent" in names
    assert not any("pytest" in str(s.get("run", "")) for s in steps), "tests must not run in the delivery job"
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["persist-credentials"] is False
    run_step = next(s for s in steps if s.get("name") == "Run agent")
    assert "inputs.reset_state" in run_step["if"]


def test_state_push_retries_include_the_pull():
    text, _ = load("run_agent.yml")
    loop = re.search(r"for attempt in .*?\n(.*?)\n\s*done", text, re.S).group(1)
    assert "git pull --rebase" in loop and "git push" in loop
    assert text.index("for attempt") < text.index("git pull --rebase"), "pull must not run before the retry loop"


def test_tests_workflow_runs_dev_requirements():
    text, wf = load("tests.yml")
    assert "requirements-dev.txt" in text
