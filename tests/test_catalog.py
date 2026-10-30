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

    def wikimedia_fixture(self, svg=False):
        folder = self.root / "sources" / "wikimedia"
        originals = folder / "originals"
        originals.mkdir(parents=True)
        logo = self.picture("commons-123.png")
        original = originals / ("123-source.svg" if svg else "123-source.png")
        original.write_bytes(
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 5"><path d="M0 0h8v5H0z"/></svg>'
            if svg
            else logo.read_bytes()
        )
        checksum = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        item = {
            "page_id": 123,
            "title": "File:France 2 logo.svg",
            "categories": ["Category:TV logos"],
            "description_url": "https://commons.wikimedia.org/wiki/File:France_2_logo.svg",
        }
        asset = dict(
            item,
            image_url="https://upload.wikimedia.org/logo.svg",
            source_timestamp="2026-10-30T00:00:00Z",
            original=original.relative_to(self.root).as_posix(),
            original_sha256=checksum(original),
            logo="logosChannels/commons-123.png",
            logo_sha256=checksum(logo),
            width=8,
            height=5,
            source_format="SVG" if svg else "PNG",
            transparency=True,
            artist="Example artist",
            credit="Original credit",
            license_short_name="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution_required=True,
        )
        manifest = {
            "schema_version": 1,
            "categories": item["categories"],
            "assets": [asset],
        }
        inventory = {
            "schema_version": 1,
            "categories": item["categories"],
            "expected_count": 1,
            "files": [item],
            "excluded": [],
        }
        (folder / "manifest.json").write_text(json.dumps(manifest))
        (folder / "inventory.json").write_text(json.dumps(inventory))
        return folder, manifest, inventory

    def test_wikimedia_radio_selection_preserves_existing_station_identity(self):
        path, manifest = self.radio_manifest()
        path.write_text(json.dumps(manifest))
        folder, _, _ = self.wikimedia_fixture()
        station = manifest["stations"][0]
        selection = {field: station[field] for field in MODULE.PUBLIC_FIELDS}
        selection.update(page_id=123, logo="logosChannels/commons-123.png")
        selection_path = folder / "selections.json"
        selection_path.write_text(json.dumps({"entries": [selection]}))
        entry = self.build()["catalog.v1.json"]["entries"][0]
        self.assertEqual(
            entry, {field: selection[field] for field in MODULE.PUBLIC_FIELDS}
        )
        selection["name"] = "Different station"
        selection_path.write_text(json.dumps({"entries": [selection]}))
        with self.assertRaisesRegex(ValueError, "differs from existing station"):
            self.build()
        selection["name"] = station["name"]
        selection["id"] = "radio.xx.missing"
        selection_path.write_text(json.dumps({"entries": [selection]}))
        with self.assertRaisesRegex(ValueError, "differs from existing station"):
            self.build()

    def test_wikimedia_offline_svg_provenance_and_coverage(self):
        folder, manifest, inventory = self.wikimedia_fixture(svg=True)
        outputs = self.build()
        report = outputs["reports/wikimedia.json"]
        self.assertEqual(report["imported_count"], 1)
        self.assertEqual(report["assets"][0]["artist"], "Example artist")
        self.assertEqual(report["assets"][0]["license_short_name"], "CC BY-SA 4.0")
        self.assertEqual(report["assets"][0]["selected_entries"], [])
        original = outputs["reports/images.json"]["originals"][0]
        self.assertEqual(original["format"], "SVG")
        self.assertEqual(original["view_box"], "0 0 8 5")
        self.assertEqual(outputs, self.build())
        inventory["files"].append(dict(inventory["files"][0], page_id=456))
        inventory["expected_count"] = 2
        (folder / "inventory.json").write_text(json.dumps(inventory))
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.build()

    def test_wikimedia_original_hash_render_and_duplicate_ids_are_blocking(self):
        folder, manifest, inventory = self.wikimedia_fixture()
        asset = manifest["assets"][0]
        for field, bad in [
            ("original_sha256", "bad"),
            ("logo_sha256", "bad"),
            ("width", 999),
            ("transparency", False),
            ("original", "../outside.png"),
        ]:
            with self.subTest(field=field):
                previous = asset[field]
                asset[field] = bad
                (folder / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    self.build()
                asset[field] = previous
        manifest["assets"].append(dict(asset))
        (folder / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Duplicate.*page"):
            self.build()
        manifest["assets"].pop()
        (folder / "manifest.json").write_text(json.dumps(manifest))
        (self.root / asset["logo"]).write_bytes(b"bad PNG render")
        with self.assertRaisesRegex(ValueError, "commons-123.png"):
            self.build()

    def test_wikimedia_svg_entities_and_non_svg_xml_are_rejected(self):
        folder, manifest, inventory = self.wikimedia_fixture(svg=True)
        asset = manifest["assets"][0]
        path = self.root / asset["original"]
        for data in [
            b'<!DOCTYPE svg [<!ENTITY x "boom">]><svg>&x;</svg>',
            b"<html><body>not svg</body></html>",
            b"<svg>",
        ]:
            with self.subTest(data=data):
                path.write_bytes(data)
                asset["original_sha256"] = hashlib.sha256(data).hexdigest()
                (folder / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "SVG"):
                    self.build()

    def test_wikimedia_svg_external_dtd_is_not_loaded(self):
        folder, manifest, inventory = self.wikimedia_fixture(svg=True)
        asset = manifest["assets"][0]
        data = (
            b'<!DOCTYPE svg SYSTEM "file:///missing-never-read.dtd">'
            b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        )
        path = self.root / asset["original"]
        path.write_bytes(data)
        asset["original_sha256"] = hashlib.sha256(data).hexdigest()
        (folder / "manifest.json").write_text(json.dumps(manifest))
        self.assertEqual(
            self.build()["reports/images.json"]["originals"][0]["format"], "SVG"
        )

    def test_wikimedia_explicit_exclusions_preserve_inventory(self):
        folder, manifest, inventory = self.wikimedia_fixture()
        excluded = dict(
            inventory["files"][0],
            page_id=456,
            title="File:Photo.jpg",
            reason="Photograph rather than a logo",
        )
        inventory["files"].append(
            {key: value for key, value in excluded.items() if key != "reason"}
        )
        inventory["excluded"] = [excluded]
        inventory["expected_count"] = 2
        (folder / "inventory.json").write_text(json.dumps(inventory))
        report = self.build()["reports/wikimedia.json"]
        self.assertEqual(report["excluded"], [excluded])
        self.assertEqual(report["expected_count"], 2)
        inventory["excluded"][0]["page_id"] = 123
        (folder / "inventory.json").write_text(json.dumps(inventory))
        with self.assertRaises(ValueError):
            self.build()

    def test_wikimedia_selection_upserts_stable_media_and_validates_backups(self):
        folder, manifest, inventory = self.wikimedia_fixture()
        asset = manifest["assets"][0]
        current = self.picture("France 2.png")
        self.manifest["entries"] = [dict(self.entry(), name="Old name")]
        selected = dict(self.entry(), page_id=123)
        (folder / "selections.json").write_text(json.dumps({"entries": [selected]}))
        backup_dir = folder / "legacy-originals"
        backup_dir.mkdir()
        backup = backup_dir / "old.png"
        backup.write_bytes(current.read_bytes())
        current.write_bytes((self.root / asset["logo"]).read_bytes())
        upsert = {
            "page_id": 123,
            "logo": selected["logo"],
            "logo_sha256": asset["logo_sha256"],
            "previous_logo": backup.relative_to(self.root).as_posix(),
            "previous_logo_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        }
        (folder / "upserts.json").write_text(json.dumps({"upserts": [upsert]}))
        outputs = self.build()
        self.assertEqual(outputs["catalog.v1.json"]["entries"], [self.entry()])
        self.assertEqual(
            outputs["reports/wikimedia.json"]["assets"][0]["selected_entries"],
            [self.entry()],
        )
        self.assertEqual(outputs, self.build())
        backup.write_bytes(b"changed backup")
        with self.assertRaisesRegex(ValueError, "backup|previous_logo"):
            self.build()

    def test_wikimedia_selection_must_match_render_and_has_no_duplicate_media_ids(self):
        folder, manifest, inventory = self.wikimedia_fixture()
        current = self.picture("France 2.png", color=(1, 2, 3, 255))
        selected = dict(self.entry(), page_id=123)
        (folder / "selections.json").write_text(json.dumps({"entries": [selected]}))
        with self.assertRaisesRegex(
            ValueError, "selection.*checksum|selected.*checksum"
        ):
            self.build()
        current.write_bytes((self.root / manifest["assets"][0]["logo"]).read_bytes())
        (folder / "selections.json").write_text(
            json.dumps({"entries": [selected, selected]})
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.build()

    def test_wikimedia_legacy_alias_targets_share_one_media_identity(self):
        folder, manifest, inventory = self.wikimedia_fixture()
        published = (self.root / manifest["assets"][0]["logo"]).read_bytes()
        for name in ("France 2.png", "France2HD.png"):
            (self.root / "logosChannels" / name).write_bytes(published)
        selection = dict(
            self.entry(),
            page_id=123,
            legacy_paths=["logosChannels/France 2.png", "logosChannels/France2HD.png"],
        )
        path = folder / "selections.json"
        path.write_text(json.dumps({"entries": [selection]}))
        output = self.build()
        self.assertEqual(output["catalog.v1.json"]["entries"], [self.entry()])
        selected = output["reports/wikimedia.json"]["assets"][0]["selected_entries"][0]
        self.assertEqual(selected["legacy_paths"], selection["legacy_paths"])
        second = dict(
            selection, id="tv.fr.another", logo="logosChannels/commons-123.png"
        )
        path.write_text(json.dumps({"entries": [selection, second]}))
        with self.assertRaisesRegex(ValueError, "Duplicate.*selected logo"):
            self.build()
        path.write_text(json.dumps({"entries": [selection]}))
        (self.root / "logosChannels" / "France2HD.png").write_bytes(b"not PNG")
        with self.assertRaisesRegex(ValueError, "France2HD.png"):
            self.build()

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
