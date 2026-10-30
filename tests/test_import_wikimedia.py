import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from import_wikimedia import (
    CATEGORIES,
    MAX_DOWNLOAD_BYTES,
    SourceRedirectHandler,
    checked_url,
    convert_asset,
    convert_image,
    discover_assets,
    download,
    import_collection,
    retry_delay,
)


def png_bytes(size=(30, 12), color=(40, 60, 80, 90)):
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


def page(page_id=10, data=None, mime="image/png"):
    data = data or png_bytes()
    return {
        "pageid": page_id,
        "title": f"File:Example {page_id}.png",
        "roots": [CATEGORIES[0], CATEGORIES[1]],
        "imageinfo": [
            {
                "url": f"https://upload.wikimedia.org/original-{page_id}",
                "descriptionurl": f"https://commons.wikimedia.org/wiki/File:Example_{page_id}.png",
                "mime": mime,
                "width": 30,
                "height": 12,
                "sha1": hashlib.sha1(data).hexdigest(),
                "timestamp": "2026-10-30T12:00:00Z",
                "extmetadata": {
                    "Artist": {"value": "Example artist"},
                    "Credit": {"value": "Own work"},
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "LicenseUrl": {
                        "value": "https://creativecommons.org/licenses/by-sa/4.0"
                    },
                    "AttributionRequired": {"value": "true"},
                },
            }
        ],
    }


class DiscoveryTests(unittest.TestCase):
    def test_pagination_cycles_and_category_overlap_keep_unique_page_ids(self):
        calls = []

        def api(params, fetch):
            calls.append(params)
            if "prop" in params:
                return {"query": {"pages": [page()]}}
            category = params["cmtitle"]
            if category == CATEGORIES[0] and "cmcontinue" not in params:
                members = [{"ns": 14, "title": "Category:Child"}]
                return {
                    "query": {"categorymembers": members},
                    "continue": {"cmcontinue": "next", "continue": "-||"},
                }
            if category in (CATEGORIES[0], CATEGORIES[1], "Category:Child"):
                members = [
                    {"ns": 6, "title": "File:Example 10.png", "pageid": 10},
                    {"ns": 14, "title": CATEGORIES[0]},
                ]
            else:
                members = []
            return {"query": {"categorymembers": members}}

        with patch("import_wikimedia.api_query", side_effect=api):
            metadata = discover_assets()
        self.assertEqual(list(metadata), ["10"])
        self.assertEqual(metadata["10"]["roots"], CATEGORIES[:2])
        self.assertTrue(any(call.get("cmcontinue") == "next" for call in calls))
        self.assertEqual(sum(call.get("prop") == "imageinfo" for call in calls), 1)

    def test_repeated_continuation_fails_instead_of_hanging(self):
        result = {
            "query": {"categorymembers": []},
            "continue": {"cmcontinue": "same"},
        }
        with patch("import_wikimedia.api_query", return_value=result):
            with self.assertRaisesRegex(ValueError, "Repeated"):
                discover_assets()


class ConversionTests(unittest.TestCase):
    def test_large_raster_uses_bounded_wikimedia_rendering(self):
        original = png_bytes((30, 12))
        info = page()["imageinfo"][0]
        info.update(
            width=30, height=12, thumburl="https://thumb.wikimedia.org/logo.png"
        )
        with patch.object(Image, "MAX_IMAGE_PIXELS", 200):
            converted, extension, rendered = convert_asset(
                info, original, lambda url: png_bytes((15, 6))
            )
            self.assertEqual(
                (converted[1], converted[2:4], extension, rendered),
                ("PNG", (15, 6), ".png", info["thumburl"]),
            )
            with self.assertRaisesRegex(ValueError, "proportions"):
                convert_asset(info, original, lambda url: png_bytes((10, 10)))
            info["width"] = 31
            with self.assertRaisesRegex(ValueError, "dimensions"):
                convert_asset(info, original, lambda url: png_bytes((15, 6)))

    def test_raster_never_enlarges_and_preserves_alpha_and_ratio(self):
        for size, expected in (((30, 12), (30, 12)), ((2048, 512), (1024, 256))):
            with self.subTest(size=size):
                png, source_format, width, height, alpha = convert_image(
                    png_bytes(size)
                )
                self.assertEqual(source_format, "PNG")
                self.assertEqual((width, height), expected)
                self.assertTrue(alpha)
                with Image.open(io.BytesIO(png)) as image:
                    self.assertEqual(image.getpixel((0, 0))[3], 90)

    def test_svg_original_is_parsed_and_only_png_rendering_is_published(self):
        original = b'<svg xmlns="http://www.w3.org/2000/svg" width="30" height="12"/>'
        info = page(mime="image/svg+xml")["imageinfo"][0]
        info["thumburl"] = "https://thumb.wikimedia.org/rendered.png"
        converted, extension, rendered = convert_asset(
            info, original, lambda url: png_bytes()
        )
        self.assertEqual(converted[1], "SVG")
        self.assertEqual(extension, ".svg")
        self.assertEqual(rendered, info["thumburl"])
        with self.assertRaisesRegex(ValueError, "proportions"):
            convert_asset(info, original, lambda url: png_bytes((100, 100)))
        with self.assertRaisesRegex(ValueError, "SVG root"):
            convert_asset(info, b"<html/>", lambda url: png_bytes())
        with self.assertRaisesRegex(ValueError, "entity"):
            convert_asset(info, b'<!ENTITY x "bad"><svg/>', lambda url: png_bytes())

    def test_mime_disagreement_and_opaque_alpha(self):
        info = page()["imageinfo"][0]
        info["mime"] = "image/jpeg"
        with self.assertRaisesRegex(ValueError, "MIME"):
            convert_asset(info, png_bytes(), lambda url: self.fail("No second fetch"))
        self.assertFalse(convert_image(png_bytes(color=(1, 2, 3, 255)))[4])


