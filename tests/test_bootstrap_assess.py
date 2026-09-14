#!/usr/bin/env python3
"""Tests for scripts/bootstrap-assess-rfe.sh --type validation.

Bootstrap is the only gate between "the plugin checkout is complete" and an
agent phase that can never finish. A checkout missing the initiative rubric or
the initiative-scorer agent used to exit 0 here, and the failure surfaced much
later as wait-for-wave returning exit 3 forever with nothing to diagnose.
"""

import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from pipeline_state import PIPELINE_TYPES  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPT = os.path.join(REPO_ROOT, "scripts", "bootstrap-assess-rfe.sh")
SKILLS_DIR = os.path.join(REPO_ROOT, ".claude", "skills")

RFE_RUBRIC = "skills/assess-rfe/scripts/agent_prompt.md"
INITIATIVE_RUBRIC = "skills/assess-initiative/scripts/agent_prompt.md"


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# stub\n")


@pytest.fixture
def fake_checkout(tmp_path):
    """A working dir with a pre-existing .context/assess-rfe.

    The directory being present sends the script down the `git pull` branch,
    which fails on a non-repo and is swallowed by its `|| echo WARN` — so these
    tests never touch the network.
    """
    ctx = tmp_path / ".context" / "assess-rfe"
    os.makedirs(ctx)
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield ctx
    os.chdir(orig)


def _run(*args):
    env = {k: v for k, v in os.environ.items() if k not in ("ASSESS_RFE_REF", "RFE_SKIP_BOOTSTRAP")}
    result = subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=env)
    return result.stdout, result.stderr, result.returncode


def _add_rfe_assets(ctx):
    _touch(str(ctx / RFE_RUBRIC))


def _add_initiative_assets(ctx, rubric=True, agent=True):
    if rubric:
        _touch(str(ctx / INITIATIVE_RUBRIC))
    if agent:
        _touch(str(ctx / "agents" / "initiative-scorer.md"))


