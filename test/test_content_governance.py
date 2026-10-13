import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("governance", ROOT / "scripts/content_governance.py")
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)


def sections(text):
    """Read real H2 sections without treating code comments as headings."""
    source = text.splitlines(keepends=True)
    visible = g.outside_fences(text).splitlines()
    starts = [(i, line[3:].strip()) for i, line in enumerate(visible) if line.startswith("## ")]
    return {title: "".join(source[start:starts[n + 1][0] if n + 1 < len(starts) else len(source)])
            for n, (start, title) in enumerate(starts)}


class GovernanceChecks(unittest.TestCase):
    def test_relative_links_ignore_nested_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ok.md").write_text("# OK\n")
            (root / "test.md").write_text('''# Links
[valid](ok.md#old-anchor)
[missing](missing.md)
[reference]: missing-reference.md
`[code](not-a-link.md)`
    ```cpp
    auto f = [](void* p) {};
    // [sample](not-real.md)
    ```
<a href="ok.md">ok</a>
<img src="missing-image.png">
''')
            with patch.object(g, "ROOT", root):
                self.assertEqual(g.local_link_issues(), {
                    "test.md -> missing.md", "test.md -> missing-reference.md",
                    "test.md -> missing-image.png"})

    def test_duplicate_detection_survives_date_and_prefix_changes(self):
        text = "\n".join(f"数据路径 item_{i} 的生产消费关系与生命周期应逐项核对。" for i in range(200))
        a = g.fingerprints("# 2026-06-01\n" + text)
        b = g.fingerprints("# 2026-08-25 新版标题\n" + text)
        self.assertGreater(len(a & b) / min(len(a), len(b)), 0.95)

    def test_registry_rejects_missing_notes_and_duplicate_primary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes").mkdir()
            for name in ("a.md", "b.md", "c.md"):
                (root / "notes" / name).write_text("# " + name)
            topics = [{"id": "cpp/a", "category": "cpp", "canonical": "notes/a.md", "documents": [
                {"path": "notes/a.md", "role": "primary"},
                {"path": "notes/b.md", "role": "primary"}]}]
            with patch.object(g, "ROOT", root):
                errors = g.validate_catalog(topics)
            self.assertTrue(any("notes/c.md" in error for error in errors))
            self.assertTrue(any("恰有一篇主文" in error for error in errors))

    def test_stale_daily_plan_is_rejected(self):
        result = subprocess.run([
            "python3", str(ROOT / "scripts/content_governance.py"), "candidate", "--topic", "graphpool",
            "--plan", str(ROOT / "notes/weekly-topic-selection/daily-plan-20260529.json"),
            "--date", "2026-10-13"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("拒绝复用旧计划", result.stderr)

    def test_render_is_current_and_idempotent(self):
        topics = g.read_json("governance/catalog.json")["topics"]
        for path, content in g.generated_files(topics).items():
            with self.subTest(path=path):
                self.assertEqual((ROOT / path).read_text(), content)


class MigrationPreservation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = g.read_json("governance/changes.json")
        cls.revision = cls.ledger["baseline_revision"]
        cls.originals = {}
        for change in cls.ledger["changes"]:
            path = change["path"]
            cls.originals[path] = subprocess.check_output(
                ["git", "show", cls.revision + ":" + path], cwd=ROOT).decode()

    def test_original_hashes_and_permalinks(self):
        for change in self.ledger["changes"]:
            with self.subTest(path=change["path"]):
                self.assertEqual(hashlib.sha256(self.originals[change["path"]].encode()).hexdigest(),
                                 change["original_sha256"])
                self.assertTrue(change["original_url"].endswith(self.revision + "/" + change["path"]))

    def test_retired_appendices_preserve_primary_text(self):
        further_edited = {c["path"] for c in self.ledger["changes"] if c["kind"] != "retired-appendix"}
        for change in self.ledger["changes"]:
            path = change["path"]
            if change["kind"] != "retired-appendix" or path in further_edited:
                continue
            with self.subTest(path=path):
                old = self.originals[path]
                primary = re.split(r"^## (?:七|十三)、业务代码库适配分析\s*$", old, flags=re.M)[0].rstrip()
                primary = re.sub(r"\n---\s*$", "", primary).rstrip()
                current = g.NAV.sub("", (ROOT / path).read_text())
                self.assertEqual(current.split("\n\n> 内容整理：")[0].rstrip(), primary)

    def test_consolidation_keeps_other_sections_verbatim(self):
        for change in self.ledger["changes"]:
            if change["kind"] != "consolidated-sections":
                continue
            old = sections(self.originals[change["path"]])
            current = sections((ROOT / change["path"]).read_text())
            for heading, text in old.items():
                with self.subTest(path=change["path"], heading=heading):
                    if heading in change["sections"]:
                        self.assertNotIn(heading, current)
                    else:
                        self.assertEqual(text.strip(), current[heading].strip())

    def test_enhanced_figures_are_preserved(self):
        change = next(c for c in self.ledger["changes"] if c["kind"] == "shared-paragraphs")
        old = self.originals[change["path"]]
        current = (ROOT / change["path"]).read_text()
        figures = re.findall(r"```(?:plantuml|infographic|mermaid).*?```", old, re.S)
        # The HTML architecture section is also retained byte for byte.
        figures.append(sections(old)["0. 架构全景图"])
        self.assertTrue(figures)
        for figure in figures:
            self.assertIn(figure.strip(), current)


if __name__ == "__main__":
    unittest.main()