class ImportTests(unittest.TestCase):
    def selection(self, root, logo="logosChannels/example.png"):
        path = root / "sources/wikimedia/selections.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "page_id": 10,
                            "id": "tv.ae.example",
                            "name": "Example",
                            "type": "tv",
                            "country": "AE",
                            "aliases": [],
                            "logo": logo,
                        }
                    ]
                }
            )
        )

    def test_reviewed_selection_updates_legacy_path_and_preserves_its_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "logosChannels/example.png"
            target.parent.mkdir()
            target.write_bytes(b"previous original logo")
            self.selection(root)
            manifest = import_collection(root, {"10": page()}, lambda url: png_bytes())
            self.assertEqual(
                target.read_bytes(), (root / manifest["assets"][0]["logo"]).read_bytes()
            )
            record = json.loads((root / "sources/wikimedia/upserts.json").read_text())[
                "upserts"
            ][0]
            self.assertEqual(
                (root / record["previous_logo"]).read_bytes(), b"previous original logo"
            )

    def test_invalid_selection_prevents_asset_and_legacy_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.selection(root, "../escape.png")
            before = {
                p.relative_to(root): p.read_bytes()
                for p in root.rglob("*")
                if p.is_file()
            }
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                import_collection(root, {"10": page()}, lambda url: png_bytes())
            after = {
                p.relative_to(root): p.read_bytes()
                for p in root.rglob("*")
                if p.is_file()
            }
            self.assertEqual(before, after)

    def test_upsert_idempotence_revision_and_provenance(self):
        original = png_bytes()
        metadata = {"10": page(data=original)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = import_collection(root, metadata, lambda url: original)
            asset = manifest["assets"][0]
            self.assertEqual(asset["page_id"], 10)
            self.assertEqual((root / asset["original"]).read_bytes(), original)
            self.assertEqual(asset["artist"], "Example artist")
            self.assertEqual(asset["credit"], "Own work")
            self.assertTrue(asset["attribution_required"])
            before = {
                p.relative_to(root): p.read_bytes()
                for p in root.rglob("*")
                if p.is_file()
            }
            import_collection(
                root,
                metadata,
                lambda url: self.fail("Unchanged asset should use cache"),
            )
            after = {
                p.relative_to(root): p.read_bytes()
                for p in root.rglob("*")
                if p.is_file()
            }
            self.assertEqual(before, after)
            revised = png_bytes(color=(1, 2, 3, 40))
            metadata["10"]["imageinfo"][0]["timestamp"] = "2026-10-31T12:00:00Z"
            metadata["10"]["imageinfo"][0]["sha1"] = hashlib.sha1(revised).hexdigest()
            new_asset = import_collection(root, metadata, lambda url: revised)[
                "assets"
            ][0]
            self.assertEqual(asset["logo"], new_asset["logo"])
            self.assertNotEqual(asset["original"], new_asset["original"])
            self.assertTrue((root / asset["original"]).is_file())

    def test_invalid_image_or_http_failure_leaves_all_existing_files_unchanged(self):
        for kind in ("invalid", "http"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "logosChannels/commons-10.png"
                target.parent.mkdir()
                target.write_bytes(b"previous logo")

                def fetch(url):
                    if url.endswith("10"):
                        return png_bytes()
                    if kind == "invalid":
                        return b"broken image"
                    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

                with self.assertRaises((ValueError, OSError)):
                    import_collection(root, {"10": page(), "20": page(20)}, fetch)
                self.assertEqual(target.read_bytes(), b"previous logo")
                self.assertFalse((root / "sources").exists())

    def test_reviewed_exclusions_cover_non_logos_without_downloading(self):
        exclusion = {
            "page_id": 10,
            "title": page()["title"],
            "reason": "Portrait, not a logo",
        }
        with tempfile.TemporaryDirectory() as directory:
            manifest = import_collection(
                directory,
                {"10": page()},
                lambda url: self.fail("Excluded"),
                [exclusion],
            )
            self.assertEqual(manifest["assets"], [])
            inventory = json.loads(
                (Path(directory) / "sources/wikimedia/inventory.json").read_text()
            )
            self.assertEqual(inventory["expected_count"], 1)
            self.assertEqual(inventory["excluded"], [exclusion])
        exclusion["title"] = "Wrong page title"
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValueError, "exclusion"
        ):
            import_collection(directory, {"10": page()}, exclusions=[exclusion])

    def test_source_removed_upstream_keeps_existing_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            import_collection(
                directory, {"10": page(), "20": page(20)}, lambda url: png_bytes()
            )
            manifest = import_collection(
                directory, {"10": page()}, lambda url: png_bytes()
            )
            self.assertEqual(
                [asset["page_id"] for asset in manifest["assets"]], [10, 20]
            )
            inventory = json.loads(
                (Path(directory) / "sources/wikimedia/inventory.json").read_text()
            )
            self.assertEqual(inventory["expected_count"], 2)
            self.assertEqual(inventory["discovered_count"], 1)
            self.assertTrue(inventory["files"][1]["retained"])

    def test_excluded_source_removed_upstream_remains_explicitly_retained(self):
        exclusion = {
            "page_id": 20,
            "title": page(20)["title"],
            "reason": "Portrait, not a logo",
            "description_url": page(20)["imageinfo"][0]["descriptionurl"],
        }
        with tempfile.TemporaryDirectory() as directory:
            import_collection(
                directory,
                {"10": page(), "20": page(20)},
                lambda url: png_bytes(),
                [exclusion],
            )
            import_collection(
                directory,
                {"10": page()},
                lambda url: self.fail("Use verified cache"),
                [exclusion],
            )
            inventory = json.loads(
                (Path(directory) / "sources/wikimedia/inventory.json").read_text()
            )
            self.assertEqual(inventory["expected_count"], 2)
            self.assertEqual(inventory["discovered_count"], 1)
            self.assertEqual(inventory["excluded"], [exclusion])
            self.assertTrue(inventory["files"][1]["retained"])
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValueError, "absent"
        ):
            import_collection(directory, {"10": page()}, exclusions=[exclusion])

    def test_source_hash_disagreement_blocks_publication(self):
        metadata = {"10": page()}
        metadata["10"]["imageinfo"][0]["sha1"] = "0" * 40
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "SHA-1"):
                import_collection(directory, metadata, lambda url: png_bytes())
            self.assertEqual(list(Path(directory).iterdir()), [])


