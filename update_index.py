"""Build reproducible logo indexes without changing source images."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit
from xml.parsers import expat

from PIL import Image

IMAGE_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".gif": "GIF",
    ".webp": "WEBP",
    ".jfif": "JPEG",
}
PUBLIC_FIELDS = ("id", "name", "type", "country", "aliases", "logo")


def read_json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def image_metadata(path, relative):
    """Verify the complete file and decode every frame, including alpha."""
    if path.is_symlink():
        raise ValueError(f"{relative}: image symlinks are not supported")
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            actual_format = image.format
            if actual_format not in set(IMAGE_FORMATS.values()):
                raise ValueError(f"unsupported decoded image format {actual_format}")
            width, height = image.size
            if width <= 0 or height <= 0:
                raise ValueError("dimensions must be positive")
            frames = getattr(image, "n_frames", 1)
            transparent = False
            for frame in range(frames):
                image.seek(frame)
                image.load()
                transparent |= (
                    image.convert("RGBA").getchannel("A").getextrema()[0] < 255
                )
    except Exception as error:
        raise ValueError(f"{relative}: {error}") from error
    return {
        "path": relative,
        "format": actual_format,
        "width": width,
        "height": height,
        "frames": frames,
        "has_transparency": transparent,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "extension_mismatch": IMAGE_FORMATS.get(path.suffix.lower()) != actual_format,
    }


def validate_entry(entry, image_paths, seen):
    if not isinstance(entry, dict) or any(key not in entry for key in PUBLIC_FIELDS):
        raise ValueError(f"Each catalog entry must contain {PUBLIC_FIELDS}")
    identity = entry["id"]
    if not isinstance(identity, str) or not re.fullmatch(
        r"(tv|radio)\.[a-z]{2}\.[a-z0-9]+(?:-[a-z0-9]+)*", identity
    ):
        raise ValueError(f"Invalid stable id: {identity!r}")
    if identity in seen:
        raise ValueError(f"Duplicate catalog id: {identity}")
    seen.add(identity)
    if entry["type"] not in ("tv", "radio") or not identity.startswith(
        entry["type"] + "."
    ):
        raise ValueError(f"{identity}: invalid media type")
    if not isinstance(entry["name"], str) or not entry["name"].strip():
        raise ValueError(f"{identity}: name must not be empty")
    country = entry["country"]
    if country is not None and (
        not isinstance(country, str) or not re.fullmatch(r"[A-Z]{2}", country)
    ):
        raise ValueError(f"{identity}: country must be a two-letter code or null")
    if not isinstance(entry["aliases"], list) or any(
        not isinstance(alias, str) or not alias.strip() for alias in entry["aliases"]
    ):
        raise ValueError(f"{identity}: aliases must be a list of nonempty strings")
    logo = entry["logo"]
    if (
        not isinstance(logo, str)
        or PurePosixPath(logo).is_absolute()
        or ".." in PurePosixPath(logo).parts
        or logo not in image_paths
    ):
        raise ValueError(
            f"{identity}: logo must name an exact indexed image path: {logo!r}"
        )
    return {field: entry[field] for field in PUBLIC_FIELDS}


def source_file(root, relative, prefix=("sources", "radios", "originals")):
    if not isinstance(relative, str):
        raise ValueError("Original image path must be a string")
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != relative
        or path.parts[: len(prefix)] != prefix
    ):
        raise ValueError(f"Unsafe original image path: {relative!r}")
    full = root / relative
    ancestors = [
        root.joinpath(*path.parts[:count]) for count in range(1, len(path.parts) + 1)
    ]
    if not full.is_file() or any(ancestor.is_symlink() for ancestor in ancestors):
        raise ValueError(f"Missing or unsafe original image: {relative}")
    return full


def radio_entries(root, image_metadata_by_path):
    path = root / "sources" / "radios" / "manifest.json"
    if not path.exists():
        return [], []
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise ValueError("Radio manifest must be an object")
    stations = manifest.get("stations")
    expected = manifest.get("expected_count")
    if (
        not isinstance(stations, list)
        or type(expected) is not int
        or expected <= 0
        or len(stations) != expected
    ):
        raise ValueError(f"Radio import incomplete: expected {expected} stations")
    pages = set()
    logos = set()
    originals = {}
    for station in stations:
        if not isinstance(station, dict):
            raise ValueError("Radio station must be an object")
        for field in ("page_url", "image_url"):
            address = station.get(field)
            if not isinstance(address, str):
                raise ValueError(f"Radio {field} must be an HTTPS URL")
            parsed = urlsplit(address)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username
                or parsed.password
            ):
                raise ValueError(f"Radio {field} must be an HTTPS URL: {address!r}")
        page = station["page_url"]
        logo = station.get("logo")
        if page in pages or not isinstance(logo, str) or logo in logos:
            raise ValueError(f"Duplicate radio page or logo: {page}")
        pages.add(page)
        logos.add(logo)
        if station.get("type") != "radio" or not logo.startswith(
            "logosChannels/radio-tn-"
        ):
            raise ValueError(f"Invalid imported radio type or logo: {page}")
        original = station.get("original")
        source = source_file(root, original)
        if original not in originals:
            originals[original] = image_metadata(source, original)
        original_info = originals[original]
        logo_info = image_metadata_by_path.get(logo)
        if logo_info is None:
            raise ValueError(f"Missing indexed radio image: {logo}")
        for field, info in (
            ("original_sha256", original_info),
            ("logo_sha256", logo_info),
        ):
            if station.get(field) != info["sha256"]:
                raise ValueError(f"{page}: {field} checksum mismatch")
        if (station.get("width"), station.get("height")) != (
            logo_info["width"],
            logo_info["height"],
        ):
            raise ValueError(f"{page}: published image dimensions differ from manifest")
        if logo_info["format"] != "PNG" or logo_info["frames"] != 1:
            raise ValueError(f"{page}: published radio image must be a static PNG")
        if station.get("format") != original_info["format"]:
            raise ValueError(f"{page}: original image format differs from manifest")
        if station.get("transparency") != logo_info["has_transparency"]:
            raise ValueError(f"{page}: published transparency differs from manifest")
        if not isinstance(station.get("fallback"), bool):
            raise ValueError(f"{page}: fallback status is required")
    return stations, [originals[key] for key in sorted(originals)]


def svg_metadata(path, relative):
    data = path.read_bytes()
    root = []
    parser = expat.ParserCreate(namespace_separator="}")

    def reject_entity(*args):
        raise ValueError("SVG entity declarations and external entities are forbidden")

    def start_element(name, attributes):
        if not root:
            root.append((name, attributes))

    parser.StartElementHandler = start_element
    parser.EntityDeclHandler = reject_entity
    parser.ExternalEntityRefHandler = reject_entity
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(data, True)
    except (expat.ExpatError, ValueError) as error:
        raise ValueError(f"{relative}: invalid SVG: {error}") from error
    if not root or root[0][0] not in ("svg", "http://www.w3.org/2000/svg}svg"):
        raise ValueError(f"{relative}: original XML root is not SVG")
    attributes = root[0][1]
    return {
        "path": relative,
        "format": "SVG",
        "sha256": hashlib.sha256(data).hexdigest(),
        "declared_width": attributes.get("width"),
        "declared_height": attributes.get("height"),
        "view_box": attributes.get("viewBox"),
    }


def page_map(items, label):
    if not isinstance(items, list):
        raise ValueError(f"Wikimedia {label} must be a list")
    result = {}
    for item in items:
        if (
            not isinstance(item, dict)
            or type(item.get("page_id")) is not int
            or item["page_id"] <= 0
        ):
            raise ValueError(f"Wikimedia {label} requires positive integer page_id")
        identity = item["page_id"]
        if identity in result:
            raise ValueError(f"Duplicate Wikimedia page_id in {label}: {identity}")
        result[identity] = item
    return result


def wikimedia_entries(root, image_metadata_by_path):
    folder = root / "sources" / "wikimedia"
    empty_report = {
        "schema_version": 1,
        "categories": [],
        "expected_count": 0,
        "imported_count": 0,
        "selected_media_count": 0,
        "selected_path_count": 0,
        "files": [],
        "assets": [],
        "excluded": [],
    }
    manifest_path = folder / "manifest.json"
    inventory_path = folder / "inventory.json"
    if not manifest_path.exists() and not inventory_path.exists():
        if (folder / "selections.json").exists():
            raise ValueError("Wikimedia selections require a manifest and inventory")
        return [], [], empty_report
    manifest = read_json(manifest_path)
    inventory = read_json(inventory_path)
    if any(
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        for document in (manifest, inventory)
    ):
        raise ValueError("Wikimedia manifest and inventory require schema_version 1")
    categories = manifest.get("categories")
    if (
        not isinstance(categories, list)
        or not categories
        or any(
            not isinstance(category, str) or not category.startswith("Category:")
            for category in categories
        )
        or len(categories) != len(set(categories))
        or categories != inventory.get("categories")
    ):
        raise ValueError(
            "Wikimedia manifest and inventory categories differ or are invalid"
        )
    assets = page_map(manifest.get("assets"), "assets")
    files = page_map(inventory.get("files"), "inventory files")
    excluded = page_map(inventory.get("excluded"), "exclusions")
    if (
        type(inventory.get("expected_count")) is not int
        or inventory["expected_count"] != len(files)
        or set(assets) & set(excluded)
        or set(files) != set(assets) | set(excluded)
    ):
        raise ValueError(
            "Wikimedia inventory coverage must equal imported assets plus explicit exclusions"
        )
    discovered = inventory.get("discovered_count", len(files))
    if type(discovered) is not int or not 0 <= discovered <= len(files):
        raise ValueError("Wikimedia discovered_count exceeds inventory coverage")
    exclusions_path = folder / "exclusions.json"
    if exclusions_path.exists():
        explicit = read_json(exclusions_path)
        if (
            not isinstance(explicit, dict)
            or page_map(explicit.get("excluded"), "explicit exclusions") != excluded
        ):
            raise ValueError("Wikimedia exclusions differ from inventory")
    for identity, exclusion in excluded.items():
        if (
            not isinstance(exclusion.get("reason"), str)
            or not exclusion["reason"].strip()
            or exclusion.get("title") != files[identity].get("title")
            or exclusion.get("description_url")
            != files[identity].get("description_url")
        ):
            raise ValueError(
                f"Wikimedia exclusion lacks matching provenance or reason: {identity}"
            )
    originals = []
    original_paths = set()
    logo_paths = set()
    for identity, asset in sorted(assets.items()):
        candidate = files[identity]
        if not isinstance(asset.get("title"), str) or not asset["title"].startswith(
            "File:"
        ):
            raise ValueError(f"Wikimedia asset {identity}: invalid File title")
        for field in ("title", "categories", "description_url"):
            if asset.get(field) != candidate.get(field):
                raise ValueError(
                    f"Wikimedia asset {identity}: inventory {field} differs"
                )
        if (
            not isinstance(asset.get("categories"), list)
            or not asset["categories"]
            or any(category not in categories for category in asset["categories"])
        ):
            raise ValueError(f"Wikimedia asset {identity}: unknown category")
        for field in ("description_url", "image_url"):
            address = asset.get(field)
            if not isinstance(address, str):
                raise ValueError(f"Wikimedia asset {identity}: missing {field}")
            parsed = urlsplit(address)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username
                or parsed.password
            ):
                raise ValueError(f"Wikimedia asset {identity}: invalid HTTPS {field}")
        for field in (
            "source_timestamp",
            "artist",
            "credit",
            "license_short_name",
            "license_url",
        ):
            if not isinstance(asset.get(field), str):
                raise ValueError(
                    f"Wikimedia asset {identity}: missing provenance {field}"
                )
        if (
            asset.get("attribution_required") is not None
            and type(asset["attribution_required"]) is not bool
        ):
            raise ValueError(
                f"Wikimedia asset {identity}: invalid attribution_required"
            )
        if "attribution_required" not in asset:
            raise ValueError(
                f"Wikimedia asset {identity}: missing attribution_required"
            )
        original = asset.get("original")
        path = source_file(root, original, ("sources", "wikimedia", "originals"))
        logo = asset.get("logo")
        if not isinstance(logo, str):
            raise ValueError(f"Wikimedia asset {identity}: logo path must be a string")
        if original in original_paths or logo in logo_paths:
            raise ValueError(f"Duplicate Wikimedia asset path for page_id {identity}")
        original_paths.add(original)
        logo_paths.add(logo)
        if logo != f"logosChannels/commons-{identity}.png":
            raise ValueError(f"Wikimedia asset {identity}: invalid published logo path")
        source_format = asset.get("source_format")
        if source_format == "SVG":
            info = svg_metadata(path, original)
        else:
            info = image_metadata(path, original)
        if info["format"] != source_format or info["sha256"] != asset.get(
            "original_sha256"
        ):
            raise ValueError(
                f"Wikimedia asset {identity}: original format or checksum differs"
            )
        published = image_metadata_by_path.get(logo)
        if (
            published is None
            or published["format"] != "PNG"
            or published["frames"] != 1
            or published["sha256"] != asset.get("logo_sha256")
            or type(asset.get("transparency")) is not bool
            or published["has_transparency"] != asset["transparency"]
            or (published["width"], published["height"])
            != (asset.get("width"), asset.get("height"))
        ):
            raise ValueError(
                f"Wikimedia asset {identity}: published PNG render or checksum differs"
            )
        originals.append(info)
    selections_path = folder / "selections.json"
    selections = []
    if selections_path.exists():
        document = read_json(selections_path)
        if not isinstance(document, dict) or not isinstance(
            document.get("entries"), list
        ):
            raise ValueError("Wikimedia selections require entries list")
        selections = document["entries"]
    selected_by_page = defaultdict(list)
    seen = set()
    selected_paths = set()
    selected_pairs = set()
    for selection in selections:
        entry = validate_entry(selection, set(image_metadata_by_path), seen)
        identity = selection.get("page_id")
        if type(identity) is not int or identity not in assets:
            raise ValueError(
                f"Wikimedia selection refers to unknown page_id: {identity}"
            )
        legacy_paths = selection.get("legacy_paths", [])
        if not isinstance(legacy_paths, list) or any(
            not isinstance(target, str) for target in legacy_paths
        ):
            raise ValueError(
                f"Wikimedia legacy_paths must be a list of paths: {entry['id']}"
            )
        targets = sorted(set([entry["logo"], *legacy_paths]))
        for target in targets:
            if target in selected_paths:
                raise ValueError(
                    f"Duplicate Wikimedia selected logo shared by media: {target}"
                )
            selected_paths.add(target)
            selected_pairs.add((identity, target))
            metadata = image_metadata_by_path.get(target)
            if (
                metadata is None
                or metadata["sha256"] != assets[identity]["logo_sha256"]
            ):
                raise ValueError(f"Wikimedia selected logo checksum differs: {target}")
        report_entry = dict(entry)
        if "legacy_paths" in selection:
            report_entry["legacy_paths"] = legacy_paths
        selected_by_page[identity].append(report_entry)
    upserts_path = folder / "upserts.json"
    if upserts_path.exists():
        document = read_json(upserts_path)
        if not isinstance(document, dict) or not isinstance(
            document.get("upserts"), list
        ):
            raise ValueError("Wikimedia upserts require an upserts list")
        upsert_pairs = set()
        for upsert in document["upserts"]:
            if not isinstance(upsert, dict):
                raise ValueError("Wikimedia upsert must be an object")
            identity, logo = upsert.get("page_id"), upsert.get("logo")
            if (
                type(identity) is not int
                or not isinstance(logo, str)
                or (identity, logo) not in selected_pairs
                or (identity, logo) in upsert_pairs
            ):
                raise ValueError("Wikimedia upserts do not match unique selected logos")
            upsert_pairs.add((identity, logo))
            if upsert.get("logo_sha256") != assets[identity]["logo_sha256"]:
                raise ValueError(f"Wikimedia upsert checksum differs: {logo}")
            previous = upsert.get("previous_logo")
            checksum = upsert.get("previous_logo_sha256")
            if previous is None:
                if checksum is not None:
                    raise ValueError(
                        f"Wikimedia backup checksum without previous_logo: {logo}"
                    )
            else:
                backup = source_file(
                    root, previous, ("sources", "wikimedia", "legacy-originals")
                )
                if hashlib.sha256(backup.read_bytes()).hexdigest() != checksum:
                    raise ValueError(
                        f"Wikimedia backup previous_logo checksum differs: {previous}"
                    )
        if upsert_pairs != selected_pairs:
            raise ValueError("Wikimedia upserts coverage differs from selected logos")
    report_assets = []
    for identity, asset in sorted(assets.items()):
        report_assets.append(
            dict(
                asset,
                status="selected" if selected_by_page[identity] else "available",
                selected_entries=sorted(
                    selected_by_page[identity], key=lambda entry: entry["id"]
                ),
            )
        )
    report = {
        "schema_version": 1,
        "categories": categories,
        "expected_count": len(files),
        "discovered_count": discovered,
        "imported_count": len(assets),
        "selected_media_count": len(selections),
        "selected_path_count": len(selected_paths),
        "files": [files[key] for key in sorted(files)],
        "assets": report_assets,
        "excluded": [excluded[key] for key in sorted(excluded)],
    }
    return selections, originals, report


def build_outputs(root):
    root = Path(root)
    folder = root / "logosChannels"
    candidates = sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_FORMATS
    )
    metadata = []
    errors = []
    for path in candidates:
        try:
            metadata.append(image_metadata(path, "logosChannels/" + path.name))
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError("Invalid indexed images:\n" + "\n".join(errors))
    manifest = read_json(root / "logo_manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("Logo manifest must be an object")
    preferences = manifest.get("preferred_images")
    if not isinstance(preferences, dict):
        raise ValueError("preferred_images must be an object")
    groups = defaultdict(list)
    for path in candidates:
        groups[path.stem.lower()].append(path.name)
    collisions = []
    for stem, variants in sorted(groups.items()):
        if len(variants) > 1:
            preferred = preferences.get(stem)
            if preferred not in variants:
                raise ValueError(
                    f"Missing or invalid explicit preference for {stem}: {variants}"
                )
            collisions.append(
                {"name": stem, "images": variants, "preferred": preferred}
            )
    for stem, preferred in preferences.items():
        if stem not in groups or len(groups[stem]) < 2 or preferred not in groups[stem]:
            raise ValueError(f"Stale or invalid preference for {stem}: {preferred}")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")
    entries = list(entries)
    stations, originals = radio_entries(root, {item["path"]: item for item in metadata})
    entries.extend(stations)
    wiki_selections, wiki_originals, wiki_report = wikimedia_entries(
        root, {item["path"]: item for item in metadata}
    )
    originals.extend(wiki_originals)
    image_paths = {item["path"] for item in metadata}
    seen = set()
    catalog = sorted(
        (validate_entry(entry, image_paths, seen) for entry in entries),
        key=lambda entry: entry["id"],
    )
    by_id = {entry["id"]: entry for entry in catalog}
    for selection in wiki_selections:
        entry = {field: selection[field] for field in PUBLIC_FIELDS}
        by_id[entry["id"]] = entry
    catalog = sorted(by_id.values(), key=lambda entry: entry["id"])
    identified = {entry["logo"] for entry in catalog}
    return {
        "index_picons.json": {"name": [path.name for path in candidates]},
        "catalog.v1.json": {"schema_version": 1, "entries": catalog},
        "reports/images.json": {"images": metadata, "originals": originals},
        "reports/collisions.json": {"collisions": collisions},
        "reports/wikimedia.json": wiki_report,
        "reports/unidentified.json": {"images": sorted(image_paths - identified)},
    }


def serialized(document):
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def write_outputs(outputs, target):
    for relative, document in outputs.items():
        path = Path(target) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized(document), encoding="utf-8")


def check_outputs(outputs, target):
    changed = []
    for relative, document in outputs.items():
        path = Path(target) / relative
        if not path.exists() or path.read_text(encoding="utf-8") != serialized(
            document
        ):
            changed.append(relative)
    if changed:
        raise ValueError(
            "Generated files are missing or outdated: " + ", ".join(changed)
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Compare without writing files"
    )
    parser.add_argument(
        "--output-dir", type=Path, help="Write to a separate output directory"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    try:
        outputs = build_outputs(root)
        target = args.output_dir or root
        if args.check:
            check_outputs(outputs, target)
            print("Logo indexes and reports are current; all indexed images are valid.")
        else:
            write_outputs(outputs, target)
            print(f"Generated {len(outputs)} files in {target}")
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
