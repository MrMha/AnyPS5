import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stubgen


class StubGeneratorTests(unittest.TestCase):
    def test_default_stdout_contains_only_cpp(self):
        with tempfile.TemporaryDirectory() as directory:
            nids = Path(directory) / "nids.txt"
            nids.write_text("AAAAAAAAAAA\n", encoding="utf-8")
            output, debt = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(debt):
                self.assertEqual(stubgen.main(["--module", "libSceExample", "--nids", str(nids)]), 0)
            self.assertTrue(output.getvalue().endswith("\n}\n"))
            self.assertNotIn("unknown name, signature", output.getvalue())
            self.assertIn("unknown name, signature", debt.getvalue())

    def test_output_cannot_overwrite_an_input_file(self):
        with tempfile.TemporaryDirectory() as directory:
            nids = Path(directory) / "nids.txt"
            original = "AAAAAAAAAAA\n"
            nids.write_text(original, encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                stubgen.main(["--module", "libSceExample", "--nids", str(nids), "--out", str(nids)])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(nids.read_text(encoding="utf-8"), original)

    def test_debt_write_failure_preserves_existing_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nids, output = root / "nids.txt", root / "Export.cpp"
            nids.write_text("AAAAAAAAAAA\n", encoding="utf-8")
            output.write_text("existing source\n", encoding="utf-8")
            arguments = ["--module", "libSceExample", "--nids", str(nids), "--out", str(output), "--td-out", str(root / "absent" / "debt.txt")]
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                stubgen.main(arguments)
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing source\n")
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["Export.cpp", "nids.txt"])

    def test_malformed_nid_is_rejected_before_generating_cpp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nids.txt"
            path.write_text('AAAAAAAAAAA\ninvalid"nid\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid NID"):
                stubgen.parse_nids(path)

    def test_code_and_debt_outputs_cannot_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nids = root / "nids.txt"
            nids.write_text("AAAAAAAAAAA\n", encoding="utf-8")
            output = root / "Export.cpp"
            arguments = ["--module", "libSceExample", "--nids", str(nids), "--out", str(output), "--td-out", str(output)]
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                stubgen.main(arguments)
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(output.exists())

    def test_valid_annotations_comments_and_duplicates_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nids.txt"
            path.write_text("# imports\nAAAAAAAAAAA#A#B\nAAAAAAAAAAA\nBBBBBBBBBBB\n", encoding="utf-8-sig")
            self.assertEqual(stubgen.parse_nids(path), ["AAAAAAAAAAA", "BBBBBBBBBBB"])

    def test_valid_generation_keeps_the_code_and_debt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nids = root / "nids.txt"
            nids.write_text("AAAAAAAAAAA\n", encoding="utf-8")
            code, debt = root / "Export.cpp", root / "debt.txt"
            arguments = ["--module", "libSceExample", "--nids", str(nids), "--out", str(code), "--td-out", str(debt)]
            self.assertEqual(stubgen.main(arguments), 0)
            self.assertIn('APS5_EXPORT("AAAAAAAAAAA",', code.read_text(encoding="utf-8"))
            self.assertIn("unknown name, signature", debt.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
