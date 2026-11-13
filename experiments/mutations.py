"""Check test selection against failures from hand-selected BatchWindow mutations."""
import argparse
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from testtrail import plan

REVISION = "ee8b56e"
CASES = [
    ("planner.py", "return setup + len(batch)", "return setup + 1 + len(batch)"),
    ("planner.py", "now = max(now, future[0].arrival)", "now = future[0].arrival"),
    ("oracle.py", "return min(states[full])", "return max(states[full])"),
]


def main(source):
    rev = subprocess.check_output(["git", "-C", source, "rev-parse", REVISION]).decode().strip()
    archive = subprocess.check_output(["git", "-C", source, "archive", rev])
    results = []
    for file, old, new in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(root, filter="data")
            def git(*args):
                subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                *args], cwd=root, check=True, capture_output=True)
            git("init", "-q")
            git("add", ".")
            git("commit", "-qm", "base fixture")
            test_files = sorted(root.glob("tests/test_*.py"))
            def failures():
                return [p.relative_to(root).as_posix() for p in test_files
                        if subprocess.run([sys.executable, "-m", "unittest", p.relative_to(root).as_posix()],
                                          cwd=root, capture_output=True).returncode]
            if failures():
                raise RuntimeError("baseline suite failed")
            path = root/file
            text = path.read_text()
            if text.count(old) != 1:
                raise RuntimeError("fixture drift")
            path.write_text(text.replace(old,new))
            git("commit", "-qam", "mutated fixture")
            selected = [r["test"] for r in plan(root,"HEAD~1","HEAD")["tests"]]
            failed = failures()
            if not failed or set(failed) - set(selected):
                raise RuntimeError(f"undetected or unselected failure: {file} {failed} {selected}")
            results.append(dict(file=file, substitution=[old,new], selected=selected, failed=failed))
    return dict(repository="iwang-1/batchwindow", revision=rev, cases=results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batchwindow_checkout")
    print(json.dumps(main(parser.parse_args().batchwindow_checkout), indent=2))
