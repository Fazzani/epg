import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import hashlib
import unittest

from PIL import Image

SPEC = importlib.util.spec_from_file_location(
    "update_index", Path(__file__).resolve().parents[1] / "update_index.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "logosChannels").mkdir()
        self.manifest = {"entries": [], "preferred_images": {}}

    def picture(self, name, mode="RGBA", color=(20, 40, 60, 120), format=None):
        path = self.root / "logosChannels" / name
        Image.new(mode, (8, 5), color).save(path, format=format)
        return path

    def build(self):
        (self.root / "logo_manifest.json").write_text(json.dumps(self.manifest))
        return MODULE.build_outputs(self.root)

    def entry(self, logo="logosChannels/France 2.png"):
        return {
            "id": "tv.fr.france-2",
            "name": "France 2",
            "type": "tv",
            "country": "FR",
            "aliases": ["France2"],
            "logo": logo,
        }

    def test_legacy_contract_exact_unicode_and_case_and_only_images(self):
        self.picture("Été.PNG")
        self.picture("France 2.png")
        self.picture("photo.jfif", "RGB", (20, 40, 60), format="JPEG")
        for name in ("Thumbs.db", "script.py", "old.png.bak"):
            (self.root / "logosChannels" / name).write_text("not an image")
        outputs = self.build()
        self.assertEqual(
            outputs["index_picons.json"],
            {"name": ["France 2.png", "photo.jfif", "Été.PNG"]},
        )
        metadata = outputs["reports/images.json"]["images"]
        self.assertEqual(metadata[0]["width"], 8)
        self.assertEqual(metadata[0]["height"], 5)
        self.assertTrue(metadata[0]["has_transparency"])
        self.assertEqual(metadata[1]["format"], "JPEG")

    def test_collision_requires_explicit_preference_and_retains_variants(self):
        self.picture("Media.png")
        self.picture("media.jpg", "RGB", (20, 40, 60))
        with self.assertRaisesRegex(ValueError, "preference.*media"):
            self.build()
        self.manifest["preferred_images"]["media"] = "Media.png"
        outputs = self.build()
        self.assertEqual(
            outputs["index_picons.json"]["name"], ["Media.png", "media.jpg"]
        )
        self.assertEqual(
            outputs["reports/collisions.json"]["collisions"][0]["preferred"],
            "Media.png",
        )
        self.manifest["preferred_images"]["media"] = "missing.png"
        with self.assertRaisesRegex(ValueError, "preference"):
            self.build()

    def test_catalog_identity_survives_image_replacement_and_projects_public_fields(
        self,
    ):
        self.picture("France 2.png")
        entry = self.entry()
        entry["private_note"] = "source metadata"
        self.manifest["entries"] = [entry]
        initial = self.build()["catalog.v1.json"]["entries"][0]
        self.picture("replacement.png")
        entry["logo"] = "logosChannels/replacement.png"
        changed = self.build()["catalog.v1.json"]["entries"][0]
        self.assertEqual(initial["id"], changed["id"])
        self.assertEqual(
            set(changed), {"id", "name", "type", "country", "aliases", "logo"}
        )
        self.assertEqual(
            self.build()["reports/unidentified.json"]["images"],
            ["logosChannels/France 2.png"],
        )

    def test_invalid_image_is_blocking_with_filename(self):
        (self.root / "logosChannels" / "broken.png").write_bytes(b"invalid")
        with self.assertRaisesRegex(ValueError, "broken.png"):
            self.build()

    def test_truncated_image_is_blocking(self):
        path = self.picture("truncated.png")
        path.write_bytes(path.read_bytes()[:45])
        with self.assertRaisesRegex(ValueError, "truncated.png"):
            self.build()

    def test_all_animation_frames_are_decoded(self):
        path = self.root / "logosChannels" / "animated.gif"
        frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue")]
        frames[0].save(path, save_all=True, append_images=frames[1:])
        item = self.build()["reports/images.json"]["images"][0]
        self.assertEqual(item["frames"], 2)
        path.write_bytes(path.read_bytes()[:-8])
        with self.assertRaisesRegex(ValueError, "animated.gif"):
            self.build()

    def test_bad_metadata_and_unsafe_paths_are_rejected(self):
        self.picture("France 2.png")
        entry = self.entry()
        self.manifest["entries"] = [entry]
        for key, bad in [
            ("country", "Tunisia"),
            ("type", "stream"),
            ("id", "invalid"),
            ("aliases", "France2"),
            ("logo", "../France 2.png"),
            ("logo", "logosChannels/france 2.png"),
        ]:
            with self.subTest(key=key, bad=bad):
                original = entry[key]
                entry[key] = bad
                with self.assertRaises(ValueError):
                    self.build()
                entry[key] = original
        self.manifest["entries"].append(dict(entry))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.build()

    def test_unknown_country_is_allowed_and_ids_are_sorted(self):
        self.picture("France 2.png")
        self.manifest["entries"] = [
            self.entry(),
            {
                "id": "radio.xx.example",
                "name": "Example",
                "type": "radio",
                "country": None,
                "aliases": [],
                "logo": "logosChannels/France 2.png",
            },
        ]
        entries = self.build()["catalog.v1.json"]["entries"]
        self.assertEqual(
            [entry["id"] for entry in entries], ["radio.xx.example", "tv.fr.france-2"]
        )
        self.assertIsNone(entries[0]["country"])

    def test_generation_is_deterministic_and_does_not_change_originals(self):
        path = self.picture("France 2.png")
        original = path.read_bytes()
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(path.read_bytes(), original)
        output = self.root / "build"
        MODULE.write_outputs(first, output)
        MODULE.check_outputs(second, output)
        (output / "index_picons.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "index_picons.json"):
            MODULE.check_outputs(second, output)

    def test_actual_format_is_recorded_even_if_extension_differs(self):
        self.picture("disguised.jpg", format="PNG")
        item = self.build()["reports/images.json"]["images"][0]
        self.assertEqual(item["format"], "PNG")
        self.assertTrue(item["extension_mismatch"])

    def test_cli_output_directory_and_check_do_not_modify_sources(self):
        original = self.picture("France 2.png")
        self.build()
        script = self.root / "update_index.py"
        script.write_bytes(Path(MODULE.__file__).read_bytes())
        output = self.root / "generated"
        before = original.read_bytes()
        command = [sys.executable, str(script), "--output-dir", str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "index_picons.json").exists())
        result = subprocess.run(command + ["--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        (output / "catalog.v1.json").write_text("{}")
        result = subprocess.run(command + ["--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("catalog.v1.json", result.stderr)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual((output / "catalog.v1.json").read_text(), "{}")

    def radio_manifest(self):
        folder = self.root / "sources" / "radios" / "originals"
        folder.mkdir(parents=True)
        logo = self.picture("radio-tn-example.png")
        original = folder / "example.png"
        original.write_bytes(logo.read_bytes())
        checksum = hashlib.sha256(logo.read_bytes()).hexdigest()
        station = {
            "id": "radio.xx.example",
            "name": "Example",
            "type": "radio",
            "country": None,
            "aliases": [],
            "logo": "logosChannels/radio-tn-example.png",
            "page_url": "https://example.com/station",
            "image_url": "https://example.com/logo.png",
            "original": "sources/radios/originals/example.png",
            "original_sha256": checksum,
            "logo_sha256": checksum,
            "width": 8,
            "height": 5,
            "format": "PNG",
            "transparency": True,
            "fallback": False,
        }
        manifest = {"expected_count": 1, "stations": [station]}
        return folder.parent / "manifest.json", manifest

    def test_radio_source_and_output_checksums_and_provenance(self):
        path, manifest = self.radio_manifest()
        station = manifest["stations"][0]
        path.write_text(json.dumps(manifest))
        result = self.build()
        self.assertEqual(len(result["catalog.v1.json"]["entries"]), 1)
        self.assertEqual(len(result["reports/images.json"]["originals"]), 1)
        for field, bad in [
            ("original_sha256", "bad"),
            ("logo_sha256", "bad"),
            ("original", "../outside.png"),
            ("original", "sources/radios/originals/missing.png"),
            ("page_url", "http://example.com/station"),
            ("width", 100),
        ]:
            with self.subTest(field=field):
                previous = station[field]
                station[field] = bad
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    self.build()
                station[field] = previous
        original = self.root / station["original"]
        original.write_bytes(b"corrupted")
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "example.png"):
            self.build()

    def test_radio_duplicate_pages_and_published_paths_are_rejected(self):
        path, manifest = self.radio_manifest()
        manifest["expected_count"] = 2
        duplicate = dict(manifest["stations"][0])
        duplicate["id"] = "radio.xx.second"
        manifest["stations"].append(duplicate)
        for field, value in [
            ("page_url", "https://example.com/second"),
            ("logo", "logosChannels/radio-tn-second.png"),
        ]:
            with self.subTest(field=field):
                previous = duplicate[field]
                duplicate[field] = value
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "Duplicate radio"):
                    self.build()
                duplicate[field] = previous

    def test_incomplete_radio_manifest_is_blocking(self):
        path = self.root / "sources" / "radios"
        path.mkdir(parents=True)
        (path / "manifest.json").write_text(
            json.dumps(
                {
                    "source_url": "https://example.com",
                    "expected_count": 80,
                    "stations": [],
                }
            )
        )
        with self.assertRaisesRegex(ValueError, "80"):
            self.build()


if __name__ == "__main__":
    unittest.main()
