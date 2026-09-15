#!/usr/bin/env python3
"""Post-barrier verification for agent phases.

Checks that expected output files exist for each ID after a phase
completes. Missing outputs are treated as agent failures — error
frontmatter is written and the ID is removed from the active set.

The phase→path maps below must stay in step with
check_review_progress.PHASE_CHECKS.

Usage:
    python3 scripts/verify_phase.py --phase assess --ids-file tmp/pipeline-active-ids.txt
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry
from artifact_utils import read_frontmatter, write_frontmatter

_TYPES = type_registry.load()

# Type-neutral assess staging dir (design §10: kept byte-stable for every type).
ASSESS_STAGING = "tmp/rfe-assess/single"


def _phase_output(desc):
    """phase -> (id -> expected output path) for one type: ``dirs`` x the pipeline phases,
    plus one ``<reviews>/<id>-<name>.md`` row per ``pipeline.dimensions`` entry."""
    dirs = desc.dirs()
    table = {
        "fetch": lambda id: f"{dirs['tasks']}/{id}.md",
        "assess": lambda id: f"{ASSESS_STAGING}/{id}.result.md",
    }
    for dimension in desc.get("pipeline.dimensions", []):
        name = dimension["name"]
        table[name] = lambda id, name=name: f"{dirs['reviews']}/{id}-{name}.md"
    table["review"] = lambda id: f"{dirs['reviews']}/{id}-review.md"
    table["split"] = lambda id: f"{dirs['reviews']}/{id}-split-status.yaml"
    return table


_PHASE_OUTPUT = {name: _phase_output(_TYPES.get(name)) for name in _TYPES.names()}
# Per-type aliases kept for their existing importers (tests/test_verify_phase.py).
_RFE_PHASE_OUTPUT = _PHASE_OUTPUT["rfe"]
_INITIATIVE_PHASE_OUTPUT = _PHASE_OUTPUT["initiative"]

_TYPE_CONFIG = {
    name: {
        "phases": _PHASE_OUTPUT[name],
        "reviews_dir": _TYPES.get(name).dirs()["reviews"],
        "id_field": _TYPES.get(name).id_field,
        "review_schema": f"{name}-review",
        # The varying tail of the error-stub command below (the fixed fields are pipeline
        # vocabulary, not type facts).
        "score_fields": [f"scores.{field}=0" for field in _TYPES.get(name).score_fields],
    }
    for name in _TYPES.names()
}


def write_error_stubs(phase, ids, pipeline_type="rfe", outcome="failed", error=None, failures=None):
    """Write the error-stub review for each id whose ``phase`` agent produced no output.

    One ``python3 scripts/frontmatter.py set`` per id, field for field the stub
    ``validate_types.build_error_stub`` mirrors (gate 1 checks the shape against the
    type's review schema): ``<id_field>=<id>``, ``error=<phase>_<outcome>``, the fixed
    stub vocabulary, then one ``scores.<f>=0`` per registry score field. ``outcome`` is
    ``failed`` for the post-barrier verifier (the agent finished without its output)
    and ``stalled`` for pipeline_state's wave stall guard (the agent never finished);
    both classify as retryable in error_collect.py. ``error`` replaces the derived
    ``<phase>_<outcome>`` value outright: the stall guard uses it to keep the
    non-retryable ``split_not_attempted:`` class when a split parent's review has to
    be replaced by the stub.

    ``frontmatter.py set`` merges the stub into an existing review and validates the
    merged record, so it also repairs a review the schema rejects in a field the stub
    sets (``feasibility: likely``). It cannot repair one rejected in a field the stub
    leaves alone — a half-written review with an unknown ``scores`` member — and a stub
    that silently goes unwritten leaves the id dropped from its ids file with no error
    marker: error_collect never retries it and the run report shows it failed for no
    reason. So when the merge fails, the review's frontmatter is replaced outright with
    the stub (``artifact_utils.write_frontmatter``, which keeps the body it can read) and
    one stderr line names the id and frontmatter.py's reason. One bad id still cannot
    take the caller down: the ids for which even the replacement failed are reported on
    stderr and returned (an empty list when every stub is on disk). ``failures``, when a
    dict is given, additionally receives ``id -> reason`` for those ids (the same reason
    the stderr line carries), for a caller that has to report them itself.
    """
    tc = _TYPE_CONFIG[pipeline_type]
    error_msg = error or f"{phase}_{outcome}"
    unwritten = []
    for rfe_id in ids:
        review_path = f"{tc['reviews_dir']}/{rfe_id}-review.md"
        cmd = [
            "python3",
            "scripts/frontmatter.py",
            "set",
            review_path,
            f"{tc['id_field']}={rfe_id}",
            f"error={error_msg}",
            "score=0",
            "pass=false",
            "recommendation=revise",
            "feasibility=feasible",
            "auto_revised=false",
            "needs_attention=true",
            f"needs_attention_reason=Agent failed: {error_msg}",
        ] + tc["score_fields"]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            continue
        except subprocess.CalledProcessError as exc:
            output = f"{exc.stderr or ''} {exc.stdout or ''}"
            reason = " ".join(output.split()) or f"exit {exc.returncode}"
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
        try:
            record = _stub_record(rfe_id, error_msg, pipeline_type)
            write_frontmatter(review_path, record, tc["review_schema"])
        except Exception as exc:
            unwritten.append(rfe_id)
            detail = " ".join(str(exc).split())
            why = (
                f"frontmatter.py set failed ({reason}) and replacing the review frontmatter"
                f" failed too ({type(exc).__name__}: {detail})"
            )
            if failures is not None:
                failures[rfe_id] = why
            print(
                f"verify_phase: {rfe_id}: no {error_msg} stub written: {why}",
                file=sys.stderr,
            )
            continue
        print(
            f"verify_phase: {rfe_id}: frontmatter.py set failed ({reason}); replaced the review"
            f" frontmatter with the {error_msg} stub",
            file=sys.stderr,
        )
    return unwritten


def _stub_record(rfe_id, error_msg, pipeline_type):
    """The stub as a record for the replace path: the same fields, in the same order, as the
    ``frontmatter.py set`` argv above (tests/test_verify_phase.py pins the two paths equal)."""
    tc = _TYPE_CONFIG[pipeline_type]
    return {
        tc["id_field"]: rfe_id,
        "error": error_msg,
        "score": 0,
        "pass": False,
        "recommendation": "revise",
        "feasibility": "feasible",
        "auto_revised": False,
        "needs_attention": True,
        "needs_attention_reason": f"Agent failed: {error_msg}",
        "scores": {field: 0 for field in _TYPES.get(pipeline_type).score_fields},
    }


def verify(phase, ids_file, pipeline_type="rfe"):
    tc = _TYPE_CONFIG[pipeline_type]
    phase_output = tc["phases"]

    ids = []
    if os.path.exists(ids_file):
        with open(ids_file) as f:
            ids = [line.strip() for line in f if line.strip()]

    if not ids:
        print("FAILED=")
        return

    output_fn = phase_output.get(phase)
    if not output_fn:
        print(f"Unknown phase: {phase}", file=sys.stderr)
        sys.exit(1)

    failed = []
    for rfe_id in ids:
        path = output_fn(rfe_id)
        exists = os.path.exists(path)

        # For review phase, also check that score is set
        if exists and phase == "review":
            try:
                data, _ = read_frontmatter(path)
                if data.get("score") is None:
                    exists = False
            except Exception:
                exists = False

        if not exists:
            failed.append(rfe_id)

    if failed:
        write_error_stubs(phase, failed, pipeline_type)

        failed_set = set(failed)
        remaining = [id_ for id_ in ids if id_ not in failed_set]
        with open(ids_file, "w") as f:
            for id_ in remaining:
                f.write(f"{id_}\n")

    print(f"FAILED={','.join(failed)}")


def main():
    parser = argparse.ArgumentParser(description="Post-barrier verification for agent phases")
    parser.add_argument("--type", choices=_TYPES.choices(), default="rfe")
    parser.add_argument("--phase", required=True, help="Phase to verify")
    parser.add_argument("--ids-file", required=True, help="File containing IDs to check")
    args = parser.parse_args()
    verify(args.phase, args.ids_file, args.type)


if __name__ == "__main__":
    main()
