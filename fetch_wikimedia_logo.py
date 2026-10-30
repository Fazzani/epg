#!/usr/bin/env python3
"""Fetch one explicit Commons/FR file page as PNG, without metadata sidecars."""

import argparse
import io
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from PIL import Image, ImageOps

MAX_DOWNLOAD_BYTES = 24 * 1024 * 1024
MAX_RETRIES = 3
MAX_SIDE = 1024
MAX_IMAGE_PIXELS = 40_000_000
USER_AGENT = "EPGLogoCatalog/1.0 (https://github.com/Fazzani/epg)"
PAGE_HOSTS = {"commons.wikimedia.org", "fr.wikipedia.org"}
ALLOWED_HOSTS = PAGE_HOSTS | {"upload.wikimedia.org", "thumb.wikimedia.org"}
RASTER_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def checked_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or any(ord(char) < 33 for char in url)
    ):
        raise ValueError("Untrusted Wikimedia URL")
    return url


class SourceRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def retry_delay(value, attempt):
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            delay = (
                parsedate_to_datetime(value) - datetime.now(timezone.utc)
            ).total_seconds()
        except (ValueError, TypeError, OverflowError):
            delay = 2**attempt
    return min(60, max(0, delay))


def download(url):
    checked_url(url)
    opener = urllib.request.build_opener(SourceRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(MAX_RETRIES + 1):
        try:
            with opener.open(request, timeout=40) as response:
                checked_url(response.url)
                data = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise ValueError("Wikimedia download exceeds size limit")
            return data
        except urllib.error.HTTPError as error:
            if error.code not in (429, 503) or attempt == MAX_RETRIES:
                raise
            delay = retry_delay(error.headers.get("Retry-After"), attempt)
            error.close()
            time.sleep(delay)


def resolve_image(page_url, fetch=download):
    parsed = urllib.parse.urlsplit(checked_url(page_url))
    title = urllib.parse.unquote(parsed.path.removeprefix("/wiki/"))
    namespace, separator, filename = title.partition(":")
    if (
        parsed.hostname not in PAGE_HOSTS
        or not parsed.path.startswith("/wiki/")
        or namespace not in {"File", "Fichier"}
        or not separator
        or not filename
        or "|" in title
        or parsed.query
    ):
        raise ValueError("Expected an explicit Commons or French Wikipedia file page")
    params = {
        "action": "query",
        "format": "json",
        "formatversion": 2,
        "prop": "imageinfo",
        "titles": "File:" + filename,
        "redirects": 1,
        "iiprop": "url|size|mime",
        "iiurlwidth": MAX_SIDE,
        "iiurlheight": MAX_SIDE,
    }
    api = f"https://{parsed.hostname}/w/api.php?"
    result = json.loads(fetch(api + urllib.parse.urlencode(params)))
    pages = result.get("query", {}).get("pages", [])
    if "error" in result or "warnings" in result or len(pages) != 1:
        raise ValueError("Wikimedia did not return one file")
    info = pages[0].get("imageinfo", [])
    if len(info) != 1:
        raise ValueError("Wikimedia file is missing or unavailable")
    info = info[0]
    width, height = info.get("width"), info.get("height")
    if any(type(side) is not int or side < 1 for side in (width, height)):
        raise ValueError("Invalid Wikimedia image dimensions")
    mime = info.get("mime")
    if mime not in RASTER_MIMES | {"image/svg+xml"}:
        raise ValueError(f"Unsupported Wikimedia image MIME: {mime}")
    rendered = mime == "image/svg+xml" or width * height > MAX_IMAGE_PIXELS
    url = info.get("thumburl" if rendered else "url", "")
    return checked_url(url), info, rendered


def fetch_logo(page_url, output, fetch=download):
    """Write only a new PNG; originals and API metadata stay in memory."""
    output = Path(output)
    if output.suffix.lower() != ".png":
        raise ValueError("Output must have a .png extension")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output already exists: {output}")
    url, info, rendered = resolve_image(page_url, fetch)
    data = fetch(url)
    # Decode only bounded raster data; SVG is rendered by Wikimedia.
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("Downloaded image exceeds pixel limit")
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("Animated logos are not supported")
            if not rendered and source.size != (info["width"], info["height"]):
                raise ValueError("Raster dimensions disagree with Wikimedia metadata")
            expected = {
                "image/png": "PNG",
                "image/jpeg": "JPEG",
                "image/gif": "GIF",
                "image/webp": "WEBP",
            }
            if (
                (info["mime"] == "image/svg+xml" and source.format != "PNG")
                or (not rendered and source.format != expected[info["mime"]])
                or source.format not in expected.values()
            ):
                raise ValueError(
                    "Decoded image format disagrees with Wikimedia metadata"
                )
            if (
                rendered
                and info["mime"] != "image/svg+xml"
                and (source.width > info["width"] or source.height > info["height"])
            ):
                raise ValueError("Wikimedia rendering enlarged the raster")
            if rendered and (
                abs(source.width * info["height"] - source.height * info["width"])
                > max(info["width"], info["height"])
            ):
                raise ValueError("Wikimedia rendering changed image proportions")
            image = ImageOps.exif_transpose(source)
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            image = image.convert("RGBA" if has_alpha else "RGB")
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            width, height = image.size
            optimized = io.BytesIO()
            image.save(optimized, format="PNG", optimize=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(optimized.getvalue())
            temporary.close()
            # Atomic publication without replacing a concurrently created output.
            os.link(temporary_path, output)
        finally:
            temporary_path.unlink()
    return width, height


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page_url", help="HTTPS Commons/FR /wiki/File: or Fichier: URL")
    parser.add_argument("output", type=Path, help="New PNG path (never overwritten)")
    args = parser.parse_args()
    try:
        width, height = fetch_logo(args.page_url, args.output)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"{args.output}: {width}x{height} PNG")


if __name__ == "__main__":
    main()
