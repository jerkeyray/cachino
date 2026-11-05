"""Evaluate the frozen split_after task without installing the target package.

The default prints the proposed evaluation and executes no repository code.
--run imports an explicitly supplied task export in a bounded child process.
This evaluator is not a sandbox; agent-produced code must be evaluated inside
the task isolation boundary. Frozen original tests stay outside the agent mount.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest


MANIFEST = Path(__file__).with_name("manifest.json")
# Each case specifies observable behavior rather than the reference patch.
# Test both reusable containers and one-shot iterators below.
CASES = (
    ("singleton_final_limit_1", [1], 1, 1, [[1]]),
    ("prefix_final_limit_1", [0, 1], 1, 1, [[0, 1]]),
    ("final_limit_2", [1, 1], 1, 2, [[1], [1]]),
    ("nonempty_tail", [1, 2, 3], 1, 1, [[1], [2, 3]]),
    ("tail_not_resplit", [1, 1, 1], 1, 1, [[1], [1, 1]]),
    ("empty_unlimited", [], 1, -1, []),
    ("empty_zero_limit", [], 1, 0, [[]]),
    ("zero_limit", [1, 2], 1, 0, [[1, 2]]),
    ("no_match", [2, 3], 1, 1, [[2, 3]]),
    ("unlimited_final", [1, 1], 1, -1, [[1], [1]]),
)
EXPECTED_BASELINE_FAILURES = {
    mode + ":" + name
    for mode in ("list", "iterator")
    for name in ("singleton_final_limit_1", "prefix_final_limit_1", "final_limit_2")
}


def blob_sha(path):
    """Compute Git's blob identity without requiring a Git checkout."""
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def verify_inputs(repo, frozen_tests, manifest, expectation):
    """Check the exported revision's fingerprints and immutable test source.

    Candidate exports may change only the declared editable tracked paths;
    checking for additional/deleted files belongs to the workspace diff gate.
    This check verifies the listed immutable files, not an entire Git tree.
    """
    repo, frozen_tests = repo.resolve(), frozen_tests.resolve()
    if not repo.is_dir() or repo == frozen_tests or repo in frozen_tests.parents:
        raise ValueError("provide a task export and frozen tests outside that export")
    task = manifest["task"]
    expected = {item["path"]: item["git_blob_sha"] for item in task["baseline_files"]}
    original_tests_sha = expected["tests/test_more.py"]
    if blob_sha(frozen_tests) != original_tests_sha:
        raise ValueError("frozen original tests do not match the pinned baseline")
    if expectation == "reference":
        expected.update({item["path"]: item["git_blob_sha"] for item in task["reference_files"]})
    for name, digest in expected.items():
        if expectation == "candidate" and name in task["editable_paths"]:
            continue
        path = repo / name
        if not path.is_file() or path.is_symlink() or repo not in path.resolve().parents or blob_sha(path) != digest:
            raise ValueError("snapshot fingerprint mismatch: " + name)


def worker(repo, frozen_tests):
    """Run independent behavioral cases plus the sealed original test class."""
    sys.path.insert(0, str(repo.resolve()))
    import more_itertools as mi

    if Path(mi.__file__).resolve() != repo.resolve() / "more_itertools" / "__init__.py":
        raise ValueError("target import did not resolve to the supplied task export")
    results = []
    for mode in ("list", "iterator"):
        for name, values, marker, limit, expected in CASES:
            try:
                source = list(values) if mode == "list" else iter(values)
                actual = list(mi.split_after(source, lambda value: value == marker, maxsplit=limit))
                results.append({"case": mode + ":" + name, "passed": actual == expected,
                                "expected": expected, "actual": actual})
            except Exception as error:
                results.append({"case": mode + ":" + name, "passed": False,
                                "error": type(error).__name__ + ": " + str(error)})
    calls = []
    try:
        def predicate(value):
            calls.append(value)
            return value == 1
        actual = list(mi.split_after(iter([1, 2, 3]), predicate, maxsplit=1))
        results.append({"case": "predicate_stops_at_limit", "passed": actual == [[1], [2, 3]] and calls == [1],
                        "actual": actual, "predicate_calls": calls})
    except Exception as error:
        results.append({"case": "predicate_stops_at_limit", "passed": False, "error": repr(error)})
    # Compile the verified external copy, rather than the agent-editable tests.
    module = types.ModuleType("suez_frozen_upstream_tests")
    module.__file__ = str(frozen_tests)
    sys.modules[module.__name__] = module
    exec(compile(frozen_tests.read_bytes(), str(frozen_tests), "exec"), module.__dict__)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.SplitAfterTest)
    log = io.StringIO()
    upstream = unittest.TextTestRunner(stream=log, verbosity=1).run(suite)
    return {"cases": results, "upstream_tests_run": upstream.testsRun,
            "upstream_passed": upstream.wasSuccessful() and upstream.testsRun == 4,
            "upstream_skipped": len(upstream.skipped), "upstream_log": log.getvalue()}


