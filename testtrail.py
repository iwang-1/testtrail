"""Explain Python test impact between two committed Git trees."""
import argparse
import ast
import json
import subprocess
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


def git(repo, *args, data=None):
    result = subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def tree(repo, ref):
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    entries = {}
    for record in git(repo, "ls-tree", "-rz", commit).split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            mode, _, oid = meta.split()
            entries[path.decode("utf-8")] = (mode.decode(), oid.decode())
    return commit, entries


def python_sources(repo, entries):
    files = sorted(p for p in entries if p.endswith(".py") and entries[p][0].startswith("100"))
    if not files:
        return {}
    request = "".join(entries[p][1] + "\n" for p in files).encode()
    output = git(repo, "cat-file", "--batch", data=request)
    offset, sources = 0, {}
    for path in files:
        end = output.index(b"\n", offset)
        size = int(output[offset:end].split()[-1])
        sources[path] = output[end + 1:end + 1 + size]
        offset = end + size + 2
    return sources


def module_name(path):
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[0] == "src":
        parts.pop(0)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def is_test(path):
    name = PurePosixPath(path).name
    return name.startswith("test_") or name.endswith("_test.py")


@dataclass
class Graph:
    dependents: dict
    tests: set
    warnings: list


def build(sources):
    modules = defaultdict(set)
    dependents = defaultdict(set)
    warnings = []
    for path in sources:
        modules[module_name(path)].add(path)
    for path, source in sorted(sources.items()):
        name = module_name(path)
        package = name.split(".")
        if PurePosixPath(path).name != "__init__.py":
            package.pop()
        imports = {".".join(package)}
        try:
            nodes = ast.walk(ast.parse(source, filename=path))
        except (SyntaxError, ValueError, UnicodeError):
            warnings.append(f"Cannot parse {path}")
            continue
        for node in nodes:
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    prefix = package[:len(package) - node.level + 1] if node.level <= len(package) else []
                    base = ".".join(filter(None, [*prefix, base]))
                imports.add(base)
                imports.update(".".join(filter(None, [base, alias.name])) for alias in node.names)
            elif isinstance(node, ast.Call):
                target = node.func
                if getattr(target, "id", "") in {"__import__", "import_module"} or getattr(target, "attr", "") == "import_module":
                    warnings.append(f"Dynamic import in {path}:{node.lineno}")
        for imported in imports:
            parts = imported.split(".")
            for length in range(1, len(parts) + 1):
                for dependency in modules.get(".".join(parts[:length]), ()):
                    if path != dependency:
                        dependents[dependency].add(path)
    return Graph(dependents, {p for p in sources if is_test(p)}, sorted(set(warnings)))


def trails(graph, changed):
    parents = {p: None for p in sorted(changed)}
    queue = deque(parents)
    while queue:
        path = queue.popleft()
        for caller in sorted(graph.dependents.get(path, ())):
            if caller not in parents:
                parents[caller] = path
                queue.append(caller)
    results = {}
    for test in sorted(graph.tests & parents.keys()):
        chain = [test]
        while parents[chain[-1]] is not None:
            chain.append(parents[chain[-1]])
        results[test] = chain
    return results


def plan(repo, base, head):
    old, before = tree(repo, base)
    new, after = tree(repo, head)
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    old_graph = build(python_sources(repo, before))
    new_graph = build(python_sources(repo, after))
    reasons = []
    if changed:
        reasons = sorted(set(old_graph.warnings + new_graph.warnings))
        for path in changed:
            if not path.endswith(".py") or PurePosixPath(path).name == "conftest.py":
                reasons.append("Suite-wide input changed: " + path)
        for entries in (before, after):
            if any(mode in {"120000", "160000"} for mode, _ in entries.values()):
                reasons.append("Repository contains a symlink or submodule")
                break
    selected = {}
    for revision, graph in (("head", new_graph), ("base", old_graph)):
        for test, chain in trails(graph, changed).items():
            if test in new_graph.tests and test not in selected:
                selected[test] = {"test": test, "revision": revision, "chain": chain}
    if reasons:
        for test in new_graph.tests:
            selected.setdefault(test, {"test": test, "revision": None, "chain": []})
    return {"base": old, "head": new, "changed": changed, "reasons": reasons,
            "mode": "full" if reasons else "static", "tests": [selected[t] for t in sorted(selected)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("head", nargs="?", default="HEAD")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(args.repo, args.base, args.head)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"testtrail: {exc}\n")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{result['mode']} selection | {len(result['changed'])} changed paths | {len(result['tests'])} test files")
        for reason in result["reasons"]:
            print("  " + reason)
        for test in result["tests"]:
            print("  " + (" -> ".join(test["chain"]) + f" [{test['revision']}]" if test["chain"] else test["test"]))


if __name__ == "__main__":
    main()
