import os
import re
import shutil
import subprocess

import pytest
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


def steps():
    return load("run_agent.yml")[1]["jobs"]["run-agent"]["steps"]


def by_name(name):
    return next(s for s in steps() if s.get("name") == name)


def test_state_commit_runs_whatever_the_agent_did_and_the_run_still_fails():
    all_steps = steps()
    agent, commit, final = by_name("Run agent"), by_name("Commit state"), by_name("Fail the run if the agent failed")
    assert agent["id"] == "agent"
    assert "success()" not in commit["if"] and "!cancelled()" in commit["if"]       # not skipped by a failed agent step
    assert "!cancelled()" in final["if"] and "steps.agent.outputs.exit_code" in final["if"]
    names = [s.get("name") for s in all_steps]
    assert names.index("Run agent") < names.index("Commit state") < names.index("Fail the run if the agent failed")
    assert names[-1] == "Fail the run if the agent failed"      # the red mark comes last, after the commit


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_agent_step_script_captures_the_exit_code_without_failing_and_final_step_re_exits(tmp_path):
    fakebin = tmp_path / "bin"; fakebin.mkdir()
    (fakebin / "python").write_text("#!/bin/sh\nexit ${FAKE_EXIT}\n")
    (fakebin / "python").chmod(0o755)
    out = tmp_path / "out"

    def run_step(script, **env):
        f = tmp_path / "step.sh"; f.write_text(script)
        return subprocess.run(["bash", "--noprofile", "--norc", "-e", str(f)], capture_output=True, text=True,
                              env={"PATH": f"{fakebin}:/usr/bin:/bin", "AGENT": "x", "GITHUB_OUTPUT": str(out), **env})

    for code in ("0", "1", "4"):
        out.write_text("")
        r = run_step(by_name("Run agent")["run"], FAKE_EXIT=code)
        assert r.returncode == 0, r.stderr                     # the step itself never fails the job
        assert out.read_text().strip() == f"exit_code={code}"  # but it records what happened
    r = run_step(by_name("Fail the run if the agent failed")["run"], EXIT_CODE="4")
    assert r.returncode == 4 and "::error::" in r.stdout        # the run turns red with the agent's own code


def test_final_step_is_skipped_when_the_agent_succeeded_or_did_not_run():
    cond = by_name("Fail the run if the agent failed")["if"]
    assert "exit_code != ''" in cond and "exit_code != '0'" in cond


def test_reset_and_seed_inputs_and_step_conditions():
    text, wf = load("run_agent.yml")
    inputs = (wf.get(True) or wf.get("on"))["workflow_dispatch"]["inputs"]
    assert {"reset_state", "seed"} <= set(inputs)
    assert by_name("Reset state (recovery)")["if"] == "${{ inputs.reset_state }}"
    assert by_name("Seed state")["if"] == "${{ inputs.seed }}"
    agent_if = by_name("Run agent")["if"]
    assert "!inputs.reset_state" in agent_if and "!inputs.seed" in agent_if
    all_steps = [s.get("name") for s in steps()]
    assert all_steps.index("Reset state (recovery)") < all_steps.index("Seed state")   # reset first, then seed
