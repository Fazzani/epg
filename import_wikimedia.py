#!/usr/bin/env python3
"""Upsert Wikimedia Commons logo assets while preserving source files and attribution."""

import argparse
import hashlib
import io
import json
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageOps

from upsert_wikimedia import prepare_upserts

API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "EPGLogoCatalog/1.0 (https://github.com/Fazzani/epg)"
ALLOWED_HOSTS = {
    "commons.wikimedia.org",
    "upload.wikimedia.org",
    "thumb.wikimedia.org",
}
MAX_DOWNLOAD_BYTES = 24 * 1024 * 1024
MAX_SIDE = 1024
MAX_RETRIES = 3
Image.MAX_IMAGE_PIXELS = 40_000_000
FORMATS = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp"}
CATEGORIES = [
    "Category:Logos of television channels in the United Arab Emirates",
    "Category:SVG logos of television channels of the United Arab Emirates",
    "Category:Logos of television channels in Egypt",
    "Category:Logos of television channels in Saudi Arabia",
    "Category:Logos of television channels in Tunisia",
    "Category:SVG logos of DAZN",
]


def checked_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError(f"Untrusted Wikimedia URL: {url}")
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
                raise ValueError(f"Download exceeds {MAX_DOWNLOAD_BYTES} bytes: {url}")
            return data
        except urllib.error.HTTPError as error:
            if error.code not in (429, 503) or attempt == MAX_RETRIES:
                raise
            delay = retry_delay(error.headers.get("Retry-After"), attempt)
            error.close()
            time.sleep(delay)


def api_query(params, fetch=download):
    query = {"action": "query", "format": "json", "formatversion": 2, **params}
    result = json.loads(fetch(API_URL + "?" + urllib.parse.urlencode(query)))
    if "error" in result or "warnings" in result:
        raise ValueError(f"Wikimedia API did not complete cleanly: {result}")
    if "query" not in result:
        raise ValueError("Wikimedia API response has no query")
    return result


def discover_assets(fetch=download):
    """Traverse each root separately so shared descendants retain every root."""
    files = {}
    for root in CATEGORIES:
        pending, visited = [root], set()
        while pending:
            category = pending.pop()
            if category in visited:
                continue
            visited.add(category)
            continuation = {}
            seen_tokens = set()
            while True:
                result = api_query(
                    {
                        "list": "categorymembers",
                        "cmtitle": category,
                        "cmnamespace": "6|14",
                        "cmlimit": "max",
                        **continuation,
                    },
                    fetch,
                )
                for member in result["query"]["categorymembers"]:
                    if member["ns"] == 14:
                        pending.append(member["title"])
                    elif member["ns"] == 6:
                        page_id = member["pageid"]
                        entry = files.setdefault(
                            page_id,
                            {
                                "pageid": page_id,
                                "title": member["title"],
                                "roots": set(),
                                "member_categories": set(),
                            },
                        )
                        entry["roots"].add(root)
                        entry["member_categories"].add(category)
                continuation = result.get("continue")
                if not continuation:
                    break
                token = json.dumps(continuation, sort_keys=True)
                if token in seen_tokens:
                    raise ValueError("Repeated Wikimedia category continuation")
                seen_tokens.add(token)
    ids = sorted(files)
    for offset in range(0, len(ids), 50):
        result = api_query(
            {
                "prop": "imageinfo",
                "pageids": "|".join(map(str, ids[offset : offset + 50])),
                "iiprop": "url|size|mime|sha1|timestamp|extmetadata",
                "iiurlwidth": MAX_SIDE,
            },
            fetch,
        )
        for page in result["query"]["pages"]:
            if page.get("missing") or not page.get("imageinfo"):
                raise ValueError(f"Missing Wikimedia image metadata: {page}")
            if page["pageid"] not in files:
                raise ValueError("Wikimedia returned an unexpected page")
            files[page["pageid"]].update(page)
    for entry in files.values():
        entry["roots"] = sorted(entry["roots"])
        entry["member_categories"] = sorted(entry["member_categories"])
    return {str(key): value for key, value in sorted(files.items())}


def convert_image(data):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            source_format = image.format
            if source_format not in FORMATS:
                raise ValueError(f"Unsupported raster format: {source_format}")
            for frame in range(getattr(image, "n_frames", 1)):
                image.seek(frame)
                image.load()
            image.seek(0)
            frame = ImageOps.exif_transpose(image)
            has_alpha = "A" in frame.getbands() or "transparency" in frame.info
            frame = frame.convert("RGBA" if has_alpha else "RGB")
            frame.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            width, height = frame.size
            if min(width, height) < 1:
                raise ValueError("Invalid raster dimensions")
            transparency = has_alpha and frame.getchannel("A").getextrema()[0] < 255
            output = io.BytesIO()
            frame.save(output, format="PNG")
            return output.getvalue(), source_format, width, height, transparency


