"""Generate content-addressed PNG logos and a small client catalog offline."""

import argparse
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path

from PIL import Image, ImageOps

from update_index import image_metadata, read_json, validate_entry
from upsert_wikimedia import checked_path

SIZES = (128, 256)
IDENTITY_FIELDS = ("id", "name", "type", "country", "aliases")
CHANGES = (
    "PNG normalisé ; réduction proportionnelle selon la taille, sans agrandissement."
)


class CreditText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        self.parts.append(" ")

    def handle_endtag(self, tag):
        self.parts.append(" ")


def plain_text(value):
    parser = CreditText()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


def credit_sources(root):
    sources = {}
    radio_path = root / "sources/radios/manifest.json"
    if radio_path.exists():
        for station in read_json(radio_path)["stations"]:
            sources[station["id"]] = {
                "logo": station["logo"],
                "sha256": station["logo_sha256"],
                "title": station["name"],
                "source_url": station["page_url"],
                "artist": "",
                "credit": "",
                "license": "",
                "license_url": "",
                "attribution_required": None,
            }
    selection_path = root / "sources/wikimedia/selections.json"
    if selection_path.exists():
        assets = {
            asset["page_id"]: asset
            for asset in read_json(root / "sources/wikimedia/manifest.json")["assets"]
        }
        seen = set()
        for selection in read_json(selection_path)["entries"]:
            identity = selection["id"]
            if identity in seen:
                raise ValueError(f"Duplicate selected credit identity: {identity}")
            seen.add(identity)
            asset = assets[selection["page_id"]]
            sources[identity] = {
                "logo": selection["logo"],
                "sha256": asset["logo_sha256"],
                "title": asset["title"],
                "source_url": asset["description_url"],
                "artist": plain_text(asset["artist"]),
                "credit": plain_text(asset["credit"]),
                "license": plain_text(asset["license_short_name"]),
                "license_url": asset["license_url"],
                "attribution_required": asset["attribution_required"],
            }
    return sources


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
    sources = credit_sources(root)
    files, entries, credits = {}, [], []
    seen = set()
    for entry in catalog["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("logo"), str):
            raise ValueError("Source catalog entry requires a logo path")
        logo = entry["logo"]
        path = checked_path(root, logo)
        if len(Path(logo).parts) != 2 or Path(logo).parts[0] != "logosChannels":
            raise ValueError(f"Invalid source logo path: {logo}")
        validate_entry(entry, {logo}, seen)
        metadata = image_metadata(path, logo)
        source = sources.get(entry["id"])
        if source is not None and (
            source["logo"] != logo or source["sha256"] != metadata["sha256"]
        ):
            raise ValueError(f"Source credit image differs from catalog: {entry['id']}")
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
        credit = {
            "id": entry["id"],
            "name": entry["name"],
            "title": "",
            "source_url": "",
            "artist": "",
            "credit": "",
            "license": "",
            "license_url": "",
            "attribution_required": None,
            "changes": CHANGES,
        }
        if source is not None:
            credit.update(
                {
                    field: value
                    for field, value in source.items()
                    if field not in ("logo", "sha256")
                }
            )
        credits.append(credit)
    entries.sort(key=lambda entry: entry["id"])
    credits.sort(key=lambda entry: entry["id"])
    files["catalog.client.v1.json"] = json_bytes(
        {"schema_version": 1, "entries": entries}
    )
    files["credits.client.v1.json"] = json_bytes(
        {"schema_version": 1, "entries": credits}
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
            f"Client outputs {'validated' if args.check else 'generated'}: {len(files) - 2} PNGs, catalog and credits"
        )
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
