import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location(
    "client_catalog", ROOT / "build_client_catalog.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ClientCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "logosChannels").mkdir()
        self.entries = []

    def image(
        self, name="example.png", size=(600, 200), alpha=120, identity="tv.fr.example"
    ):
        path = self.root / "logosChannels" / name
        Image.new("RGBA", size, (32, 64, 128, alpha)).save(path)
        self.entries.append(
            {
                "id": identity,
                "name": "Example",
                "type": "tv",
                "country": "FR",
                "aliases": ["FR: Example"],
                "logo": f"logosChannels/{name}",
            }
        )
        return path

    def build(self):
        (self.root / "catalog.v1.json").write_text(
            json.dumps({"schema_version": 1, "entries": self.entries})
        )
        return MODULE.build_outputs(self.root)

    def document(self, files, name="catalog.client.v1.json"):
        return json.loads(files[name])

    def test_proportions_alpha_and_content_addressed_files(self):
        source = self.image()
        before = source.read_bytes()
        files = self.build()
        entry = self.document(files)["entries"][0]
        self.assertEqual(entry["aliases"], ["FR: Example"])
        for size in (128, 256):
            variant = entry["variants"][str(size)]
            data = files[variant["path"]]
            self.assertEqual(hashlib.sha256(data).hexdigest(), variant["sha256"])
            self.assertEqual(variant["path"], f"logosClient/{variant['sha256']}.png")
            self.assertEqual(len(data), variant["bytes"])
            self.assertLessEqual(max(variant["width"], variant["height"]), size)
            self.assertAlmostEqual(variant["width"] / variant["height"], 3, delta=0.05)
            import io

            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (variant["width"], variant["height"]))
                self.assertEqual(image.getchannel("A").getextrema(), (120, 120))
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(files, self.build())

    def test_small_sources_are_not_enlarged_and_shared_bytes_are_deduplicated(self):
        self.image(size=(30, 10))
        self.image("other.png", size=(30, 10), identity="tv.fr.other")
        files = self.build()
        variants = [
            entry["variants"][size]
            for entry in self.document(files)["entries"]
            for size in ("128", "256")
        ]
        self.assertTrue(
            all(
                (variant["width"], variant["height"]) == (30, 10)
                for variant in variants
            )
        )
        self.assertEqual(len({variant["path"] for variant in variants}), 1)
        self.assertEqual(len([path for path in files if path.endswith(".png")]), 1)

    def test_release_checksum_binds_catalog_without_credits(self):
        self.image()
        files = self.build()
        release = self.document(files, "catalog.release.v1.json")
        self.assertEqual(release["schema_version"], 1)
        self.assertEqual(
            release["catalog_sha256"],
            hashlib.sha256(files["catalog.client.v1.json"]).hexdigest(),
        )
        self.assertNotIn("credits_sha256", release)
        self.assertNotIn("credits.client.v1.json", files)

    def test_changed_logo_changes_only_its_variant_revisions(self):
        first = self.image()
        self.image("unchanged.png", identity="tv.fr.unchanged")
        before = self.document(self.build())["entries"]
        Image.new("RGBA", (600, 200), (255, 0, 0, 255)).save(first)
        after = self.document(self.build())["entries"]
        self.assertEqual(before[0]["id"], after[0]["id"])
        self.assertNotEqual(before[0]["variants"], after[0]["variants"])
        self.assertEqual(before[1], after[1])

    def test_check_rejects_missing_or_corrupt_files_and_writes_nothing(self):
        self.image()
        files = self.build()
        target = self.root / "build"
        MODULE.publish(files, target)
        MODULE.publish(files, target, check=True)
        png = next(path for path in files if path.endswith(".png"))
        path = target / png
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing or outdated"):
            MODULE.publish(files, target, check=True)
        self.assertFalse(path.exists())
        path.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            MODULE.publish(files, target, check=True)
        self.assertEqual(path.read_bytes(), b"corrupt")

    def test_rejects_unsafe_paths_invalid_images_and_duplicate_ids(self):
        source = self.image()
        self.entries[0]["logo"] = "../outside.png"
        with self.assertRaises(ValueError):
            self.build()
        self.entries[0]["logo"] = "logosChannels/example.png"
        self.entries.append(dict(self.entries[0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.build()
        self.entries.pop()
        source.write_bytes(b"bad PNG")
        with self.assertRaisesRegex(ValueError, "example.png"):
            self.build()

    def test_cli_separate_outputs_leave_source_catalogs_and_images_unchanged(self):
        self.image()
        self.build()
        for name in (
            "build_client_catalog.py",
            "update_index.py",
        ):
            (self.root / name).write_bytes((ROOT / name).read_bytes())
        before = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        command = [
            sys.executable,
            str(self.root / "build_client_catalog.py"),
            "--output-dir",
            str(self.root / "dist"),
        ]
        for arguments in (command, command + ["--check"]):
            result = subprocess.run(arguments, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        for name, data in before.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertFalse((self.root / "catalog.client.v1.json").exists())


if __name__ == "__main__":
    unittest.main()
