"""Build reproducible logo indexes without changing source images."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

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


def source_file(root, relative):
    if not isinstance(relative, str):
        raise ValueError("Original image path must be a string")
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != relative
        or path.parts[:3] != ("sources", "radios", "originals")
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
    image_paths = {item["path"] for item in metadata}
    seen = set()
    catalog = sorted(
        (validate_entry(entry, image_paths, seen) for entry in entries),
        key=lambda entry: entry["id"],
    )
    identified = {entry["logo"] for entry in catalog}
    return {
        "index_picons.json": {"name": [path.name for path in candidates]},
        "catalog.v1.json": {"schema_version": 1, "entries": catalog},
        "reports/images.json": {"images": metadata, "originals": originals},
        "reports/collisions.json": {"collisions": collisions},
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