def convert_asset(info, original, fetch):
    mime = info["mime"]
    if mime != "image/svg+xml":
        converted = convert_image(original)
        expected = {
            "image/png": "PNG",
            "image/jpeg": "JPEG",
            "image/gif": "GIF",
            "image/webp": "WEBP",
        }.get(mime)
        if converted[1] != expected:
            raise ValueError(f"Source MIME disagrees with decoded format: {mime}")
        return converted, FORMATS[converted[1]], ""
    # Parse the preserved original, but never execute SVG or resolve its links locally.
    if b"<!ENTITY" in original.upper():
        raise ValueError("SVG entity declarations are not accepted")
    try:
        document = ElementTree.fromstring(original)
    except ElementTree.ParseError as error:
        raise ValueError(f"Invalid SVG XML: {error}") from error
    if document.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError("SVG original has no SVG root")
    thumbnail = checked_url(info.get("thumburl", ""))
    converted = convert_image(fetch(thumbnail))
    if converted[1] != "PNG":
        raise ValueError("Wikimedia SVG rendering is not a PNG")
    source_width, source_height = info["width"], info["height"]
    width, height = converted[2:4]
    if (
        min(source_width, source_height) < 1
        or abs(width / height - source_width / source_height) > 0.02
    ):
        raise ValueError("Wikimedia SVG rendering does not preserve proportions")
    return (converted[0], "SVG", width, height, converted[4]), ".svg", thumbnail


def metadata_value(info, key):
    return str(info.get("extmetadata", {}).get(key, {}).get("value", ""))


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def cached_asset(root, prior, info):
    if not prior or (
        prior.get("source_sha1") != info.get("sha1")
        or prior.get("source_timestamp") != info.get("timestamp")
        or prior.get("image_url") != info.get("url")
    ):
        return None
    for field in ("original", "logo"):
        expected_prefix = (
            "sources/wikimedia/originals/" if field == "original" else "logosChannels/"
        )
        relative = Path(prior[field])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe cached asset path")
        if not relative.as_posix().startswith(expected_prefix):
            raise ValueError("Unexpected cached asset path")
        path = root / relative
        if not path.is_file():
            return None
        if hashlib.sha256(path.read_bytes()).hexdigest() != prior[field + "_sha256"]:
            return None
    return prior


