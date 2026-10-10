import os
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_conventions import Check, STUB_BODY, function_body, links, output


class FunctionBodyTests(unittest.TestCase):
    def test_line_comments_preserve_following_statements(self):
        sources = (
            "int APS5_VABI sceExample() {\n// pending implementation\nreturn 0;\n}",
            "int APS5_VABI sceExample() // pending implementation\n{\nreturn 0;\n}",
            "int APS5_VABI sceExample() {\n(void)arg; // unused argument\nreturn 0;\n}",
            "int APS5_VABI sceExample() {\n// ignored } ; { tokens\nreturn 0;\n}",
        )
        for source in sources:
            with self.subTest(source=source):
                body = function_body(source.splitlines(), 0)
                self.assertIsNotNone(body)
                self.assertIsNotNone(STUB_BODY.fullmatch(body))

    def test_line_comment_does_not_turn_an_implementation_into_a_stub(self):
        source = "int APS5_VABI sceExample() {\n// validate the request\nvalidate();\nreturn 0;\n}"
        body = function_body(source.splitlines(), 0)
        self.assertEqual(body, "validate();return0;")
        self.assertIsNone(STUB_BODY.fullmatch(body))

    def test_block_comments_preserve_following_statements(self):
        source = "int APS5_VABI sceExample() {\n/* pending\nimplementation */\nreturn 0;\n}"
        self.assertEqual(function_body(source.splitlines(), 0), "return0;")

    def test_comment_markers_and_braces_in_strings_are_ignored(self):
        source = 'int APS5_VABI sceExample() {\nuse("// }", "/* { */");\nreturn 0;\n}'
        self.assertEqual(function_body(source.splitlines(), 0), 'use("","");return0;')

    def test_declaration_has_no_body(self):
        source = "int APS5_VABI sceExample();\nint APS5_VABI sceOther() { return 0; }"
        self.assertIsNone(function_body(source.splitlines(), 0))

    def test_unterminated_function_has_no_body(self):
        source = "int APS5_VABI sceExample() {\n// pending implementation\nreturn 0;"
        self.assertIsNone(function_body(source.splitlines(), 0))


class SilentStubTests(unittest.TestCase):
    def test_commented_stub_is_reported_when_debt_change_allows_comments(self):
        checker = Path(__file__).resolve().parents[1] / "check_conventions.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.run(
                    ["git", "-c", "user.name=Conventions Test", "-c", "user.email=test@example.com",
                     "-c", "commit.gpgsign=false", *args],
                    cwd=root, capture_output=True, text=True, check=True,
                )

            git("init", "-q")
            debt = root / "docs/dev/TechnicalDebt.md"
            debt.parent.mkdir(parents=True)
            debt.write_text("# Project technical debt\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "docs: record technical debt")
            source = root / "core/libs/prx/libSceExample/Export.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("int APS5_VABI sceExample() {\n// pending implementation\nreturn 0;\n}\n", encoding="utf-8")
            debt.write_text("# Project technical debt\n\nAn unrelated build limitation.\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "feat: add example export")
            result = subprocess.run(
                [sys.executable, str(checker), "--base", "HEAD~1"],
                cwd=root, capture_output=True, text=True,
                env={key: value for key, value in os.environ.items()
                     if key not in ("GITHUB_EVENT_PATH", "GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS")},
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("[silent-stub]", result.stdout)
            self.assertNotIn("[comment]", result.stdout)


class RepositoryChecksTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.original = Path.cwd()
        os.chdir(self.root)
        self.git("init", "-q")
        (self.root / "README.md").write_text("Project\n", encoding="utf-8")
        self.commit()

    def tearDown(self):
        os.chdir(self.original)
        self.directory.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=Conventions Test", "-c",
                               "user.email=test@example.com", "-c", "commit.gpgsign=false", *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "test: add convention fixture")

    def test_quoted_utf8_paths_are_checked(self):
        name = "docs/dev/測試guide.md" if os.name == "nt" else "docs/dev/測試\tguide.md"
        path = self.root / name
        path.parent.mkdir(parents=True)
        path.write_text("[missing](absent.md)\n", encoding="utf-8")
        self.commit()
        check = Check("HEAD~1", "HEAD")
        check.repository()
        check.docs()
        self.assertEqual(check.findings, [("doc-link", name, 1, "absent.md")])

    def test_mixed_patch_types_preserve_file_order(self):
        name = "a b.cpp" if os.name == "nt" else "a\nb.cpp"
        (self.root / "README.md").unlink()
        (self.root / "a.bin").write_bytes(b"binary\x00data")
        (self.root / name).write_text("// debt\n", encoding="utf-8")
        self.commit()
        check = Check("HEAD~1", "HEAD")
        check.code()
        check.repository()
        self.assertIn(("comment", name, 1, ""), check.findings)
        self.assertIn(("binary", "a.bin", 0, ""), check.findings)
        self.assertNotIn("README.md", check.added)

    def test_increment_line_is_not_a_patch_file_header(self):
        path = self.root / "core/example.cpp"
        path.parent.mkdir()
        path.write_text("void example() {\n++ counter;\n// debt\n}\n", encoding="utf-8")
        self.commit()
        check = Check("HEAD~1", "HEAD")
        check.code()
        self.assertIn(("comment", "core/example.cpp", 3, ""), check.findings)

    def test_submodule_outside_thirdparty_reports_without_crashing(self):
        head = self.git("rev-parse", "HEAD")
        self.git("update-index", "--add", "--cacheinfo", "160000", head, "vendor")
        self.git("commit", "-qm", "build: add invalid submodule")
        check = Check("HEAD~1", "HEAD")
        check.repository()
        self.assertIn(("system-dependency", "vendor", 0, "submodule outside 3rdparty/"), check.findings)

    def test_thirdparty_submodule_remains_allowed(self):
        head = self.git("rev-parse", "HEAD")
        self.git("update-index", "--add", "--cacheinfo", "160000", head, "3rdparty/vendor")
        self.git("commit", "-qm", "build: add allowed submodule")
        check = Check("HEAD~1", "HEAD")
        check.repository()
        self.assertEqual(check.findings, [])

    def test_relative_link_url_decoding(self):
        self.assertEqual(links("docs/dev/index.md", "[guide](./space%20name.md#details)"),
                         [("./space%20name.md", "docs/dev/space name.md")])
        self.assertEqual(links("docs/dev/index.md", "[reference](HTTPS://example.com/x)"), [])
        self.assertEqual(links("docs/dev/index.md", "[reference](//example.com/x)"), [])

    def test_percent_encoded_fragment_is_part_of_filename(self):
        self.assertEqual(links("docs/dev/index.md", "[guide](./part%23one.md#details)"),
                         [("./part%23one.md", "docs/dev/part#one.md")])

    def test_annotation_filename_properties_are_escaped(self):
        check = type("Findings", (), {"findings": [("doc-link", "docs/name,with:colon%\n.md", 2, "missing") ]})()
        original = os.environ.get("GITHUB_ACTIONS")
        os.environ["GITHUB_ACTIONS"] = "true"
        try:
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                output(check, "owner/repo")
        finally:
            if original is None:
                os.environ.pop("GITHUB_ACTIONS", None)
            else:
                os.environ["GITHUB_ACTIONS"] = original
        annotation = captured.getvalue().splitlines()[0]
        self.assertIn("file=docs/name%2Cwith%3Acolon%25%0A.md,line=2", annotation)


if __name__ == "__main__":
    unittest.main()
