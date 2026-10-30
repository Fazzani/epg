"""Generate content-addressed PNG logos and a small client catalog offline."""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps

from update_index import image_metadata, read_json, validate_entry

SIZES = (128, 256)
IDENTITY_FIELDS = ("id", "name", "type", "country", "aliases")


def checked_path(root, relative):
    if not isinstance(relative, str):
        raise ValueError("Image path must be a string")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
        raise ValueError(f"Unsafe image path: {relative}")
    full = root / relative
    for parent in [full, *full.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f"Symlink image path: {relative}")
    return full


def json_bytes(document):
    return (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def build_outputs(root):
    root = Path(root)
    catalog = read_json(root / "catalog.v1.json")
    if (
        not isinstance(catalog, dict)
        or type(catalog.get("schema_version")) is not int
        or catalog["schema_version"] != 1
        or not isinstance(catalog.get("entries"), list)
    ):
        raise ValueError("Source catalog requires schema_version 1 and an entries list")
    files, entries = {}, []
    seen = set()
    for entry in catalog["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("logo"), str):
            raise ValueError("Source catalog entry requires a logo path")
        logo = entry["logo"]
        path = checked_path(root, logo)
        if len(Path(logo).parts) != 2 or Path(logo).parts[0] != "logosChannels":
            raise ValueError(f"Invalid source logo path: {logo}")
        validate_entry(entry, {logo}, seen)
        image_metadata(path, logo)
        variants = {}
        with Image.open(path) as image:
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            alpha = "A" in image.getbands() or "transparency" in image.info
            normalized = image.convert("RGBA" if alpha else "RGB")
            for size in SIZES:
                frame = normalized.copy()
                frame.thumbnail((size, size), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                frame.save(output, format="PNG", optimize=True)
                png = output.getvalue()
                digest = hashlib.sha256(png).hexdigest()
                target = f"logosClient/{digest}.png"
                files[target] = png
                variants[str(size)] = {
                    "path": target,
                    "sha256": digest,
                    "width": frame.width,
                    "height": frame.height,
                    "bytes": len(png),
                }
        entries.append(
            {**{field: entry[field] for field in IDENTITY_FIELDS}, "variants": variants}
        )
    entries.sort(key=lambda entry: entry["id"])
    files["catalog.client.v1.json"] = json_bytes(
        {"schema_version": 1, "entries": entries}
    )
    files["catalog.release.v1.json"] = json_bytes(
        {
            "schema_version": 1,
            "catalog_sha256": hashlib.sha256(
                files["catalog.client.v1.json"]
            ).hexdigest(),
        }
    )
    return files


def publish(files, root, check=False):
    outdated = []
    for relative, data in files.items():
        path = checked_path(Path(root), relative)
        if check:
            if not path.is_file() or path.read_bytes() != data:
                outdated.append(relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    if outdated:
        raise ValueError("Client outputs missing or outdated: " + ", ".join(outdated))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, help="Write client outputs into another directory"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare every expected file without writing",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    try:
        files = build_outputs(root)
        publish(files, args.output_dir or root, check=args.check)
        print(
            f"Client outputs {'validated' if args.check else 'generated'}: {len(files) - 2} PNGs, catalog and release"
        )
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
