"""Prepare reviewed Wikimedia channel replacements without writing files."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def digest(data):
    return hashlib.sha256(data).hexdigest()


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


def prepare_upserts(root, manifest, files):
    """Validate all selections, then return their PNGs and original backups."""
    root = Path(root)
    selection_path = root / "sources/wikimedia/selections.json"
    if not selection_path.exists():
        return {}
    selections = json.loads(selection_path.read_text(encoding="utf-8"))
    if not isinstance(selections, dict) or not isinstance(
        selections.get("entries"), list
    ):
        raise ValueError("Wikimedia selections must contain an entries list")
    assets = {asset["page_id"]: asset for asset in manifest["assets"]}
    previous_path = root / "sources/wikimedia/upserts.json"
    previous = {}
    retired = []
    if previous_path.exists():
        document = json.loads(previous_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(
            document.get("upserts"), list
        ):
            raise ValueError("Invalid previous upsert provenance")
        for record in document["upserts"]:
            target = record.get("logo")
            if not isinstance(target, str) or target in previous:
                raise ValueError("Duplicate or invalid previous upsert target")
            previous[target] = record
        retired = document.get("retired_upserts", [])
        if not isinstance(retired, list) or any(
            not isinstance(row, dict) for row in retired
        ):
            raise ValueError("Invalid retired upsert provenance")
        retired = list(retired)
    additions = {}
    records = {}
    identities = set()
    targets = set()
    for entry in selections["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("Wikimedia selection must be an object")
        identity = entry.get("id")
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"tv\.[a-z]{2}\.[a-z0-9]+(?:-[a-z0-9]+)*", identity)
            or identity in identities
            or entry.get("type") != "tv"
        ):
            raise ValueError(
                f"Invalid or duplicate selected media identity: {identity}"
            )
        identities.add(identity)
        country = entry.get("country")
        if country is not None and (
            not isinstance(country, str) or not re.fullmatch(r"[A-Z]{2}", country)
        ):
            raise ValueError(f"{identity}: invalid country")
        if not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError(f"{identity}: name must not be empty")
        aliases = entry.get("aliases")
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise ValueError(f"{identity}: invalid aliases")
        page_id = entry.get("page_id")
        if type(page_id) is not int or page_id not in assets:
            raise ValueError(f"{identity}: selected Wikimedia asset is missing")
        asset = assets[page_id]
        source = checked_path(root, asset["logo"])
        if asset["logo"] != f"logosChannels/commons-{page_id}.png":
            raise ValueError(f"{identity}: unexpected Wikimedia PNG path")
        png = files.get(asset["logo"])
        if png is None:
            png = source.read_bytes()
        if digest(png) != asset["logo_sha256"]:
            raise ValueError(f"{identity}: Wikimedia PNG checksum mismatch")
        legacy_paths = entry.get("legacy_paths", [])
        if not isinstance(legacy_paths, list) or any(
            not isinstance(path, str) for path in legacy_paths
        ):
            raise ValueError(f"{identity}: legacy_paths must be a list of paths")
        requested = [entry.get("logo"), *legacy_paths]
        if any(not isinstance(target, str) for target in requested):
            raise ValueError(f"{identity}: upsert target must be a path string")
        for target in dict.fromkeys(requested):
            path = checked_path(root, target)
            parts = PurePosixPath(target).parts
            if (
                len(parts) != 2
                or parts[0] != "logosChannels"
                or path.suffix.lower() != ".png"
                or path.name.startswith("radio-tn-")
                or (path.name.startswith("commons-") and target != asset["logo"])
                or target in targets
            ):
                raise ValueError(
                    f"{identity}: invalid or shared upsert target: {target}"
                )
            targets.add(target)
            old = path.read_bytes() if path.exists() else None
            prior = previous.get(target)
            if old == png and prior is not None and prior.get("page_id") == page_id:
                if prior.get("logo_sha256") != digest(png):
                    raise ValueError(f"{target}: existing upsert checksum mismatch")
                record = prior
            else:
                backup = None
                old_digest = None
                if old is not None and old != png:
                    old_digest = digest(old)
                    backup = f"sources/wikimedia/legacy-originals/{path.stem}-{old_digest}.png"
                    backup_path = checked_path(root, backup)
                    if backup_path.exists() and backup_path.read_bytes() != old:
                        raise ValueError(
                            f"{target}: original backup has different bytes"
                        )
                    additions[backup] = old
                record = {
                    "page_id": page_id,
                    "logo": target,
                    "logo_sha256": digest(png),
                    "previous_logo": backup,
                    "previous_logo_sha256": old_digest,
                }
            records[target] = record
            additions[target] = png
    for target, prior in previous.items():
        if prior != records.get(target) and prior not in retired:
            retired.append(prior)
    provenance = {"upserts": [records[key] for key in sorted(records)]}
    if retired:
        provenance["retired_upserts"] = retired
    additions["sources/wikimedia/upserts.json"] = (
        json.dumps(
            provenance,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    return additions
