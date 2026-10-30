"""Match imported radio logos offline using versioned name and image scores."""

import argparse
from difflib import SequenceMatcher
from html.parser import HTMLParser
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import time
import unicodedata
from urllib.parse import unquote, urlsplit
import warnings

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from upsert_wikimedia import checked_path, prepare_upserts
from update_index import radio_name_aliases

VERSION = 3
THRESHOLDS = {
    "prefilter": 0.60,
    "name": 0.85,
    "visual": 0.85,
    "score": 0.85,
    "margin": 0.06,
}
RADIO_CATEGORIES = {
    "Category:Radio station logos of France": "FR",
    "Category:Radio station logos of Tunisia": "TN",
}
REPORT = "reports/radio_matches.json"
SELECTIONS = "sources/wikimedia/selections.json"
GENERIC_WORDS = {"file", "logo", "wikipedia", "radio", "fm", "am"}


class _Metadata(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.text, self.links = [], []
        self.feed(value)

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value)


def _compact(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(c for c in value if c.isalnum() and not unicodedata.combining(c))


def discover_radios(stations, assets):
    """Bootstrap unknown FM/AM identities only from corroborating local metadata."""
    groups, decisions = {}, []
    for asset in sorted(assets, key=lambda a: a["page_id"]):
        if not any(c in RADIO_CATEGORIES for c in asset["categories"]):
            continue
        decision = {
            "page_id": asset["page_id"],
            "reason": "description_not_identifiable",
        }
        decisions.append(decision)
        description = " ".join(
            _Metadata(
                asset.get("extmetadata", {})
                .get("ImageDescription", {})
                .get("value", "")
            ).text
        ).strip()
        explicit_station = re.fullmatch(
            r"Logo (?:de la station de radio|de la radio|of the radio station) (.{2,70}?)\.?",
            description,
            re.IGNORECASE,
        )
        label = explicit_station or re.fullmatch(
            r"(?:Logo de |Logo of |Logo )(.{2,70}?)\.?", description, re.IGNORECASE
        )
        if label is None:
            continue
        if explicit_station is None and not re.search(
            r"\b(?:FM|AM)\b", label[1], re.IGNORECASE
        ):
            decision["reason"] = "station_type_unconfirmed"
            continue
        name = label[1].strip()
        key = _compact(name)
        title = asset["title"].removeprefix("File:").rsplit(".", 1)[0]
        title = re.sub(r"\b(?:logo|wikipedia)\b", " ", title, flags=re.IGNORECASE)
        countries = {
            RADIO_CATEGORIES[c] for c in asset["categories"] if c in RADIO_CATEGORIES
        }
        if len(countries) != 1:
            decision["reason"] = "ambiguous_country"
            continue
        if key != _compact(title):
            decision["reason"] = "title_mismatch"
            continue
        country = next(iter(countries))
        official = []
        for link in _Metadata(asset.get("credit", "")).links:
            try:
                uri = urlsplit(link)
                if (
                    uri.scheme == "https"
                    and not uri.username
                    and not uri.password
                    and uri.port in (None, 443)
                    and uri.hostname
                    and uri.hostname.removeprefix("www.") == f"{key}.{country.lower()}"
                ):
                    official.append(link)
            except ValueError:
                continue
        if not official:
            decision["reason"] = "no_matching_source_domain"
            continue
        if any(
            (s["country"] is None or s["country"] == country)
            and key in {_compact(s["name"]), *(_compact(a) for a in s["aliases"])}
            for s in stations
        ):
            decision["reason"] = "already_known"
            continue
        folded = (
            unicodedata.normalize("NFKD", name)
            .encode("ascii", "ignore")
            .decode()
            .lower()
        )
        slug = "-".join(re.findall(r"[a-z0-9]+", folded))
        if not slug:
            continue
        entry = {
            "id": f"radio.{country.lower()}.{slug}",
            "name": name,
            "type": "radio",
            "country": country,
            "aliases": [],
            "page_id": asset["page_id"],
            "logo": asset["logo"],
            "legacy_paths": [],
            "description_url": asset["description_url"],
            "discovery": "commons-official-source-v1",
        }
        decision.update(
            name=name,
            country=country,
            official_source=sorted(official)[0],
            reason="ambiguous_variants",
        )
        groups.setdefault((country, key), []).append((entry, asset, decision))
    entries = []
    for members in groups.values():
        if len({a["logo_sha256"] for _, a, _ in members}) != 1:
            continue
        entry, _, decision = max(
            members,
            key=lambda m: (
                m[1].get("source_width", m[1].get("width", 0))
                * m[1].get("source_height", m[1].get("height", 0)),
                m[1].get("transparency", False),
                -m[1]["page_id"],
            ),
        )
        decision["reason"] = "accepted"
        entries.append(entry)
    return sorted(entries, key=lambda e: e["id"]), decisions


def normalize_name(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(t for t in re.findall(r"[^\W_]+", text) if t not in GENERIC_WORDS)


def name_score(station, asset):
    title = normalize_name(asset["title"].removeprefix("File:").rsplit(".", 1)[0])
    labels = [
        station["name"],
        *station["aliases"],
        unquote(urlsplit(station["page_url"]).path.rstrip("/").rsplit("/", 1)[-1]),
    ]
    scores = []
    for label in labels:
        label = normalize_name(label)
        if title and label:
            scores.append(
                min(
                    SequenceMatcher(None, label, title, autojunk=False).ratio(),
                    SequenceMatcher(None, title, label, autojunk=False).ratio(),
                )
            )
    return max(scores, default=0.0)


def image_signature(data):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
    box = image.getchannel("A").getbbox()
    if box is None:
        raise ValueError("Empty transparent logo")
    image = image.crop(box)
    transparent = image.getchannel("A").getextrema()[0] < 255
    if not transparent:
        rgb = image.convert("RGB")
        corners = [
            rgb.getpixel(p)
            for p in [
                (0, 0),
                (rgb.width - 1, 0),
                (0, rgb.height - 1),
                (rgb.width - 1, rgb.height - 1),
            ]
        ]
        if all(max(abs(a - b) for a, b in zip(c, corners[0])) <= 12 for c in corners):
            difference = ImageChops.difference(
                rgb, Image.new("RGB", rgb.size, corners[0])
            )
            box = (
                difference.convert("L").point(lambda x: 255 if x > 12 else 0).getbbox()
            )
            if box is None:
                raise ValueError("Empty uniform logo")
            image = image.crop(box)
    width, height = image.size
    small = ImageOps.contain(image, (64, 64), Image.Resampling.LANCZOS)
    planes, pixels = [], []
    for background in ("white", "black"):
        frame = Image.new("RGB", (64, 64), background)
        frame.paste(
            small,
            ((64 - small.width) // 2, (64 - small.height) // 2),
            small.getchannel("A"),
        )
        pixels.append(frame.tobytes())
        planes.append(frame.filter(ImageFilter.GaussianBlur(1)))
    gray = planes[0].convert("L").resize((9, 8))
    dhash = sum(
        (gray.getpixel((x, y)) > gray.getpixel((x + 1, y))) << (y * 8 + x)
        for y in range(8)
        for x in range(8)
    )
    return {
        "planes": planes,
        "dhash": dhash,
        "pixels_sha256": hashlib.sha256(b"".join(pixels)).hexdigest(),
        "width": width,
        "height": height,
        "transparency": transparent,
    }


def visual_score(left, right):
    color = (
        1
        - sum(
            sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3 / 255
            for a, b in zip(left["planes"], right["planes"])
        )
        / 2
    )
    shape = 1 - (left["dhash"] ^ right["dhash"]).bit_count() / 64
    return {"color": color, "shape": shape, "visual": 0.6 * color + 0.4 * shape}


def asset_country(asset):
    if asset.get("country"):
        return asset["country"]
    countries = {
        RADIO_CATEGORIES[c] for c in asset["categories"] if c in RADIO_CATEGORIES
    }
    return next(iter(countries)) if len(countries) == 1 else None


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build_outputs(root):
    root = Path(root).resolve()
    inputs = {}

    def read(relative):
        data = checked_path(root, relative).read_bytes()
        inputs[relative] = hashlib.sha256(data).hexdigest()
        return json.loads(data)

    stations = read("sources/radios/manifest.json")["stations"]
    enriched_stations, alias_audit = radio_name_aliases(stations)
    manifest = read("sources/wikimedia/manifest.json")
    # Selections are output, never evidence for the next match.
    selections = json.loads(checked_path(root, SELECTIONS).read_bytes())
    assets = [
        a
        for a in manifest["assets"]
        if any(c in RADIO_CATEGORIES for c in a["categories"])
    ]
    if len({s["id"] for s in stations}) != len(stations) or len(
        {a["page_id"] for a in assets}
    ) != len(assets):
        raise ValueError("Duplicate radio station or Wikimedia page id")
    signatures = {}

    def signature(relative, expected):
        if relative not in signatures:
            data = checked_path(root, relative).read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise ValueError(f"Logo checksum differs: {relative}")
            try:
                signatures[relative] = (actual, image_signature(data))
            except (OSError, ValueError, Image.DecompressionBombWarning) as error:
                raise ValueError(f"Invalid logo {relative}: {error}") from error
        actual, result = signatures[relative]
        if actual != expected:
            raise ValueError(f"Conflicting logo checksum: {relative}")
        return result

    rows, accepted = [], []
    for station in sorted(stations, key=lambda s: s["id"]):
        source = signature(station["logo"], station["logo_sha256"])
        candidates, groups = [], {}
        for asset in sorted(assets, key=lambda a: a["page_id"]):
            n = name_score(station, asset)
            if n < THRESHOLDS["prefilter"]:
                continue
            country = asset_country(asset)
            candidate = {
                "page_id": asset["page_id"],
                "title": asset["title"],
                "source_sha256": asset["logo_sha256"],
                "country": country,
                "name_score": n,
                "color_score": None,
                "shape_score": None,
                "visual_score": None,
                "score": None,
                "reason": "country_conflict",
            }
            candidates.append(candidate)
            if station["country"] and country and station["country"] != country:
                continue
            target = signature(asset["logo"], asset["logo_sha256"])
            scores = visual_score(source, target)
            candidate.update(
                color_score=scores["color"],
                shape_score=scores["shape"],
                visual_score=scores["visual"],
                score=0.8 * scores["visual"] + 0.2 * n,
                reason="ranked",
            )
            if n < THRESHOLDS["name"]:
                candidate["reason"] = "low_name_score"
            elif scores["visual"] < THRESHOLDS["visual"]:
                candidate["reason"] = "low_visual_score"
            elif candidate["score"] < THRESHOLDS["score"]:
                candidate["reason"] = "low_score"
            groups.setdefault(target["pixels_sha256"], []).append(
                (candidate, asset, target)
            )
        ranked = []
        for members in groups.values():
            # Identity scores may differ between duplicate filenames; use the best
            # evidence for the group, then choose its highest quality source.
            best_score = max(m[0]["score"] for m in members)
            group_name = max(m[0]["name_score"] for m in members)
            for member, _, _ in members:
                member.update(
                    group_score=best_score,
                    group_name_score=group_name,
                    equivalent_pages=sorted(m[1]["page_id"] for m in members),
                )
            representative = max(
                members,
                key=lambda m: (
                    m[2]["width"] * m[2]["height"],
                    m[2]["transparency"],
                    -m[1]["page_id"],
                ),
            )
            ranked.append((best_score, representative, group_name))
        ranked.sort(key=lambda g: (-g[0], g[1][1]["page_id"]))
        reason, selected = "no_candidate", None
        margin = None
        if ranked:
            score, (winner, asset, _), group_name = ranked[0]
            margin = score - ranked[1][0] if len(ranked) > 1 else None
            reason = "low_score"
            if group_name < THRESHOLDS["name"]:
                reason = "low_name_score"
            elif winner["visual_score"] < THRESHOLDS["visual"]:
                reason = "low_visual_score"
            if (
                group_name >= THRESHOLDS["name"]
                and winner["visual_score"] >= THRESHOLDS["visual"]
                and score >= THRESHOLDS["score"]
            ):
                reason = "ambiguous_variants"
                winner["reason"] = reason
                if margin is None or margin >= THRESHOLDS["margin"]:
                    reason, selected = "accepted", asset["page_id"]
                    accepted.append(
                        {
                            **{
                                k: station[k]
                                for k in ("id", "name", "type", "country", "aliases")
                            },
                            "page_id": selected,
                            "logo": asset["logo"],
                            "legacy_paths": [],
                            "description_url": asset["description_url"],
                            "match_algorithm_version": VERSION,
                        }
                    )
                    winner["reason"] = "accepted"
        candidates.sort(
            key=lambda c: (
                -(c["score"] if c["score"] is not None else -1),
                c["page_id"],
            )
        )
        rows.append(
            {
                "id": station["id"],
                "source_sha256": station["logo_sha256"],
                "status": "accepted" if selected else "unmatched",
                "reason": reason,
                "selected_page_id": selected,
                "margin": margin,
                "candidates": candidates,
            }
        )
    discovered, discovery_decisions = discover_radios(enriched_stations, assets)
    accepted.extend(discovered)
    result = dict(
        selections,
        entries=[s for s in selections["entries"] if s["type"] != "radio"] + accepted,
    )
    report = {
        "schema_version": 1,
        "algorithm_version": VERSION,
        "thresholds": THRESHOLDS,
        "inputs": inputs,
        "station_count": len(stations),
        "asset_count": len(assets),
        "accepted_count": len(accepted),
        "stations": rows,
        "discovered_count": len(discovered),
        "discoveries": discovery_decisions,
        "name_aliases": alias_audit,
    }
    return report, result


def run(root, apply=False, check=False):
    root = Path(root).resolve()
    report, selections = build_outputs(root)
    files = {REPORT: json_bytes(report)}
    if apply or check:
        manifest = json.loads(
            checked_path(root, "sources/wikimedia/manifest.json").read_bytes()
        )
        upserts = prepare_upserts(root, manifest, {}, selections=selections)
        for name, data in upserts.items():
            if name == "sources/wikimedia/upserts.json":
                files[name] = data
            elif (
                not checked_path(root, name).is_file()
                or checked_path(root, name).read_bytes() != data
            ):
                raise ValueError(
                    f"Matching cannot replace existing images; repair the import first: {name}"
                )
        files[SELECTIONS] = json_bytes(selections)
    if check:
        stale = [
            name
            for name, data in files.items()
            if not checked_path(root, name).is_file()
            or checked_path(root, name).read_bytes() != data
        ]
        if stale:
            raise ValueError("Radio matches missing or outdated: " + ", ".join(stale))
    else:
        # Prepare and validate everything before replacing any published path.
        with tempfile.TemporaryDirectory(
            prefix=".radio-matches-", dir=root
        ) as directory:
            staged = Path(directory)
            for name, data in files.items():
                target = checked_path(root, name)
                if target.is_file() and target.read_bytes() == data:
                    continue
                path = staged / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                target.parent.mkdir(parents=True, exist_ok=True)
            for path in sorted(staged.rglob("*")):
                if path.is_file():
                    path.replace(
                        checked_path(root, path.relative_to(staged).as_posix())
                    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply", action="store_true", help="Apply accepted radio selections"
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="Verify report and radio selections without writing",
    )
    args = parser.parse_args()
    started = time.perf_counter()
    try:
        report = run(Path(__file__).resolve().parent, args.apply, args.check)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Radio matching failed: {error}\n")
    print(
        f"Radio matches: {report['accepted_count']} accepted ({report['discovered_count']} new), {report['station_count']} existing stations, {report['asset_count']} assets, {time.perf_counter() - started:.3f}s"
    )


if __name__ == "__main__":
    main()
