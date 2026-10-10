import tempfile
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nid_names


class DatabaseCacheTests(unittest.TestCase):
    def test_interrupted_download_does_not_publish_a_partial_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache" / "aerolib.csv"

            def interrupted(url, filename):
                Path(filename).write_text("nid1 scePartial\n", encoding="utf-8")
                raise URLError("connection interrupted")

            with patch.object(nid_names.urllib.request, "urlretrieve", side_effect=interrupted):
                with self.assertRaises(URLError):
                    nid_names.load_db(cache)
            self.assertFalse(cache.exists())
            self.assertEqual(list(cache.parent.iterdir()), [])

    def test_retry_after_interruption_downloads_the_complete_database(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "aerolib.csv"

            def interrupted(url, filename):
                Path(filename).write_text("nid1 scePartial\n", encoding="utf-8")
                raise URLError("connection interrupted")

            def complete(url, filename):
                Path(filename).write_text("nid1 sceComplete\nnid2 sceOther\n", encoding="utf-8")

            with patch.object(nid_names.urllib.request, "urlretrieve", side_effect=interrupted):
                with self.assertRaises(URLError):
                    nid_names.load_db(cache)
            with patch.object(nid_names.urllib.request, "urlretrieve", side_effect=complete) as download:
                self.assertEqual(nid_names.load_db(cache), {"nid1": "sceComplete", "nid2": "sceOther"})
                download.assert_called_once()
            self.assertEqual(list(cache.parent.iterdir()), [cache])

    def test_cache_is_published_only_after_download_completes(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "aerolib.csv"

            def complete(url, filename):
                self.assertEqual(url, nid_names.DB_URL)
                self.assertNotEqual(Path(filename), cache)
                self.assertEqual(Path(filename).parent, cache.parent)
                self.assertFalse(cache.exists())
                Path(filename).write_text("nid1 sceComplete\n", encoding="utf-8")

            with patch.object(nid_names.urllib.request, "urlretrieve", side_effect=complete):
                self.assertEqual(nid_names.load_db(cache), {"nid1": "sceComplete"})
            self.assertEqual(cache.read_text(encoding="utf-8"), "nid1 sceComplete\n")
            self.assertEqual(list(cache.parent.iterdir()), [cache])

    def test_existing_cache_is_used_without_downloading(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "aerolib.csv"
            cache.write_text("nid1 sceExisting\n", encoding="utf-8")
            with patch.object(nid_names.urllib.request, "urlretrieve") as download:
                self.assertEqual(nid_names.load_db(cache), {"nid1": "sceExisting"})
                download.assert_not_called()

    def test_utf8_bom_does_not_change_the_first_nid(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "aerolib.csv"
            nid = nid_names.compute_nid("sceExample")
            cache.write_text(f"{nid} sceExample\n", encoding="utf-8-sig")
            self.assertEqual(nid_names.load_db(cache), {nid: "sceExample"})

    def test_comment_rows_do_not_become_database_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "aerolib.csv"
            nid = nid_names.compute_nid("sceExample")
            cache.write_text(f"# Database names\n{nid} sceExample\n", encoding="utf-8")
            self.assertEqual(nid_names.load_db(cache), {nid: "sceExample"})

    def test_annotations_do_not_change_hash_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "aerolib.csv"
            nid = nid_names.compute_nid("sceExample")
            cache.write_text(f"{nid}#A#B sceExample#verified\n", encoding="utf-8")
            self.assertEqual(nid_names.load_db(cache), {nid: "sceExample"})


class SourceCollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.prx = self.root / "core" / "libs" / "prx"
        self.module = self.prx / "libSceExample"
        self.module.mkdir(parents=True)
        self.root_patch = patch.object(nid_names, "ROOT", self.root)
        self.prx_patch = patch.object(nid_names, "PRX", self.prx)
        self.root_patch.start()
        self.prx_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.prx_patch.stop)

    def write_source(self, text, relative="Export.cpp"):
        path = self.module / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_comments_and_literals_do_not_create_export_records(self):
        self.write_source('''// APS5_EXPORT("AAAAAAAAAAA", exampleUnknown00);
/* int APS5_VABI sceCommented() { return 0; } */
auto example = R"text(APS5_EXPORT("BBBBBBBBBBB", exampleUnknown01);
int APS5_VABI sceQuoted() { return 0; })text";
APS5_EXPORT("CCCCCCCCCCC", exampleUnknown02);
int APS5_VABI sceReal() { return 0; }
''')
        self.assertEqual(nid_names.collect_unknowns(), {"CCCCCCCCCCC": "core/libs/prx/libSceExample/Export.cpp:exampleUnknown02"})
        self.assertEqual(nid_names.collect_real_names(), {"sceReal"})

    def test_formatted_alias_macro_is_detected(self):
        self.write_source('APS5_EXPORT (\n "AAAAAAAAAAA" ,\n exampleUnknown00\n );\n')
        self.assertEqual(nid_names.collect_unknowns(), {"AAAAAAAAAAA": "core/libs/prx/libSceExample/Export.cpp:exampleUnknown00"})

    def test_test_fixtures_are_not_runtime_implementations(self):
        self.write_source('APS5_EXPORT("AAAAAAAAAAA", exampleUnknown00);\nint APS5_VABI sceFixture() { return 0; }\n', "tests/Fixture.cpp")
        self.assertEqual(nid_names.collect_unknowns(), {})
        self.assertEqual(nid_names.collect_real_names(), set())

    def test_unreadable_sources_fail_instead_of_returning_incomplete_names(self):
        self.write_source('int APS5_VABI sceReal() { return 0; }\n')
        with patch.object(Path, "read_text", side_effect=OSError("read denied")):
            with self.assertRaisesRegex(OSError, "read denied"):
                nid_names.collect_unknowns()
            with self.assertRaisesRegex(OSError, "read denied"):
                nid_names.collect_real_names()

    def test_explicit_empty_nid_filter_does_not_scan_the_tree(self):
        cache = self.root / "aerolib.csv"
        cache.write_text("AAAAAAAAAAA example\n", encoding="utf-8")
        output = io.StringIO()
        with patch.object(sys, "argv", ["nid_names.py", "--db", str(cache), "--nid", "--json"]), patch.object(nid_names, "collect_unknowns", side_effect=AssertionError("unexpected tree scan")), contextlib.redirect_stdout(output):
            self.assertEqual(nid_names.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["rows"], [])

    def test_literals_and_digit_separators_keep_following_definitions(self):
        self.write_source('''auto count = 1'000;
auto quote = '\\'';
auto string = "\\\" APS5_VABI sceQuoted() { }";
int APS5_VABI sceReal() { return 0; }
''')
        self.assertEqual(nid_names.collect_real_names(), {"sceReal"})

    def test_spliced_line_comment_and_macro_tokens(self):
        self.write_source('// continued \\\nAPS5_EXPORT("AAAAAAAAAAA", exampleUnknown00);\nAPS5_EX\\\nPORT("BBBBBBBBBBB", exampleUnknown01);\n')
        self.assertEqual(nid_names.collect_unknowns(), {"BBBBBBBBBBB": "core/libs/prx/libSceExample/Export.cpp:exampleUnknown01"})


if __name__ == "__main__":
    unittest.main()