def evaluate(repo, frozen_tests, manifest, expectation, timeout):
    """Run checked task code with a clean child import path and a wall timeout.

    No dependency installation, network setup, model call or Git mutation occurs.
    The clean environment reduces accidental credential inheritance but is not
    OS-level isolation. Containers/resource limits remain a live-task gate.
    """
    verify_inputs(repo, frozen_tests, manifest, expectation)
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker",
               "--repo", str(repo.resolve()), "--frozen-tests", str(frozen_tests.resolve())]
    try:
        completed = subprocess.run(command, env={"PATH": os.defpath, "LC_ALL": "C"},
                                   capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"evaluation_matched_expectation": False, "status": "timeout", "timeout_seconds": timeout}
    if completed.returncode != 0:
        return {"evaluation_matched_expectation": False, "status": "worker_failed",
                "returncode": completed.returncode, "stderr": completed.stderr[-12000:]}
    result = json.loads(completed.stdout)
    failed = {case["case"] for case in result["cases"] if not case["passed"]}
    matched = failed == (EXPECTED_BASELINE_FAILURES if expectation == "baseline" else set())
    result.update(status="evaluated", expectation=expectation, failed_cases=sorted(failed),
                  regressions_passed=not failed,
                  evaluation_matched_expectation=matched and result["upstream_passed"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--frozen-tests", type=Path)
    parser.add_argument("--expect", choices=("baseline", "reference", "candidate"), default="candidate")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--run", action="store_true", help="execute the supplied target export after fingerprint checks")
    parser.add_argument("--out", type=Path, help="new private JSON result file, required with --run")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    manifest_bytes = MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    if args.worker:
        if args.repo is None or args.frozen_tests is None:
            parser.error("worker requires --repo and --frozen-tests")
        verify_inputs(args.repo, args.frozen_tests, manifest, "candidate")
        print(json.dumps(worker(args.repo, args.frozen_tests)))
        return
    if not args.run:
        print(json.dumps({"status": "dry_run", "task_id": manifest["task_id"],
                          "baseline_revision": manifest["task"]["baseline_revision"],
                          "reference_revision": manifest["task"]["reference_revision"],
                          "behavioral_cases": 2 * len(CASES) + 1,
                          "existing_tests": "four sealed original SplitAfterTest methods",
                          "expected_baseline_failures": sorted(EXPECTED_BASELINE_FAILURES),
                          "repository_code_executed": False}, indent=2))
        return
    if args.repo is None or args.frozen_tests is None or args.out is None or args.timeout <= 0:
        parser.error("--run requires --repo, --frozen-tests, --out and a positive timeout")
    if args.out.exists():
        parser.error("output file already exists")
    try:
        result = evaluate(args.repo, args.frozen_tests, manifest, args.expect, args.timeout)
        result.update(task_id=manifest["task_id"], python=sys.version,
                      evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                      frozen_tests_git_blob_sha=blob_sha(args.frozen_tests))
        args.out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
            json.dump(result, file, indent=2)
            file.write("\n")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, "Evaluation failed: %s\n" % error)
    print(json.dumps({"status": result["status"], "evaluation_matched_expectation": result["evaluation_matched_expectation"],
                      "out": str(args.out)}))
    if not result["evaluation_matched_expectation"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
