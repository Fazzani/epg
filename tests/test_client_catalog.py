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

    def test_release_checksums_bind_catalog_and_credits(self):
        self.image()
        files = self.build()
        release = self.document(files, "catalog.release.v1.json")
        self.assertEqual(release["schema_version"], 1)
        for key, path in (
            ("catalog_sha256", "catalog.client.v1.json"),
            ("credits_sha256", "credits.client.v1.json"),
        ):
            self.assertEqual(release[key], hashlib.sha256(files[path]).hexdigest())

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

    def test_credits_keep_identity_source_license_and_remove_html(self):
        source = self.image()
        folder = self.root / "sources" / "wikimedia"
        folder.mkdir(parents=True)
        asset = {
            "page_id": 42,
            "logo_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "title": "File:Example.png",
            "description_url": "https://commons.wikimedia.org/wiki/File:Example.png",
            "artist": '<a href="https://example.com">Alice &amp; Bob</a>',
            "credit": "<b>Original</b> credit",
            "license_short_name": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution_required": True,
        }
        (folder / "manifest.json").write_text(json.dumps({"assets": [asset]}))
        (folder / "selections.json").write_text(
            json.dumps({"entries": [dict(self.entries[0], page_id=42)]})
        )
        credits = self.document(self.build(), "credits.client.v1.json")["entries"]
        self.assertEqual(credits[0]["artist"], "Alice & Bob")
        self.assertEqual(credits[0]["credit"], "Original credit")
        self.assertEqual(credits[0]["license"], "CC BY 4.0")
        self.assertTrue(credits[0]["attribution_required"])
        self.assertEqual(credits[0]["source_url"], asset["description_url"])
        self.assertTrue(credits[0]["changes"])
        asset["logo_sha256"] = "wrong"
        (folder / "manifest.json").write_text(json.dumps({"assets": [asset]}))
        with self.assertRaisesRegex(ValueError, "credit image differs"):
            self.build()

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

    def test_selected_radio_credit_comes_from_wikimedia(self):
        source = self.image(identity="radio.tn.example")
        self.entries[0].update(type="radio", country=None, aliases=[])
        radios = self.root / "sources/radios"
        radios.mkdir(parents=True)
        (radios / "manifest.json").write_text(
            json.dumps(
                {
                    "stations": [
                        {
                            "id": "radio.tn.example",
                            "name": "Example",
                            "logo": "logosChannels/old.png",
                            "logo_sha256": "old",
                            "page_url": "https://example.com/radio",
                        }
                    ]
                }
            )
        )
        wiki = self.root / "sources/wikimedia"
        wiki.mkdir(parents=True)
        asset = {
            "page_id": 42,
            "logo_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "title": "File:Example.png",
            "description_url": "https://commons.wikimedia.org/wiki/File:Example.png",
            "artist": "Artist",
            "credit": "Source",
            "license_short_name": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution_required": True,
        }
        (wiki / "manifest.json").write_text(json.dumps({"assets": [asset]}))
        (wiki / "selections.json").write_text(
            json.dumps({"entries": [dict(self.entries[0], page_id=42)]})
        )
        credit = self.document(self.build(), "credits.client.v1.json")["entries"][0]
        self.assertEqual(credit["source_url"], asset["description_url"])
        self.assertEqual(credit["license"], "CC BY 4.0")

    def test_cli_separate_outputs_leave_source_catalogs_and_images_unchanged(self):
        self.image()
        self.build()
        for name in (
            "build_client_catalog.py",
            "update_index.py",
            "upsert_wikimedia.py",
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
