# -*- coding: utf-8 -*-
"""build_bundle 回归测试（stdlib unittest，零依赖）。"""
import sys
import hashlib
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_bundle  # noqa: E402


class IgnoreTests(unittest.TestCase):
    def test_excludes_vendored_by_default(self):
        ig = build_bundle._make_ignore(offline=False)
        out = ig("d", ["_models", "_tiktoken", "foo.py"])
        self.assertIn("_models", out)
        self.assertIn("_tiktoken", out)
        self.assertNotIn("foo.py", out)

    def test_keeps_vendored_when_offline(self):
        ig = build_bundle._make_ignore(offline=True)
        out = ig("d", ["_models", "_tiktoken", "foo.py"])
        self.assertNotIn("_models", out)
        self.assertNotIn("_tiktoken", out)
        # normal junk still excluded in both modes
        self.assertIn("__pycache__", ig("d", ["__pycache__"]))
        self.assertIn("x.pyc", ig("d", ["x.pyc"]))


class NameTests(unittest.TestCase):
    def test_zip_name(self):
        self.assertEqual(build_bundle._zip_name("1.1.2", False),
                         "anydocsmarked-v1.1.2.zip")
        self.assertEqual(build_bundle._zip_name("1.1.2", True),
                         "anydocsmarked-v1.1.2-offline.zip")

    def test_component_version_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "版本不一致"):
            build_bundle._validate_component_versions(
                "1.7.0", {"makeitdown": "1.7.0", "rag-retriever": "1.6.0"}
            )

    def test_matching_component_versions_pass(self):
        build_bundle._validate_component_versions(
            "1.7.0", {"makeitdown": "1.7.0", "rag-retriever": "1.7.0"}
        )

    def test_declared_and_runtime_versions_are_all_release_aligned(self):
        self.assertEqual(
            build_bundle._component_versions(),
            {
                "makeitdown": "2.1.0",
                "makeitdown.__version__": "2.1.0",
                "rag-retriever": "2.1.0",
                "rag-retriever.__version__": "2.1.0",
            },
        )


class VendoredCheckTests(unittest.TestCase):
    def test_absent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(build_bundle._has_vendored_models(Path(d)))

    def test_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "rag_retriever" / "_models" / "BAAI--x"
            m.mkdir(parents=True)
            (m / "model.onnx").write_bytes(b"x")
            self.assertTrue(build_bundle._has_vendored_models(Path(d)))


class BundleRootContentsTests(unittest.TestCase):
    def test_ingest_scripts_present_at_bundle_root(self):
        # Regression guard: SKILL.md instructs agents/desktop users to run
        # `<bundle-root>/ingest.py` and `<bundle-root>/ingest_gui.py`; both
        # must actually land at the bundle root, not just skill/lawiki/.
        version = build_bundle.VERSION_FILE.read_text(encoding="utf-8").strip()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / f"anydocsmarked-v{version}"
            root.mkdir()
            build_bundle._assemble_bundle_root(root, version, offline=False)
            self.assertTrue((root / "install.py").is_file())
            self.assertTrue((root / "ingest.py").is_file())
            self.assertTrue((root / "ingest_gui.py").is_file())
            self.assertTrue((root / "skill" / "lawiki").is_dir())


class ChecksumTests(unittest.TestCase):
    def test_checksum_manifest_covers_all_bundles(self):
        with tempfile.TemporaryDirectory() as d:
            dist = Path(d)
            first = dist / "anydocsmarked-v1.7.0.zip"
            second = dist / "anydocsmarked-v1.7.0-offline.zip"
            stale = dist / "anydocsmarked-v1.6.0.zip"
            first.write_bytes(b"source")
            second.write_bytes(b"offline")
            stale.write_bytes(b"stale")

            manifest = build_bundle._write_checksums(dist, "1.7.0")

            lines = manifest.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                lines,
                [
                    f"{hashlib.sha256(second.read_bytes()).hexdigest()}  {second.name}",
                    f"{hashlib.sha256(first.read_bytes()).hexdigest()}  {first.name}",
                ],
            )


if __name__ == "__main__":
    unittest.main()
