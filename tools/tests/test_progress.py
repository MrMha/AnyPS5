import contextlib
import io
import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import progress


class ProgressTests(unittest.TestCase):
    def test_test_sources_do_not_change_library_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tests" / "prx"
            library = root / "libSceExample"
            source = library / "src"
            source.mkdir(parents=True)
            (source / "Export.cpp").write_text(
                "int APS5_VABI Ready() { return 0; }\n"
                "int APS5_VABI Pending() { NotImplemented_nid_no_patch(__func__); }\n")
            alias = root / "libSceExample.native"
            alias.mkdir()
            (alias / "CMakeLists.txt").write_text('include("${CMAKE_CURRENT_SOURCE_DIR}/../libSceExample/Library.cmake")\n')
            with patch.object(progress, "PRX", root):
                base = progress.collect_libraries()
                self.assertEqual(base["groups"][0]["done_names"], ["Ready"])
                self.assertEqual(base["groups"][0]["todo_names"], ["Pending"])
                tests = library / "tests"
                nested = tests / "fixtures"
                nested.mkdir(parents=True)
                (tests / "Callbacks.cpp").write_text(
                    "int APS5_VABI FixtureReady() { return 0; }\n"
                    "int APS5_VABI Pending() { return 0; }\n"
                    "int APS5_VABI FixturePending() { NotImplemented_nid_no_patch(__func__); }\n")
                (nested / "Callbacks.cpp").write_text("int APS5_VABI NestedFixture() { return 0; }\n")
                (alias / "tests").mkdir()
                (alias / "tests" / "Callbacks.cpp").write_text("int APS5_VABI AliasFixture() { return 0; }\n")
                head = progress.collect_libraries()
                self.assertEqual(head, base)
                self.assertEqual(progress.compare("Libraries", "Library", "functions", base, head), [])

    def test_shared_source_refactor_preserves_functions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner = root / "libSceExample"
            alias = root / "libSceExample.native"
            empty = root / "libSceEmpty"
            for path in (owner, alias, empty):
                path.mkdir()
            source = "int APS5_VABI Ready() { return 0; }\nint APS5_VABI Pending() { NotImplemented_nid_no_patch(__func__); }\n"
            (owner / "Export.cpp").write_text(source)
            (alias / "Export.cpp").write_text(source)
            (owner / "Library.cmake").write_text("add_library(example SHARED Export.cpp)\n")
            (empty / "CMakeLists.txt").write_text('# include(${CMAKE_CURRENT_SOURCE_DIR}/../libSceExample/Library.cmake)\n')
            with patch.object(progress, "PRX", root):
                base = progress.collect_libraries()
                self.assertEqual((base["done"], base["total"]), (2, 4))
                (alias / "Export.cpp").unlink()
                (alias / "CMakeLists.txt").write_text('include("${CMAKE_CURRENT_SOURCE_DIR}/../libSceExample/Library.cmake")\n')
                head = progress.collect_libraries()
                self.assertEqual((head["done"], head["total"]), (1, 2))
                groups = {group["name"]: group for group in head["groups"]}
                self.assertEqual(groups[alias.name]["shared_sources"], owner.name)
                self.assertNotIn("shared_sources", groups[empty.name])
                html = progress.table("Libraries", "Library", head)
                self.assertIn('colspan="3">Shared sources:', html)
                self.assertIn("shared by " + alias.name, "\n".join(progress.treemap("Libraries", head, 0)))
                report = "\n".join(progress.compare("Libraries", "Library", "functions", base, head))
                self.assertIn("sharing sources", report)
                self.assertNotIn("removed", report)
                self.assertNotIn("implemented", report)
                self.assertEqual(progress.compare("Libraries", "Library", "functions", head, head), [])
                (owner / "Export.cpp").write_text(source.replace("return 0;", "NotImplemented_nid_no_patch(__func__);"))
                regressed = progress.collect_libraries()
                report = "\n".join(progress.compare("Libraries", "Library", "functions", base, regressed))
                self.assertIn("-1 reverted", report)
                self.assertIn("Ready", report)
                (owner / "Export.cpp").write_text("int APS5_VABI Ready() { return 0; }\n")
                removed = progress.collect_libraries()
                report = "\n".join(progress.compare("Libraries", "Library", "functions", base, removed))
                self.assertIn("-1 removed", report)
                self.assertIn("Pending", report)

    def test_empty_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner = root / "libSceExample"
            alias = root / "libSceExampleBackend"
            dummy = root / "libSceDummy"
            for path in (owner, alias, dummy):
                path.mkdir()
            (owner / "Export.cpp").write_text("int APS5_VABI Ready() { return 0; }\n")
            (alias / "Export.cpp").write_text('#include "prx/libSceExample/Export.cpp"\n')
            (dummy / "Export.cpp").write_text('#include "prx/libc/include/General.hpp"\nextern "C" {\nAPS5_DUMMY_FUN\n}\n')
            with patch.object(progress, "PRX", root):
                data = progress.collect_libraries()
            groups = {group["name"]: group for group in data["groups"]}
            self.assertEqual(groups[alias.name]["shared_sources"], owner.name)
            self.assertNotIn("shared_sources", groups[dummy.name])
            self.assertEqual((data["done"], data["total"]), (1, 1))
            html = progress.table("Libraries", "Library", data)
            self.assertIn(f'<td>{alias.name}</td><td colspan="3">Shared sources:', html)
            self.assertIn(f'<td>{dummy.name}</td><td colspan="3">No exports</td>', html)
            self.assertNotIn("<td>0</td>", html)


    def fixture(self, directory, library="libSceExample", function="Ready"):
        root = Path(directory)
        script = root / "runner" / "tools" / "progress.py"
        script.parent.mkdir(parents=True)
        shutil.copyfile(progress.__file__, script)
        (script.parent / "rdna_isa.txt").write_text("S_ADD_U32 SOP2\n", encoding="utf-8")
        target = root / "target"
        source = target / "core" / "libs" / "prx" / library / "Export.cpp"
        source.parent.mkdir(parents=True)
        source.write_text(f"int APS5_VABI {function}() {{ return 0; }}\n", encoding="utf-8")
        opcodes = target / progress.OPCODES.relative_to(progress.ROOT)
        opcodes.parent.mkdir(parents=True)
        opcodes.write_text("enum class RdnaOpcode {\n SAddU32,\n Count,\n};\n", encoding="utf-8")
        (target / "tools").mkdir()
        (target / "tools" / "rdna_isa.txt").write_text("S_ADD_U32 SOP2\n", encoding="utf-8")
        return script, target, root / "output"

    def test_alternate_root_uses_its_own_instruction_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            script, target, output = self.fixture(directory)
            (target / "tools" / "rdna_isa.txt").write_text(
                "S_ADD_U32 SOP2\nS_SUB_U32 SOP2\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(script), "--root", str(target), str(output)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads((output / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual((data["shaders"]["done"], data["shaders"]["total"]), (1, 2))
            self.assertEqual(data["shaders"]["groups"][0]["todo_names"], ["S_SUB_U32"])

    def test_generation_preserves_utf8_with_a_legacy_default_encoding(self):
        real_open = io.open

        def legacy_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None,
                        closefd=True, opener=None):
            if "b" not in mode and encoding in (None, "locale"):
                encoding = "cp1252"
            return real_open(file, mode, buffering, encoding, errors, newline, closefd, opener)

        with tempfile.TemporaryDirectory() as directory:
            script, target, output = self.fixture(directory, "libSce\u6e2c\u8a66", "Ready\u6e2c\u8a66")
            args = [str(script), "--root", str(target), str(output)]
            with patch.object(sys, "argv", args), patch("io.open", legacy_open):
                with contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(str(script), run_name="__main__")
            data = json.loads((output / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(data["libraries"]["done"], 1)
            self.assertEqual(data["libraries"]["groups"][0]["done_names"], ["Ready\u6e2c\u8a66"])
            self.assertIn("libSce\u6e2c\u8a66", (output / "index.html").read_text(encoding="utf-8"))
            ElementTree.parse(output / "progress.svg")

    def test_lopsided_treemap_keeps_every_cell_inside_its_panel(self):
        data = progress.summarize([
            {"name": "libSceLarge", "label": "Large", "done": 1000, "todo": 0},
            {"name": "libSceSmall", "label": "Small", "done": 1, "todo": 0},
        ])
        svg = '<svg>' + "\n".join(progress.treemap("Libraries", data, 0)) + '</svg>'
        root = ElementTree.fromstring(svg)
        rectangles = root.findall(".//rect")
        self.assertEqual(len(rectangles), 1003)
        for rectangle in rectangles:
            x, y, width, height = (float(rectangle.attrib[name]) for name in ("x", "y", "width", "height"))
            self.assertGreaterEqual(width, 0)
            self.assertGreaterEqual(height, 0)
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, progress.HEADER)
            self.assertLessEqual(x + width, progress.PANEL_WIDTH + 0.02)
            self.assertLessEqual(y + height, progress.HEADER + progress.MAP_HEIGHT + 0.02)


if __name__ == "__main__":
    unittest.main()