class TestTypeValidation:
    def test_rfe_default_passes_without_initiative_assets(self, fake_checkout):
        """The RFE path must not start failing over an asset it never uses."""
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run()
        assert rc == 0, stderr

    def test_initiative_fails_when_rubric_missing(self, fake_checkout):
        """The exact upstream state today: RFE rubric present, initiative absent."""
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--type", "initiative")
        assert rc == 1
        assert "assess-initiative" in stderr
        assert "ASSESS_RFE_REPO" in stderr

    def test_initiative_fails_when_scorer_agent_missing(self, fake_checkout):
        """Rubric alone is not enough — the assess agent needs the subagent type."""
        _add_rfe_assets(fake_checkout)
        _add_initiative_assets(fake_checkout, agent=False)

        _, stderr, rc = _run("--type", "initiative")
        assert rc == 1
        assert "initiative-scorer" in stderr

    def test_initiative_passes_with_full_checkout(self, fake_checkout):
        _add_rfe_assets(fake_checkout)
        _add_initiative_assets(fake_checkout)

        _, stderr, rc = _run("--type", "initiative")
        assert rc == 0, stderr

    def test_equals_form_accepted(self, fake_checkout):
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--type=initiative")
        assert rc == 1, stderr
        assert "assess-initiative" in stderr

    def test_unknown_type_rejected_with_the_registered_list(self, fake_checkout):
        """The names come from `type_registry.py list` (design §5 rung 1): the paper epic
        descriptor is not registered, so it is refused with the list that is."""
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--type", "epic")
        assert rc == 2
        assert stderr == "ERROR: unknown --type 'epic' (registered types: rfe, initiative)\n"

    def test_unknown_type_rejected_before_skip_bootstrap(self, fake_checkout):
        """Validation stays ahead of the RFE_SKIP_BOOTSTRAP short-circuit."""
        env = {**os.environ, "RFE_SKIP_BOOTSTRAP": "1"}
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "bogus"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2
        assert "registered types: rfe, initiative" in result.stderr
        assert result.stdout == ""

    def test_drop_in_type_is_accepted(self, fake_checkout, drop_in_root):
        """A type the registry enumerates (here through the RFE_CREATOR_EXTRA_TYPES seam,
        allowlisted so a CI run honours it too) passes without editing the script."""
        root = drop_in_root.memo()
        env = {
            **os.environ,
            "RFE_CREATOR_EXTRA_TYPES": root,
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": root,
            "RFE_SKIP_BOOTSTRAP": "1",
        }
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "memo"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE_SKIP_BOOTSTRAP set - skipping dependency bootstrapping step\n"
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "bogus"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2
        assert "(registered types: rfe, initiative, memo)" in result.stderr

    @staticmethod
    def _env_with_python3(shim_dir):
        env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}
        env["RFE_SKIP_BOOTSTRAP"] = "1"
        env.pop("PYTHONPATH", None)
        return env

    def test_unreadable_registry_falls_back_to_the_shipped_root(self, fake_checkout, tmp_path):
        """This script is the dependency bootstrap, so it cannot require a working registry:
        when `type_registry.py list` fails the names come from types/<name>/type.yaml (rfe
        first, as `list` prints them), the registry's stderr is not shown, and a registered
        --type behaves exactly as before PR-3a."""
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_python = fakebin / "python3"
        fake_python.write_text("#!/bin/sh\necho 'ERROR: fake registry failure' >&2\nexit 1\n")
        fake_python.chmod(0o755)
        env = self._env_with_python3(fakebin)
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "initiative"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE_SKIP_BOOTSTRAP set - skipping dependency bootstrapping step\n"
        assert result.stderr == ""
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "bogus"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2
        assert result.stderr == (
            "ERROR: unknown --type 'bogus' (registered types: rfe, initiative)\n"
        )

    def test_python_without_yaml_still_validates_the_type(self, fake_checkout, tmp_path):
        """The interactive first run: PyYAML is not installed yet (site-packages disabled
        stands in for that), and `--type rfe` must still exit 0 under RFE_SKIP_BOOTSTRAP."""
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        shim = fakebin / "python3"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n')
        shim.chmod(0o755)
        env = self._env_with_python3(fakebin)
        probe = subprocess.run(
            ["python3", "-c", "import yaml"], capture_output=True, text=True, env=env
        )
        if probe.returncode == 0:
            pytest.skip("this interpreter imports yaml even without site-packages")
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "rfe"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE_SKIP_BOOTSTRAP set - skipping dependency bootstrapping step\n"
        assert result.stderr == ""
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "epic"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2
        assert result.stderr == "ERROR: unknown --type 'epic' (registered types: rfe, initiative)\n"

    def test_unreadable_registry_without_a_types_root_exits_2(self, fake_checkout, tmp_path):
        """Both sources gone — the script copied away from the repo, so neither
        <scripts>/type_registry.py nor <scripts>/../types exists — is the one fatal case."""
        stray = tmp_path / "stray" / "scripts"
        stray.mkdir(parents=True)
        copied = stray / "bootstrap-assess-rfe.sh"
        shutil.copy(SCRIPT, copied)
        env = {**os.environ, "RFE_SKIP_BOOTSTRAP": "1"}
        result = subprocess.run(
            ["bash", str(copied), "--type", "rfe"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith("ERROR: could not read the type registry")
        assert "holds no <name>/type.yaml" in result.stderr

    def test_unknown_argument_rejected(self, fake_checkout):
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--initiative")
        assert rc == 2
        assert "unknown argument" in stderr

    def test_skip_bootstrap_still_short_circuits(self, fake_checkout):
        """RFE_SKIP_BOOTSTRAP wins over validation — offline runs stay possible."""
        env = {**os.environ, "RFE_SKIP_BOOTSTRAP": "1"}
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "initiative"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0


class TestPathsMatchPipelineRegistry:
    """The shell script restates paths PIPELINE_TYPES already owns.

    Duplication is fine, silent divergence is not: if the rubric moves in the
    registry but not here, bootstrap validates a path nothing reads.
    """

    def _script_text(self):
        with open(SCRIPT) as f:
            return f.read()

    def test_rfe_rubric_matches_registry(self):
        assert PIPELINE_TYPES["rfe"]["rubric_path"].endswith(RFE_RUBRIC)
        assert RFE_RUBRIC in self._script_text()

    def test_initiative_rubric_matches_registry(self):
        assert PIPELINE_TYPES["initiative"]["rubric_path"].endswith(INITIATIVE_RUBRIC)
        assert INITIATIVE_RUBRIC in self._script_text()

    def test_scorer_agent_filename_matches_registry(self):
        expected = PIPELINE_TYPES["initiative"]["scorer_type"] + ".md"
        assert f'INITIATIVE_AGENT="{expected}"' in self._script_text()


class TestCallersDeclareType:
    """An initiative skill that forgets the flag silently loses the gate."""

    def _bootstrap_lines(self, path):
        with open(path) as f:
            return [ln for ln in f if re.search(r"bootstrap-assess-rfe\.sh", ln)]

    def _skill_files(self):
        for dirpath, _, filenames in os.walk(SKILLS_DIR):
            for name in filenames:
                if name.endswith(".md"):
                    yield os.path.join(dirpath, name)

    def test_initiative_skills_pass_type_initiative(self):
        missing = []
        for path in self._skill_files():
            if "initiative-" not in path:
                continue
            for line in self._bootstrap_lines(path):
                if "--type initiative" not in line:
                    missing.append(f"{os.path.relpath(path, REPO_ROOT)}: {line.strip()}")
        detail = "\n".join(missing)
        assert not missing, f"initiative skills invoking bootstrap without --type:\n{detail}"

    def test_at_least_one_initiative_caller_exists(self):
        """Guards the filter above from passing vacuously."""
        found = [
            path
            for path in self._skill_files()
            if "initiative-" in path and self._bootstrap_lines(path)
        ]
        assert len(found) >= 3

    def test_pipeline_setup_phase_is_type_aware(self):
        """The pipeline is where a missed gate becomes an unbounded wait-for-wave spin."""
        from pipeline_state import _build_phase_config

        for ptype in PIPELINE_TYPES:
            setup = _build_phase_config(ptype)["SETUP"]
            # SETUP runs its bootstrap steps as a concurrent "commands" list.
            commands = setup.get("commands") or [setup["command"]]
            assert any(f"bootstrap-assess-rfe.sh --type {ptype}" in c for c in commands)
