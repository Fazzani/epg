#!/usr/bin/env python3
"""Import the Tunisie Radio homepage collection without changing source images."""

import argparse
import hashlib
import io
import json
import re
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import warnings
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image, ImageOps

SOURCE_URL = "https://www.tunisie-radio.com/"
EXPECTED_COUNT = 80
MAX_DOWNLOAD_BYTES = 12 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 16_000_000
ALLOWED_HOSTS = {"www.tunisie-radio.com", "tunisie-radio.com"}
FORMATS = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp"}


def checked_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError(f"Untrusted source URL: {url}")
    return url


class SourceRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url):
    checked_url(url)
    opener = urllib.request.build_opener(SourceRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "epg-logo-import/1"})
    with opener.open(request, timeout=30) as response:
        checked_url(response.url)
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"Download exceeds {MAX_DOWNLOAD_BYTES} bytes: {url}")
    return data


class StationParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchor = None
        self.stations = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.anchor = attrs.get("href")
        if tag != "img" or not self.anchor:
            return
        image = attrs.get("data-src") or attrs.get("src", "")
        if "/styles/medium_2/public/radio/" not in image:
            return
        image = checked_url(urllib.parse.urljoin(SOURCE_URL, image))
        page = checked_url(urllib.parse.urljoin(SOURCE_URL, self.anchor))
        page = urllib.parse.urlunsplit(
            urllib.parse.urlsplit(page)._replace(query="", fragment="")
        )
        name = (attrs.get("alt") or attrs.get("title") or "").strip()
        if not name:
            raise ValueError(f"Station has no source name: {page}")
        self.stations.setdefault(
            page, {"name": name, "page_url": page, "thumbnail_url": image}
        )

    def handle_endtag(self, tag):
        if tag == "a":
            self.anchor = None


def parse_stations(html):
    parser = StationParser()
    parser.feed(html)
    return sorted(parser.stations.values(), key=lambda station: station["page_url"])


def original_url(thumbnail):
    parsed = urllib.parse.urlsplit(checked_url(thumbnail))
    path = parsed.path.replace("/styles/medium_2/public/", "/", 1)
    return urllib.parse.urlunsplit(parsed._replace(path=path, query="", fragment=""))


def make_slug(page_url):
    name = urllib.parse.unquote(urllib.parse.urlsplit(page_url).path).strip("/")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"Cannot derive a stable initial identifier: {page_url}")
    return slug


def convert_image(data):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            if image_format not in FORMATS:
                raise ValueError(f"Unsupported source image format: {image_format}")
            # Load every frame to reject incomplete animations before publishing frame one.
            for frame in range(getattr(image, "n_frames", 1)):
                image.seek(frame)
                image.load()
            image.seek(0)
            frame = ImageOps.exif_transpose(image)
            has_alpha = "A" in frame.getbands() or "transparency" in frame.info
            frame = frame.convert("RGBA" if has_alpha else "RGB")
            width, height = frame.size
            if width < 1 or height < 1:
                raise ValueError("Image has invalid dimensions")
            output = io.BytesIO()
            frame.save(output, format="PNG")
            transparent = has_alpha and frame.getchannel("A").getextrema()[0] < 255
            return output.getvalue(), image_format, width, height, transparent


def fetch_image(station, fetch):
    original = original_url(station["thumbnail_url"])
    try:
        data = fetch(original)
        converted = convert_image(data)
        return data, converted, original, False, None
    except (OSError, ValueError, urllib.error.URLError) as error:
        reason = str(error)
    try:
        thumbnail = station["thumbnail_url"]
        data = fetch(thumbnail)
        converted = convert_image(data)
        return data, converted, thumbnail, True, reason
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise ValueError(
            f"No valid image for {station['page_url']}: {error}"
        ) from error


def import_collection(root, html=None, expected_count=EXPECTED_COUNT, fetch=download):
    root = Path(root)
    if expected_count < 1:
        raise ValueError("Expected count must be positive")
    if html is None:
        html = fetch(SOURCE_URL).decode("utf-8")
    stations = parse_stations(html)
    if len(stations) != expected_count:
        raise ValueError(
            f"Expected {expected_count} stations, found {len(stations)}; nothing published"
        )
    manifest_path = root / "sources/radios/manifest.json"
    previous = {}
    if manifest_path.exists():
        previous = {
            item["page_url"]: item
            for item in json.loads(manifest_path.read_text())["stations"]
        }
    entries = []
    ids = set()
    files = {}
    for station in stations:
        prior = previous.get(station["page_url"], {})
        slug = make_slug(station["page_url"])
        identifier = prior.get("id", f"radio.tn.{slug}")
        if not re.fullmatch(r"radio\.[a-z]{2}\.[a-z0-9]+(?:-[a-z0-9]+)*", identifier):
            raise ValueError(f"Invalid existing identifier: {identifier}")
        if identifier in ids:
            raise ValueError(f"Duplicate radio identifier: {identifier}")
        ids.add(identifier)
        logo = f"logosChannels/radio-tn-{slug}.png"
        if logo in files:
            raise ValueError(f"Duplicate published logo: {logo}")
        data, converted, image_url, fallback, reason = fetch_image(station, fetch)
        png, image_format, width, height, alpha = converted
        digest = hashlib.sha256(data).hexdigest()
        original = (
            f"sources/radios/originals/{slug}-{digest[:16]}{FORMATS[image_format]}"
        )
        files[original] = data
        files[logo] = png
        entry = {
            "id": identifier,
            "name": station["name"],
            "type": "radio",
            "country": prior.get("country"),
            "aliases": prior.get("aliases", []),
            "logo": logo,
            "page_url": station["page_url"],
            "image_url": image_url,
            "thumbnail_url": station["thumbnail_url"],
            "original": original,
            "original_sha256": digest,
            "logo_sha256": hashlib.sha256(png).hexdigest(),
            "width": width,
            "height": height,
            "format": image_format,
            "transparency": alpha,
            "fallback": fallback,
        }
        if fallback:
            entry["fallback_reason"] = reason
        entries.append(entry)
    manifest = {
        "source_url": SOURCE_URL,
        "expected_count": expected_count,
        "stations": sorted(entries, key=lambda entry: entry["id"]),
    }
    snapshot = "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
    files["sources/radios/homepage.html"] = snapshot.encode("utf-8")
    files["sources/radios/manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    # All network and decoding checks complete before any published file changes.
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".radio-import-", dir=root) as staging:
        staging = Path(staging)
        for relative, content in files.items():
            staged = staging / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)
        for relative in files:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            (staging / relative).replace(target)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-count", type=int, default=EXPECTED_COUNT)
    parser.add_argument(
        "--html-file",
        type=Path,
        help="Use a saved homepage; image downloads remain online",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent
    )
    args = parser.parse_args()
    html = args.html_file.read_text(encoding="utf-8") if args.html_file else None
    try:
        manifest = import_collection(args.output_dir, html, args.expected_count)
    except (OSError, ValueError, urllib.error.URLError) as error:
        parser.exit(1, f"Radio import failed: {error}\n")
    fallbacks = [entry for entry in manifest["stations"] if entry["fallback"]]
    print(
        f"Imported {len(manifest['stations'])} stations; {len(fallbacks)} thumbnail fallbacks"
    )
    for entry in fallbacks:
        print(
            f"  {entry['id']}: thumbnail {entry['width']}x{entry['height']} ({entry['fallback_reason']})"
        )


if __name__ == "__main__":
    main()
