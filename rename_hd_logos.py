"""Remove the final HD suffix from channel logo filenames and update the catalog."""

import json
from pathlib import Path

import update_catalog
from update_index import IMAGE_FORMATS


def rename_logos(root):
    root = Path(root)
    folder = root / "logosChannels"
    images = [
        p
        for p in folder.iterdir()
        if p.is_file() and not p.is_symlink() and p.suffix.lower() in IMAGE_FORMATS
    ]
    candidates = sorted(
        (p for p in images if len(p.stem) > 2 and p.stem.lower().endswith("hd")),
        key=lambda p: (-len(p.stem), p.name),
    )
    mapping = {
        f"logosChannels/{p.name}": f"logosChannels/{p.stem[:-2]}{p.suffix}"
        for p in candidates
    }
    if any(Path(target).stem.lower().endswith("hd") for target in mapping.values()):
        raise ValueError("A renamed logo would still end in HD; resolve it first")

    manifest_path = root / "logo_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        path = entry["logo"]
        target = mapping.get(path)
        if (
            target
            and (root / target).exists()
            and (root / path).read_bytes() != (root / target).read_bytes()
        ):
            raise ValueError(f"Catalog destination has different content: {target}")
        entry["logo"] = target or path
    affected = {p.stem.lower() for p in candidates}
    for stem in affected:
        manifest["preferred_images"].pop(stem, None)

    renamed = deleted = 0
    for path in candidates:
        target = root / mapping[f"logosChannels/{path.name}"]
        if target.exists():
            path.unlink()
            deleted += 1
        else:
            path.rename(target)
            renamed += 1

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    update_catalog.run(root)
    return renamed, deleted


if __name__ == "__main__":
    renamed, deleted = rename_logos(Path(__file__).resolve().parent)
    print(f"Logos renommés : {renamed} ; doublons supprimés : {deleted}")