class NetworkTests(unittest.TestCase):
    def test_urls_and_redirects_must_stay_within_https_wikimedia_allowlist(self):
        for url in (
            "http://commons.wikimedia.org/a",
            "https://evil.example/a",
            "https://user@upload.wikimedia.org/a",
            "https://upload.wikimedia.org:8443/a",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                checked_url(url)
        with self.assertRaises(ValueError):
            SourceRedirectHandler().redirect_request(
                None, None, 302, "", {}, "https://evil.example"
            )

    def test_download_bound_and_429_retry_after_is_capped(self):
        class Response:
            url = "https://upload.wikimedia.org/a"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return b"x" * limit

        with patch("import_wikimedia.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = Response()
            with self.assertRaisesRegex(ValueError, "exceeds"):
                download(Response.url)
        failure = urllib.error.HTTPError(
            Response.url, 429, "Rate limited", {"Retry-After": "900"}, None
        )
        with patch("import_wikimedia.urllib.request.build_opener") as opener, patch(
            "import_wikimedia.time.sleep"
        ) as sleep:
            opener.return_value.open.side_effect = [failure, Response()]
            with self.assertRaisesRegex(ValueError, "exceeds"):
                download(Response.url)
            sleep.assert_called_once_with(60)
        self.assertEqual(retry_delay("bad", 2), 4)


if __name__ == "__main__":
    unittest.main()
