import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import fetch_wikimedia_logo as logo

PAGE = "https://commons.wikimedia.org/wiki/File:Example.svg"
ASSET = "https://upload.wikimedia.org/example.png"


def image_bytes(size=(30, 12), format="PNG"):
    output = io.BytesIO()
    Image.new("RGBA", size, (40, 60, 80, 90)).save(output, format=format)
    return output.getvalue()


def metadata(mime="image/png", width=30, height=12, **extra):
    return {
        "query": {
            "pages": [
                {
                    "imageinfo": [
                        {
                            "mime": mime,
                            "width": width,
                            "height": height,
                            "url": ASSET,
                            "thumburl": "https://thumb.wikimedia.org/render.png",
                            **extra,
                        }
                    ]
                }
            ]
        }
    }


def mocked_fetch(info=None, data=None):
    info = metadata() if info is None else info
    data = image_bytes() if data is None else data
    return Mock(side_effect=[json.dumps(info).encode(), data])


class FetchLogoTests(unittest.TestCase):
    def test_raster_keeps_size_alpha_and_writes_only_requested_png(self):
        fetch = mocked_fetch()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "logo.png"
            self.assertEqual(logo.fetch_logo(PAGE, output, fetch), (30, 12))
            self.assertEqual(list(Path(directory).iterdir()), [output])
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (30, 12))
                self.assertEqual(image.getpixel((0, 0)), (40, 60, 80, 90))
        self.assertEqual(fetch.call_args_list[1].args, (ASSET,))

    def test_large_raster_is_bounded_without_changing_proportions(self):
        fetch = mocked_fetch(
            metadata(width=2048, height=1024), image_bytes((2048, 1024))
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "logo.png"
            self.assertEqual(logo.fetch_logo(PAGE, output, fetch), (1024, 512))

    def test_french_svg_uses_server_png_and_no_original(self):
        fetch = mocked_fetch(
            metadata("image/svg+xml", width=120, height=300), image_bytes((410, 1024))
        )
        page = "https://fr.wikipedia.org/wiki/Fichier:Un_logo.svg"
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                logo.fetch_logo(page, Path(directory) / "logo.png", fetch), (410, 1024)
            )
        api_url = fetch.call_args_list[0].args[0]
        self.assertEqual(urllib.parse.urlsplit(api_url).hostname, "fr.wikipedia.org")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(api_url).query)
        self.assertEqual(query["titles"], ["File:Un_logo.svg"])
        self.assertEqual(query["iiurlheight"], ["1024"])
        self.assertEqual(query["iiurlwidth"], ["1024"])
        self.assertNotIn("extmetadata", query["iiprop"][0])
        self.assertEqual(
            fetch.call_args_list[1].args, ("https://thumb.wikimedia.org/render.png",)
        )

    def test_existing_output_and_wrong_extension_fail_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "logo.png"
            output.write_bytes(b"existing")
            fetch = Mock()
            with self.assertRaises(FileExistsError):
                logo.fetch_logo(PAGE, output, fetch)
            with self.assertRaises(ValueError):
                logo.fetch_logo(PAGE, output.with_suffix(".svg"), fetch)
            fetch.assert_not_called()
            self.assertEqual(output.read_bytes(), b"existing")

    def test_large_raster_uses_bounded_server_jpeg(self):
        data = io.BytesIO()
        Image.new("RGB", (30, 12), "red").save(data, format="JPEG")
        fetch = mocked_fetch(
            metadata("image/jpeg", width=300, height=120), data.getvalue()
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            logo, "MAX_IMAGE_PIXELS", 1000
        ):
            self.assertEqual(
                logo.fetch_logo(PAGE, Path(directory) / "logo.png", fetch), (30, 12)
            )
        self.assertEqual(
            fetch.call_args_list[1].args, ("https://thumb.wikimedia.org/render.png",)
        )

    def test_render_above_pixel_limit_leaves_no_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            logo, "MAX_IMAGE_PIXELS", 100
        ):
            with self.assertRaisesRegex(ValueError, "pixel limit"):
                logo.fetch_logo(PAGE, Path(directory) / "logo.png", mocked_fetch())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_decompression_warning_is_raised_when_opening_image(self):
        fetch = mocked_fetch()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            Image, "MAX_IMAGE_PIXELS", 200
        ):
            with self.assertRaises(Image.DecompressionBombWarning):
                logo.fetch_logo(PAGE, Path(directory) / "logo.png", fetch)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_bad_data_metadata_or_render_does_not_leave_files(self):
        cases = [
            ({"query": {"pages": [{}]}}, image_bytes()),
            ({"error": {"code": "bad"}}, image_bytes()),
            (metadata(), b"not an image"),
            (metadata(width=0), image_bytes()),
            (metadata("text/html"), image_bytes()),
            (metadata(width=31), image_bytes()),
            (metadata("image/svg+xml"), image_bytes((10, 10))),
            (metadata(url="https://evil.example/logo.png"), image_bytes()),
            (metadata("image/jpeg"), image_bytes()),
        ]
        for info, data in cases:
            with self.subTest(info=info), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises((ValueError, OSError)):
                    logo.fetch_logo(
                        PAGE, Path(directory) / "logo.png", mocked_fetch(info, data)
                    )
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_animated_input_is_rejected(self):
        data = io.BytesIO()
        Image.new("RGBA", (30, 12), "red").save(
            data,
            format="GIF",
            save_all=True,
            append_images=[Image.new("RGBA", (30, 12), "blue")],
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Animated"):
                logo.fetch_logo(
                    PAGE,
                    Path(directory) / "logo.png",
                    mocked_fetch(metadata("image/gif"), data.getvalue()),
                )

    def test_concurrent_output_is_not_overwritten_and_temp_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "logo.png"

            def concurrent_link(source, target):
                target.write_bytes(b"other writer")
                raise FileExistsError()

            with patch.object(logo.os, "link", side_effect=concurrent_link):
                with self.assertRaises(FileExistsError):
                    logo.fetch_logo(PAGE, output, mocked_fetch())
            self.assertEqual(output.read_bytes(), b"other writer")
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_requires_file_page_and_rejects_multiple_titles(self):
        for page in (
            "https://fr.wikipedia.org/wiki/France_2",
            "https://commons.wikimedia.org/wiki/File:A.png%7CFile:B.png",
            "https://commons.wikimedia.org/wiki/File:A.png?oldid=123",
            ASSET,
        ):
            with self.subTest(page=page), self.assertRaises(ValueError):
                logo.resolve_image(page, Mock())


class DownloadTests(unittest.TestCase):
    def test_invalid_url_error_does_not_expose_credentials(self):
        with self.assertRaises(ValueError) as error:
            logo.checked_url("https://user:secret@upload.wikimedia.org/a.png")
        self.assertNotIn("secret", str(error.exception))

    def test_url_and_redirect_validation(self):
        for url in (
            "http://upload.wikimedia.org/a.png",
            "https://upload.wikimedia.org.evil.example/a.png",
            "https://evil.example/a.png",
            "https://user@upload.wikimedia.org/a.png",
            "https://upload.wikimedia.org:444/a.png",
            "https://upload.wikimedia.org/a\n.png",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    logo.checked_url(url)
                with self.assertRaises(ValueError):
                    logo.SourceRedirectHandler().redirect_request(
                        None, None, 302, "Found", {}, url
                    )

    def test_429_retries_are_bounded_and_delay_capped(self):
        opener = Mock()
        opener.open.side_effect = [
            urllib.error.HTTPError(ASSET, 429, "limit", {"Retry-After": "9999"}, None)
            for _ in range(logo.MAX_RETRIES + 1)
        ]
        with patch.object(
            logo.urllib.request, "build_opener", return_value=opener
        ), patch.object(logo.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                logo.download(ASSET)
        self.assertEqual(opener.open.call_count, logo.MAX_RETRIES + 1)
        self.assertEqual(sleep.call_count, logo.MAX_RETRIES)
        self.assertTrue(all(call.args == (60,) for call in sleep.call_args_list))
        self.assertTrue(
            all(call.kwargs == {"timeout": 40} for call in opener.open.call_args_list)
        )

    def test_download_size_and_final_url_are_checked(self):
        for final_url, data in ((ASSET, b"12345"), ("https://evil.example/a", b"ok")):
            with self.subTest(url=final_url):
                response = Mock(url=final_url)
                response.read.return_value = data
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                opener = Mock()
                opener.open.return_value = response
                with patch.object(
                    logo.urllib.request, "build_opener", return_value=opener
                ), patch.object(logo, "MAX_DOWNLOAD_BYTES", 4):
                    with self.assertRaises(ValueError):
                        logo.download(ASSET)

    def test_non_retryable_http_error_is_immediate(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(
            ASSET, 403, "Forbidden", {}, None
        )
        with patch.object(
            logo.urllib.request, "build_opener", return_value=opener
        ), patch.object(logo.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                logo.download(ASSET)
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
