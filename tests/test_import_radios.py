import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from import_radios import (
    MAX_DOWNLOAD_BYTES,
    SOURCE_URL,
    SourceRedirectHandler,
    checked_url,
    convert_image,
    download,
    import_collection,
    original_url,
    parse_stations,
)


def image_bytes(mode="RGBA", size=(32, 17)):
    image = Image.new(mode, size, (20, 40, 60, 77) if mode == "RGBA" else (20, 40, 60))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def station_html(slug="alpha", name="Alpha FM", lazy=True):
    url = f"{SOURCE_URL}sites/default/files/styles/medium_2/public/radio/{slug}.png?itok=fixture"
    attrs = f'data-src="{url}" src="/assets/90x90.png"' if lazy else f'src="{url}"'
    return f'<a href="/{slug}"><img {attrs} alt="{name}"></a>'


class ParserTests(unittest.TestCase):
    def test_lazy_images_and_page_deduplication_preserve_separate_webradios(self):
        html = (
            station_html()
            + station_html(lazy=False)
            + station_html("alpha-dj", "Alpha DJ")
        )
        html += '<a href="/placeholder"><img src="/assets/90x90.png"></a>'
        stations = parse_stations(html)
        self.assertEqual(
            [station["name"] for station in stations], ["Alpha FM", "Alpha DJ"]
        )
        self.assertNotIn("90x90.png", stations[0]["thumbnail_url"])
        self.assertEqual(
            original_url(stations[0]["thumbnail_url"]),
            f"{SOURCE_URL}sites/default/files/radio/alpha.png",
        )

    def test_external_image_source_is_rejected(self):
        html = station_html().replace(
            "https://www.tunisie-radio.com/sites/", "https://other.example/sites/"
        )
        with self.assertRaisesRegex(ValueError, "Untrusted"):
            parse_stations(html)

    def test_only_https_source_urls_and_redirects_are_accepted(self):
        for url in [
            "http://www.tunisie-radio.com/a",
            "https://evil.example/a",
            "https://name@www.tunisie-radio.com/a",
            "https://www.tunisie-radio.com:8443/a",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                checked_url(url)
        with self.assertRaises(ValueError):
            SourceRedirectHandler().redirect_request(
                None, None, 302, "", {}, "https://evil.example/a"
            )


class ImportTests(unittest.TestCase):
    def test_source_preservation_ratio_alpha_and_stable_identity(self):
        source = image_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = import_collection(root, station_html(), 1, lambda url: source)
            entry = manifest["stations"][0]
            self.assertIsNone(entry["country"])
            self.assertEqual(entry["type"], "radio")
            self.assertEqual(
                entry["original_sha256"], hashlib.sha256(source).hexdigest()
            )
            self.assertEqual((root / entry["original"]).read_bytes(), source)
            self.assertEqual(
                entry["logo_sha256"],
                hashlib.sha256((root / entry["logo"]).read_bytes()).hexdigest(),
            )
            with Image.open(root / entry["logo"]) as image:
                self.assertEqual(image.size, (32, 17))
                self.assertEqual(image.getpixel((0, 0)), (20, 40, 60, 77))
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            import_collection(root, station_html(), 1, lambda url: source)
            self.assertEqual(
                before,
                {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                },
            )
            manifest_path = root / "sources/radios/manifest.json"
            entry["id"] = "radio.tn.persistent-identity"
            manifest_path.write_text(json.dumps(manifest))
            refreshed = import_collection(
                root,
                station_html(name="Renamed FM"),
                1,
                lambda url: image_bytes(size=(50, 20)),
            )
            self.assertEqual(
                refreshed["stations"][0]["id"], "radio.tn.persistent-identity"
            )
            self.assertEqual(refreshed["stations"][0]["name"], "Renamed FM")

    def test_different_stations_sharing_image_keep_separate_ids(self):
        with tempfile.TemporaryDirectory() as root:
            manifest = import_collection(
                root,
                station_html() + station_html("alpha-dj", "Alpha DJ"),
                2,
                lambda url: image_bytes(),
            )
            entries = manifest["stations"]
            self.assertNotEqual(entries[0]["id"], entries[1]["id"])
            self.assertEqual(
                entries[0]["original_sha256"], entries[1]["original_sha256"]
            )

    def test_original_http_error_falls_back_with_resolution_and_reason(self):
        def fetch(url):
            if "/styles/" not in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return image_bytes(size=(90, 90))

        with tempfile.TemporaryDirectory() as root:
            entry = import_collection(root, station_html(), 1, fetch)["stations"][0]
            self.assertTrue(entry["fallback"])
            self.assertIn("404", entry["fallback_reason"])
            self.assertEqual((entry["width"], entry["height"]), (90, 90))
            self.assertEqual(entry["image_url"], entry["thumbnail_url"])

    def test_invalid_image_or_http_failure_publishes_nothing(self):
        for error_kind in ("image", "http"):

            def fetch(url):
                if "alpha.png" in url:
                    return image_bytes()
                if error_kind == "http":
                    raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)
                return b"invalid image"

            with self.subTest(
                error_kind=error_kind
            ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                existing = root / "logosChannels/radio-tn-alpha.png"
                existing.parent.mkdir()
                existing.write_bytes(b"existing published image")
                with self.assertRaisesRegex(ValueError, "No valid image"):
                    import_collection(
                        root, station_html() + station_html("broken"), 2, fetch
                    )
                self.assertEqual(existing.read_bytes(), b"existing published image")
                self.assertFalse((root / "sources").exists())

    def test_count_change_blocks_import_before_downloading_images(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "Expected 80 stations, found 1"):
                import_collection(
                    root,
                    station_html(),
                    fetch=lambda url: self.fail("No images should download"),
                )
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_animated_source_is_published_as_static_first_frame(self):
        first = Image.new("RGB", (12, 9), "red")
        second = Image.new("RGB", (12, 9), "blue")
        output = io.BytesIO()
        first.save(
            output, format="GIF", save_all=True, append_images=[second], duration=100
        )
        png, source_format, width, height, alpha = convert_image(output.getvalue())
        self.assertEqual((source_format, width, height), ("GIF", 12, 9))
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.n_frames, 1)
            self.assertEqual(image.convert("RGB").getpixel((0, 0)), (255, 0, 0))

    def test_fully_opaque_alpha_channel_is_not_reported_as_transparency(self):
        image = Image.new("RGBA", (9, 6), (20, 40, 60, 255))
        data = io.BytesIO()
        image.save(data, format="PNG")
        _, _, _, _, transparency = convert_image(data.getvalue())
        self.assertFalse(transparency)

    def test_slug_collision_with_existing_distinct_ids_blocks_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "sources/radios/manifest.json"
            manifest_path.parent.mkdir(parents=True)
            previous = {
                "stations": [
                    {"page_url": SOURCE_URL + "alpha", "id": "radio.tn.alpha"},
                    {"page_url": SOURCE_URL + "álpha", "id": "radio.tn.other-alpha"},
                ]
            }
            original_manifest = json.dumps(previous)
            manifest_path.write_text(original_manifest)
            with self.assertRaisesRegex(ValueError, "Duplicate published logo"):
                import_collection(
                    root,
                    station_html() + station_html("álpha"),
                    2,
                    lambda url: image_bytes(),
                )
            self.assertEqual(manifest_path.read_text(), original_manifest)
            self.assertFalse((root / "logosChannels").exists())

    def test_download_size_is_bounded(self):
        class Response:
            url = SOURCE_URL

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                self.limit = limit
                return b"x" * limit

        response = Response()
        with patch("import_radios.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(ValueError, "exceeds"):
                download(SOURCE_URL)
            self.assertEqual(response.limit, MAX_DOWNLOAD_BYTES + 1)


if __name__ == "__main__":
    unittest.main()
