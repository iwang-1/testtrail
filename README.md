# TestTrail

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/) [![dependencies: standard library only](https://img.shields.io/badge/dependencies-stdlib_only-brightgreen)](#)

Which Python tests could reach the code in this commit? TestTrail reads two committed Git trees and prints a shortest import path from each selected test to a changed file. Each path names the revision that supplied the evidence. You can run it against a separate checkout without installing or importing the target project.

## At a glance

- **Explains test selection.** For each selected test it prints the shortest import path to a changed file, e.g. `tests/test_prices.py -> src/shop/prices.py -> src/shop/rates.py`.
- **Checked against real failures:** on injected faults in a real project, it selected **all 5** failing test pairs out of 6 and skipped the 1 that passed.
- **Fails safe.** Dynamic imports, conftest changes, non-Python changes and parse errors fall back to running everything.
- Reads both Git revisions directly (AST parsing + multi-source BFS); never imports or runs the target project.

## Try it

Requires Git and Python 3.11 or newer. From the TestTrail checkout:

```sh
python3 testtrail.py --repo /path/to/python-project HEAD~1 HEAD
python3 testtrail.py --repo /path/to/python-project main feature --json
python3 -m unittest discover -v
```

Both arguments identify committed snapshots. To compare a feature branch against its fork point, pass the result of `git merge-base main feature` as the base. Local uncommitted files are outside the comparison.

For the relative-import fixture in the test suite, a change in the tax module yields this chain:

```text
tests/test_prices.py -> src/shop/prices.py -> src/shop/rates.py
```

The unrelated test importing only the standard library is omitted. If a module disappears in the new revision, the test that imported it can still be found using the old graph. Removed tests are excluded from the plan.

## How it works

1. Resolve both revisions to commit IDs and compare their tracked path, file-mode and blob-ID entries.
2. Read Python blobs in bulk with Git's batch object reader. Parse imports with the Python AST, including relative imports, package initializers and conventional root or src layouts.
3. Walk reverse import edges from all changed paths using multi-source breadth-first search. Sorted roots and neighbors give stable output even with cycles or equally short paths.
4. Merge both selections, preferring head evidence for a test when available. JSON includes full commit IDs, changed paths, selection mode, fallback reasons and test chains.

## Interpretation

Static selection gives an import-based candidate list. Test discovery recognizes Python files named `test_*.py` or `*_test.py`. Changed non-Python files (including documentation), changed pytest conftest files, parser failures, recognized unresolved dynamic import calls, symlinks and submodules trigger selection of all recognized head tests. With identical snapshots, the plan is empty.

Custom pytest discovery, plugin registration, indirect loader rebinding, working-directory imports and changes to data read through Python APIs can introduce dependencies outside the graph. Use the output to prioritize an initial CI lane and keep the full suite as the merge gate. TestTrail prints a plan and never invokes the target project's test runner.

### Dynamic imports

For a single literal absolute name, a call such as `load("shop.tax")` contributes a dependency edge when `load` was imported as an alias of `importlib.import_module`. An unresolved argument, relative name or multi-argument call to a recognized loader triggers full selection. The regression builds Git commits for both paths and verifies that a test importing the consumer reaches the dynamically loaded module. Runtime plugin discovery and loader rebinding remain outside the analysis.

### Check predicted tests against observed failures

`python3 experiments/mutations.py /path/to/batchwindow` extracts a pinned revision into disposable checkouts, establishes a green baseline, introduces three independent faults, and runs every test module for each fault. TestTrail selected all five failing module/case pairs out of six possible pairs and skipped one passing module. These are hand-selected mutations on a small public project. [Inputs and observed failures](experiments/mutation-results.json)