def import_collection(root, metadata=None, fetch=download, exclusions=None):
    root = Path(root)
    metadata = discover_assets(fetch) if metadata is None else metadata
    if not isinstance(metadata, dict) or not metadata:
        raise ValueError("Wikimedia inventory is empty or invalid")
    exclusions = exclusions or []
    excluded = {entry["page_id"]: entry for entry in exclusions}
    if len(excluded) != len(exclusions):
        raise ValueError("Duplicate Wikimedia exclusion")
    previous_inventory_path = root / "sources/wikimedia/inventory.json"
    previous_inventory = (
        json.loads(previous_inventory_path.read_text(encoding="utf-8"))
        if previous_inventory_path.exists()
        else {"files": [], "excluded": []}
    )
    previous_files = {row["page_id"]: row for row in previous_inventory["files"]}
    previous_exclusions = {row["page_id"] for row in previous_inventory["excluded"]}
    retained_exclusions = []
    for page_id in sorted(set(excluded) - {int(key) for key in metadata}):
        prior = previous_files.get(page_id)
        if (
            prior is None
            or page_id not in previous_exclusions
            or any(
                excluded[page_id].get(field) != prior.get(field)
                for field in ("title", "description_url")
            )
        ):
            raise ValueError("Excluded Wikimedia page is absent from inventory")
        retained_exclusions.append(dict(prior, retained=True))
    previous_path = root / "sources/wikimedia/manifest.json"
    previous = {}
    if previous_path.exists():
        previous = {
            asset["page_id"]: asset
            for asset in json.loads(previous_path.read_text(encoding="utf-8"))["assets"]
        }
    files, entries, inventory = {}, {}, []
    for key, page in sorted(metadata.items(), key=lambda item: int(item[0])):
        page_id = page["pageid"]
        if type(page_id) is not int or page_id < 1 or str(page_id) != key:
            raise ValueError(f"Invalid Wikimedia page id: {key}")
        info = page["imageinfo"][0]
        categories = sorted(set(page["roots"]))
        if not categories or set(categories) - set(CATEGORIES):
            raise ValueError(f"Invalid root category membership: {page_id}")
        description_url = checked_url(info["descriptionurl"])
        inventory.append(
            {
                "page_id": page_id,
                "title": page["title"],
                "categories": categories,
                "description_url": description_url,
            }
        )
        if page_id in excluded:
            if excluded[page_id].get("title") != page["title"] or not excluded[
                page_id
            ].get("reason"):
                raise ValueError(f"Invalid reviewed exclusion: {page_id}")
            continue
        if page_id in entries:
            raise ValueError(f"Duplicate Wikimedia page: {page_id}")
        image_url = checked_url(info["url"])
        cached = cached_asset(root, previous.get(page_id), info)
        if cached:
            entry = dict(cached)
        else:
            original = fetch(image_url)
            if hashlib.sha1(original).hexdigest() != info.get("sha1"):
                raise ValueError(
                    f"Original SHA-1 differs from Wikimedia metadata: {page_id}"
                )
            converted, extension, rendered_url = convert_asset(info, original, fetch)
            png, source_format, width, height, transparency = converted
            original_hash = hashlib.sha256(original).hexdigest()
            original_path = (
                f"sources/wikimedia/originals/{page_id}-{original_hash[:16]}{extension}"
            )
            logo_path = f"logosChannels/commons-{page_id}.png"
            files[original_path], files[logo_path] = original, png
            entry = {
                "page_id": page_id,
                "original": original_path,
                "original_sha256": original_hash,
                "logo": logo_path,
                "logo_sha256": hashlib.sha256(png).hexdigest(),
                "width": width,
                "height": height,
                "source_format": source_format,
                "transparency": transparency,
                "rendered_url": rendered_url,
            }
        entry.update(
            {
                "title": page["title"],
                "categories": categories,
                "description_url": description_url,
                "image_url": image_url,
                "source_timestamp": info.get("timestamp", ""),
                "source_sha1": info.get("sha1", ""),
                "source_width": info["width"],
                "source_height": info["height"],
                "artist": metadata_value(info, "Artist"),
                "credit": metadata_value(info, "Credit"),
                "license_short_name": metadata_value(info, "LicenseShortName"),
                "license_url": metadata_value(info, "LicenseUrl"),
                "attribution_required": {
                    "true": True,
                    "false": False,
                }.get(metadata_value(info, "AttributionRequired").lower()),
                "extmetadata": info.get("extmetadata", {}),
            }
        )
        entries[page_id] = entry
    discovered_count = len(inventory)
    inventory.extend(retained_exclusions)
    # Keep previously imported sources when category membership changes upstream.
    for page_id, prior in previous.items():
        if page_id not in entries and page_id not in excluded:
            entries[page_id] = prior
            inventory.append(
                {
                    "page_id": page_id,
                    "title": prior["title"],
                    "categories": prior["categories"],
                    "description_url": prior["description_url"],
                    "retained": True,
                }
            )
    manifest = {
        "schema_version": 1,
        "categories": CATEGORIES,
        "assets": [entries[key] for key in sorted(entries)],
    }
    files["sources/wikimedia/manifest.json"] = json_bytes(manifest)
    files["sources/wikimedia/inventory.json"] = json_bytes(
        {
            "schema_version": 1,
            "categories": CATEGORIES,
            "expected_count": len(inventory),
            "discovered_count": discovered_count,
            "files": sorted(inventory, key=lambda entry: entry["page_id"]),
            "excluded": sorted(exclusions, key=lambda entry: entry["page_id"]),
        }
    )
    files.update(prepare_upserts(root, manifest, files))
    root.mkdir(parents=True, exist_ok=True)
    # No published path changes until every download and decoder has succeeded.
    with tempfile.TemporaryDirectory(prefix=".wikimedia-import-", dir=root) as staging:
        staging = Path(staging)
        for relative, data in files.items():
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for relative in files:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            (staging / relative).replace(target)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent
    )
    parser.add_argument(
        "--metadata-file", type=Path, help="Replay a saved MediaWiki page-id inventory"
    )
    parser.add_argument(
        "--exclusions-file", type=Path, help="Reviewed non-logo file exclusions (JSON)"
    )
    args = parser.parse_args()
    try:
        metadata = (
            json.loads(args.metadata_file.read_text(encoding="utf-8"))
            if args.metadata_file
            else None
        )
        exclusions_path = (
            args.exclusions_file
            or args.output_dir / "sources/wikimedia/exclusions.json"
        )
        if args.exclusions_file and not exclusions_path.is_file():
            raise ValueError(f"Exclusions file not found: {exclusions_path}")
        saved_inventory = args.output_dir / "sources/wikimedia/inventory.json"
        if exclusions_path.exists():
            exclusions = json.loads(exclusions_path.read_text(encoding="utf-8"))[
                "excluded"
            ]
        elif saved_inventory.exists():
            exclusions = json.loads(saved_inventory.read_text(encoding="utf-8")).get(
                "excluded", []
            )
        else:
            exclusions = []
        manifest = import_collection(args.output_dir, metadata, exclusions=exclusions)
    except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
        parser.exit(1, f"Wikimedia import failed: {error}\n")
    print(f"Upserted {len(manifest['assets'])} Wikimedia logo assets")


if __name__ == "__main__":
    main()
