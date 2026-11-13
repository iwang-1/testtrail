import subprocess
import tempfile
import unittest
from pathlib import Path

from testtrail import build, plan, trails


class GraphTests(unittest.TestCase):
    def test_relative_imports_and_cycles(self):
        graph = build({
            "src/shop/__init__.py": b"",
            "src/shop/rates.py": b"from . import prices",
            "src/shop/prices.py": b"from .rates import tax",
            "tests/test_prices.py": b"from shop.prices import quote",
            "tests/test_other.py": b"import datetime",
        })
        self.assertEqual(trails(graph, ["src/shop/rates.py"]), {
            "tests/test_prices.py": ["tests/test_prices.py", "src/shop/prices.py", "src/shop/rates.py"]})
        self.assertIn("tests/test_prices.py", trails(graph, ["src/shop/__init__.py"]))


class RevisionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.write("lib.py", "value = 1")
        self.write("tests/test_lib.py", "import lib")
        self.write("tests/test_other.py", "import math")
        self.commit()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_text(content)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def compare(self):
        return plan(self.root, "HEAD~1", "HEAD")

    def test_ignores_uncommitted_edits(self):
        self.write("lib.py", "value = 2")
        self.commit()
        self.write("tests/test_other.py", "import lib")
        self.assertEqual([r["test"] for r in self.compare()["tests"]], ["tests/test_lib.py"])

    def test_deleted_module_uses_old_graph(self):
        (self.root / "lib.py").unlink()
        self.commit()
        record, = self.compare()["tests"]
        self.assertEqual(record["revision"], "base")
        self.assertEqual(record["chain"], ["tests/test_lib.py", "lib.py"])

    def test_removed_test_excluded(self):
        (self.root / "tests/test_lib.py").unlink()
        self.commit()
        self.assertEqual(self.compare()["tests"], [])

    def test_dynamic_import_falls_back(self):
        self.write("lib.py", "x = __import__(name)")
        self.commit()
        self.assertEqual(self.compare()["mode"], "full")
        self.assertEqual(len(self.compare()["tests"]), 2)

    def test_config_falls_back(self):
        self.write("pytest.ini", "[pytest]")
        self.commit()
        self.assertEqual(self.compare()["reasons"], ["Suite-wide input changed: pytest.ini"])

    def test_identical_commits(self):
        self.assertEqual(plan(self.root, "HEAD", "HEAD")["tests"], [])

    def test_literal_aliased_loader_selects_real_dependency(self):
        self.write("consumer.py", "from importlib import import_module as load\nload('lib')")
        self.write("tests/test_consumer.py", "import consumer")
        self.commit()
        self.write("lib.py", "value=9")
        self.commit()
        report = self.compare()
        self.assertEqual(report["mode"], "static")
        self.assertEqual(next(r["chain"] for r in report["tests"] if "consumer" in r["test"]),
                         ["tests/test_consumer.py", "consumer.py", "lib.py"])
        self.write("consumer.py", "from importlib import import_module as load\nload(module_name)")
        self.commit()
        self.assertEqual(self.compare()["mode"], "full")
