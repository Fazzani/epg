import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from match_radio_logos import (
    REPORT,
    build_outputs,
    discover_radios,
    image_signature,
    name_score,
    normalize_name,
    run,
    visual_score,
)

FRANCE = "Category:Radio station logos of France"
TUNISIA = "Category:Radio station logos of Tunisia"


def logo_bytes(padding=0, background=None, accent="blue"):
    motif = Image.new("RGBA", (48, 32), "red")
    draw = ImageDraw.Draw(motif)
    draw.rectangle((0, 0, 15, 31), fill=accent)
    draw.polygon([(16, 31), (35, 0), (47, 31)], fill="yellow")
    if padding:
        canvas = Image.new(
            "RGBA",
            (48 + 2 * padding, 32 + 2 * padding),
            background or (0, 0, 0, 0),
        )
        canvas.paste(motif, (padding, padding))
        motif = canvas
    output = io.BytesIO()
    motif.save(output, format="PNG")
    return output.getvalue()


class SignatureTests(unittest.TestCase):
    def test_name_normalization_aliases_and_page_slug_keep_specific_identity(self):
        self.assertEqual(normalize_name("Étoile_Radio-FM Logo Wikipédia"), "etoile")
        station = {
            "name": "Unrelated",
            "aliases": ["Étoile FM"],
            "page_url": "https://example.test/autre",
        }
        asset = {"title": "File:Etoile radio logo.svg"}
        self.assertEqual(name_score(station, asset), 1)
        station.update(aliases=[], page_url="https://example.test/%C3%A9toile-fm/")
        self.assertEqual(name_score(station, asset), 1)
        self.assertLess(name_score(station, {"title": "File:Etoile DJ.svg"}), 1)
        self.assertEqual(name_score(station, {"title": "File:Radio FM logo.png"}), 0)

    def test_name_similarity_uses_the_minimum_of_both_sequence_directions(self):
        station = {"name": "tide", "aliases": [], "page_url": "https://example.test/"}
        self.assertEqual(name_score(station, {"title": "File:diet.png"}), 0.25)

    def test_padding_is_removed_without_changing_normalized_pixels(self):
        plain = image_signature(logo_bytes())
        for background in [None, "white"]:
            with self.subTest(background=background):
                padded = image_signature(logo_bytes(12, background))
                self.assertEqual((padded["width"], padded["height"]), (48, 32))
                self.assertEqual(padded["pixels_sha256"], plain["pixels_sha256"])
                self.assertAlmostEqual(visual_score(plain, padded)["visual"], 1)

    def test_color_and_shape_weights_and_identical_visual_score(self):
        left = image_signature(logo_bytes())
        right = image_signature(logo_bytes(accent="green"))
        self.assertEqual(len(left["planes"]), 2)
        self.assertTrue(all(plane.mode == "RGB" for plane in left["planes"]))
        self.assertIsInstance(left["dhash"], int)
        self.assertEqual(
            visual_score(left, left), {"color": 1, "shape": 1, "visual": 1}
        )
        scores = visual_score(left, right)
        self.assertLess(scores["color"], 1)
        self.assertAlmostEqual(
            scores["visual"], 0.6 * scores["color"] + 0.4 * scores["shape"]
        )

    def test_invalid_and_empty_images_are_rejected(self):
        for data in [b"invalid PNG", self.empty_png()]:
            with self.subTest(data=data[:10]), self.assertRaises((OSError, ValueError)):
                image_signature(data)

    def test_resizing_and_jpeg_compression_preserve_visual_identity(self):
        original = image_signature(logo_bytes())
        with Image.open(io.BytesIO(logo_bytes())) as image:
            output = io.BytesIO()
            image.convert("RGB").resize((192, 128)).save(
                output, format="JPEG", quality=90
            )
        self.assertGreater(
            visual_score(original, image_signature(output.getvalue()))["visual"], 0.85
        )

    @staticmethod
    def empty_png():
        output = io.BytesIO()
        Image.new("RGBA", (16, 16)).save(output, format="PNG")
        return output.getvalue()


class MatchingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.station = {
            "id": "radio.tn.etoile",
            "name": "Étoile FM",
            "type": "radio",
            "aliases": [],
            "country": None,
            "page_url": "https://example.test/etoile-fm",
            "logo": "logosChannels/radio-tn-etoile.png",
        }
        self.station["logo_sha256"] = self.write_image(
            self.station["logo"], logo_bytes()
        )
        self.assets = [self.asset(10)]
        self.tv = {
            "id": "tv.fr.existing",
            "name": "Existing TV",
            "type": "tv",
            "aliases": [],
            "country": "FR",
            "page_id": 99,
            "logo": "logosChannels/existing.png",
            "legacy_paths": [],
        }
        self.assets.append(self.asset(99, categories=["Category:Television logos"]))
        self.selections = {"entries": [self.tv]}
        self.write("logosChannels/existing.png", logo_bytes())
        self.write("sources/wikimedia/credits.json", b"existing credits")
        self.save()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def write_image(self, relative, data):
        self.write(relative, data)
        return hashlib.sha256(data).hexdigest()

    def asset(self, page_id, data=None, categories=None):
        relative = f"logosChannels/commons-{page_id}.png"
        return {
            "page_id": page_id,
            "title": "File:Etoile logo.svg",
            "logo": relative,
            "logo_sha256": self.write_image(relative, data or logo_bytes()),
            "categories": categories or [TUNISIA],
            "description_url": f"https://commons.wikimedia.org/wiki/File:Etoile_{page_id}.svg",
        }

    def save(self, stations=None):
        for name, document in [
            ("sources/radios/manifest.json", {"stations": stations or [self.station]}),
            ("sources/wikimedia/manifest.json", {"assets": self.assets}),
            ("sources/wikimedia/selections.json", self.selections),
        ]:
            self.write(name, (json.dumps(document, ensure_ascii=False) + "\n").encode())

    def snapshot(self):
        return {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def test_identical_match_reports_evidence_without_mutating_inputs(self):
        before = self.snapshot()
        report, selections = build_outputs(self.root)
        row = report["stations"][0]
        self.assertEqual((row["status"], row["selected_page_id"]), ("accepted", 10))
        self.assertEqual(row["source_sha256"], self.station["logo_sha256"])
        candidate = row["candidates"][0]
        for key in [
            "name_score",
            "color_score",
            "shape_score",
            "visual_score",
            "score",
        ]:
            self.assertEqual(candidate[key], 1)
        self.assertTrue(report["algorithm_version"])
        self.assertEqual(report["thresholds"]["margin"], 0.06)
        for name, digest in report["inputs"].items():
            self.assertEqual(digest, hashlib.sha256(before[name]).hexdigest())
        self.assertEqual(selections["entries"][0], self.tv)
        radio = selections["entries"][1]
        self.assertEqual(
            (radio["id"], radio["logo"], radio["country"]),
            (self.station["id"], self.assets[0]["logo"], None),
        )
        self.assertEqual(before, self.snapshot())

    def test_country_conflicts_are_excluded_but_unknown_is_not_inferred_from_id(self):
        self.assets[0]["categories"] = [FRANCE]
        self.save()
        self.assertEqual(
            build_outputs(self.root)[0]["stations"][0]["status"], "accepted"
        )
        self.station["country"] = "TN"
        self.save()
        row = build_outputs(self.root)[0]["stations"][0]
        self.assertEqual(row["status"], "unmatched")
        self.assertEqual(row["candidates"][0]["reason"], "country_conflict")
        self.assets[0].update(country="FR", categories=[TUNISIA])
        self.save()
        self.assertEqual(
            build_outputs(self.root)[0]["stations"][0]["status"], "unmatched"
        )

    def test_only_exact_radio_roots_are_candidates(self):
        self.assets[0]["categories"] = [TUNISIA + " historical"]
        self.save()
        row = build_outputs(self.root)[0]["stations"][0]
        self.assertEqual(row["status"], "unmatched")
        self.assertEqual(row["candidates"], [])

    def test_name_prefilter_and_independent_acceptance_thresholds(self):
        for n, v, accepted in [
            (0.59, 1, False),
            (0.84, 1, False),
            (1, 0.84, False),
            (0.85, 0.85, True),
        ]:
            with self.subTest(name=n, visual=v), patch(
                "match_radio_logos.name_score", return_value=n
            ), patch(
                "match_radio_logos.visual_score",
                return_value={"color": v, "shape": v, "visual": v},
            ) as scoring:
                row = build_outputs(self.root)[0]["stations"][0]
                self.assertEqual(row["status"] == "accepted", accepted)
                self.assertEqual(scoring.call_count, int(n >= 0.6))
                if row["candidates"]:
                    self.assertAlmostEqual(
                        row["candidates"][0]["score"], 0.8 * v + 0.2 * n
                    )

    def test_distinct_variants_require_margin(self):
        self.assets.append(self.asset(11, logo_bytes(accent="green")))
        self.save()
        for runner_score, accepted in [(0.94, False), (0.9, True)]:
            with self.subTest(runner_score=runner_score), patch(
                "match_radio_logos.visual_score",
                side_effect=[
                    {"color": 1, "shape": 1, "visual": 1},
                    {
                        "color": runner_score,
                        "shape": runner_score,
                        "visual": runner_score,
                    },
                ],
            ):
                row = build_outputs(self.root)[0]["stations"][0]
                self.assertEqual(row["status"] == "accepted", accepted)
                if not accepted:
                    self.assertEqual(row["reason"], "ambiguous_variants")

    def test_duplicate_normalized_pixels_are_one_variant_and_order_is_stable(self):
        self.assets.append(self.asset(5, logo_bytes(10)))
        self.save()
        report, selections = build_outputs(self.root)
        self.assertEqual(report["stations"][0]["selected_page_id"], 5)
        self.assets.reverse()
        self.save()
        reordered, reordered_selections = build_outputs(self.root)
        self.assertEqual(report["stations"], reordered["stations"])
        self.assertEqual(selections, reordered_selections)

    def test_duplicate_representative_prefers_useful_area_then_alpha_then_page_id(self):
        # Supply equivalent normalized signatures to isolate the quality rule.
        self.assets.extend(
            [
                self.asset(11, logo_bytes(accent="green")),
                self.asset(12, logo_bytes(accent="cyan")),
            ]
        )
        for asset in self.assets:
            asset.update(width=10000, height=10000)
        self.save()
        data_ids = {
            (self.root / asset["logo"]).read_bytes(): asset["page_id"]
            for asset in self.assets
            if asset["page_id"] != 99
        }
        base = image_signature(logo_bytes())
        for qualities, expected in [
            ({10: (10, False), 11: (20, False), 12: (10, True)}, 11),
            ({10: (20, False), 11: (20, True), 12: (20, True)}, 11),
        ]:

            def signature(data):
                side, alpha = qualities[data_ids[data]]
                return dict(base, width=side, height=side, transparency=alpha)

            with self.subTest(qualities=qualities), patch(
                "match_radio_logos.image_signature", side_effect=signature
            ):
                row = build_outputs(self.root)[0]["stations"][0]
                self.assertEqual(row["selected_page_id"], expected)

    def test_default_only_writes_report_and_apply_and_check_are_idempotent(self):
        before = self.snapshot()
        run(self.root)
        self.assertEqual(set(self.snapshot()) - set(before), {REPORT})
        self.assertEqual(
            {k: v for k, v in self.snapshot().items() if k != REPORT}, before
        )
        with self.assertRaises(ValueError):
            run(self.root, check=True)
        run(self.root, apply=True)
        applied = self.snapshot()
        run(self.root, apply=True)
        run(self.root, check=True)
        self.assertEqual(applied, self.snapshot())
        for name in [
            self.station["logo"],
            "logosChannels/existing.png",
            "sources/wikimedia/credits.json",
        ]:
            self.assertEqual(applied[name], before[name])
        self.write(REPORT, b"stale report")
        stale = self.snapshot()
        with self.assertRaises(ValueError):
            run(self.root, check=True)
        self.assertEqual(stale, self.snapshot())

    def test_bad_checksum_or_invalid_image_blocks_all_writes(self):
        for target in [self.station, self.assets[0]]:
            with self.subTest(target=target["logo"]):
                valid = (self.root / target["logo"]).read_bytes()
                for invalid_image in [False, True]:
                    data = b"not an image"
                    self.write(target["logo"], data)
                    original_hash = target["logo_sha256"]
                    if invalid_image:
                        target["logo_sha256"] = hashlib.sha256(data).hexdigest()
                    self.save()
                    before = self.snapshot()
                    with self.assertRaises(ValueError):
                        run(self.root, apply=True)
                    self.assertEqual(before, self.snapshot())
                    target["logo_sha256"] = original_hash
                self.write(target["logo"], valid)
                self.save()

    def test_apply_refuses_to_repair_unrelated_tv_image(self):
        self.write("logosChannels/existing.png", logo_bytes(accent="green"))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "cannot replace existing images"):
            run(self.root, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_each_needed_file_is_decoded_once_per_run(self):
        self.save(stations=[self.station, dict(self.station, id="radio.tn.second")])
        with patch(
            "match_radio_logos.image_signature", wraps=image_signature
        ) as decode:
            build_outputs(self.root)
        self.assertEqual(decode.call_count, 2)


class DiscoveryTests(unittest.TestCase):
    def asset(self, **changes):
        asset = {
            "page_id": 123,
            "title": "File:Logo EtoileFM.svg",
            "categories": [FRANCE],
            "logo": "logosChannels/commons-123.png",
            "logo_sha256": "a" * 64,
            "description_url": "https://commons.wikimedia.org/wiki/File:Logo_EtoileFM.svg",
            "credit": '<a href="https://www.etoilefm.fr/">Étoile FM</a>',
            "extmetadata": {"ImageDescription": {"value": "Logo de Étoile FM"}},
            "source_width": 100,
            "source_height": 100,
            "transparency": False,
        }
        asset.update(changes)
        return asset

    def test_explicit_station_description_allows_names_without_fm_or_am(self):
        for prefix in [
            "Logo de la station de radio ",
            "Logo de la radio ",
            "Logo of the radio station ",
        ]:
            with self.subTest(prefix=prefix):
                asset = self.asset(
                    title="File:Logo Bretagne 5.svg",
                    credit='<a href="https://www.bretagne5.fr/">Bretagne 5</a>',
                    extmetadata={"ImageDescription": {"value": prefix + "Bretagne 5."}},
                )
                entries, decisions = discover_radios([], [asset])
                self.assertEqual(entries[0]["id"], "radio.fr.bretagne-5")
                self.assertEqual(entries[0]["name"], "Bretagne 5")
                self.assertEqual(decisions[0]["reason"], "accepted")

    def test_generic_network_description_without_fm_or_am_is_not_a_station(self):
        for prefix in ["Logo de ", "Logo of ", "Logo "]:
            with self.subTest(prefix=prefix):
                asset = self.asset(
                    title="File:Logo Radio France.svg",
                    credit='<a href="https://radiofrance.fr/">Radio France</a>',
                    extmetadata={
                        "ImageDescription": {"value": prefix + "Radio France"}
                    },
                )
                entries, decisions = discover_radios([], [asset])
                self.assertEqual(entries, [])
                self.assertEqual(decisions[0]["reason"], "station_type_unconfirmed")

    def test_explicit_station_still_requires_country_title_and_official_source(self):
        asset = self.asset(
            title="File:Logo Bretagne 5.svg",
            credit='<a href="https://bretagne5.fr/">Bretagne 5</a>',
            extmetadata={"ImageDescription": {"value": "Logo de la radio Bretagne 5"}},
        )
        for changes, reason in [
            ({"categories": [FRANCE, TUNISIA]}, "ambiguous_country"),
            ({"title": "File:Logo Bretagne 6.svg"}, "title_mismatch"),
            (
                {"credit": '<a href="https://other.fr/">Bretagne 5</a>'},
                "no_matching_source_domain",
            ),
        ]:
            with self.subTest(changes=changes):
                entries, decisions = discover_radios([], [dict(asset, **changes)])
                self.assertEqual(entries, [])
                self.assertEqual(decisions[0]["reason"], reason)

    def test_unknown_radio_is_created_from_metadata_without_visual_matching(self):
        asset = self.asset()
        with patch("match_radio_logos.visual_score") as scoring:
            entries, decisions = discover_radios([], [asset])
        self.assertEqual(
            entries,
            [
                {
                    "id": "radio.fr.etoile-fm",
                    "name": "Étoile FM",
                    "type": "radio",
                    "country": "FR",
                    "aliases": [],
                    "page_id": 123,
                    "logo": asset["logo"],
                    "legacy_paths": [],
                    "description_url": asset["description_url"],
                    "discovery": "commons-official-source-v1",
                }
            ],
        )
        self.assertEqual(decisions[0]["reason"], "accepted")
        scoring.assert_not_called()

    def test_unverifiable_identity_is_rejected(self):
        for changes in [
            {"categories": [FRANCE, TUNISIA]},
            {"categories": [FRANCE + " historical"]},
            {"title": "File:Étoile FM logo 2012.svg"},
            {"title": "File:Other FM.svg"},
            {"extmetadata": {"ImageDescription": {"value": "Old logo de Étoile FM"}}},
            {
                "extmetadata": {
                    "ImageDescription": {"value": "Logo de Étoile FM from 2012"}
                }
            },
            {"extmetadata": {"ImageDescription": {"value": "Logo de Étoile"}}},
            {"credit": '<a href="http://etoilefm.fr/">Étoile FM</a>'},
            {"credit": '<a href="https://media.etoilefm.fr/">Étoile FM</a>'},
            {"credit": '<a href="https://etoilefm.tn/">Étoile FM</a>'},
            {"credit": '<a href="https://other.fr/">Étoile FM</a>'},
            {"credit": '<a href="https://etoilefm.fr.evil.test/">Étoile FM</a>'},
            {"credit": '<a href="https://etoilefm.fr@evil.test/">Étoile FM</a>'},
            {"credit": "https://etoilefm.fr/"},
        ]:
            with self.subTest(changes=changes):
                entries, decisions = discover_radios([], [self.asset(**changes)])
                self.assertEqual(entries, [])
                self.assertTrue(all(d["reason"] != "accepted" for d in decisions))

    def test_existing_name_or_alias_blocks_duplicate_for_same_or_unknown_country(self):
        for country in ["FR", None]:
            for labels in [
                {"name": "ETOILE-FM", "aliases": []},
                {"name": "Other", "aliases": ["Étoile FM"]},
            ]:
                station = dict(labels, country=country)
                with self.subTest(station=station):
                    entries, decisions = discover_radios([station], [self.asset()])
                    self.assertEqual(entries, [])
                    self.assertEqual(decisions[0]["reason"], "already_known")
        entries, _ = discover_radios(
            [{"name": "Étoile FM", "aliases": [], "country": "TN"}],
            [self.asset()],
        )
        self.assertEqual(len(entries), 1)

    def test_different_designs_abstain_and_identical_designs_use_source_quality(self):
        first = self.asset(width=10000, height=10000)
        different = self.asset(page_id=124, logo_sha256="b" * 64)
        entries, decisions = discover_radios([], [first, different])
        self.assertEqual(entries, [])
        self.assertTrue(all(d["reason"] == "ambiguous_variants" for d in decisions))
        for extra, expected in [
            ({"source_width": 200}, 124),
            ({"transparency": True}, 124),
            ({}, 123),
        ]:
            second = self.asset(page_id=124, **extra)
            with self.subTest(extra=extra):
                entries, decisions = discover_radios([], [first, second])
                self.assertEqual(entries[0]["page_id"], expected)
                self.assertEqual(
                    (entries, decisions), discover_radios([], [second, first])
                )

    def test_tunisian_am_radio_and_html_description_are_supported(self):
        asset = self.asset(
            title="File:Logo Etoile AM.svg",
            categories=[TUNISIA],
            credit='<a href="https://etoileam.tn/">Étoile AM</a>',
            extmetadata={
                "ImageDescription": {"value": "<p>Logo of <b>Étoile AM</b></p>"}
            },
        )
        entries, _ = discover_radios([], [asset])
        self.assertEqual(entries[0]["id"], "radio.tn.etoile-am")


class CollectionRegressionTests(unittest.TestCase):
    def test_commons_bretagne_5_is_created_from_explicit_station_metadata(self):
        root = Path(__file__).resolve().parents[1]
        _, selections = build_outputs(root)
        entry = next(
            e for e in selections["entries"] if e["id"] == "radio.fr.bretagne-5"
        )
        self.assertEqual(entry["name"], "Bretagne 5")
        self.assertEqual(entry["country"], "FR")
        self.assertEqual(entry["page_id"], 71208971)
        self.assertEqual(entry["discovery"], "commons-official-source-v1")

    def test_commons_cherie_is_created_without_a_radio_manifest_entry(self):
        root = Path(__file__).resolve().parents[1]
        stations = json.loads(
            (root / "sources/radios/manifest.json").read_text("utf-8")
        )["stations"]
        self.assertNotIn("Chérie FM", [station["name"] for station in stations])
        _, selections = build_outputs(root)
        entry = next(
            e for e in selections["entries"] if e["id"] == "radio.fr.cherie-fm"
        )
        self.assertEqual(entry["name"], "Chérie FM")
        self.assertEqual(entry["country"], "FR")
        self.assertEqual(entry["page_id"], 144353906)
        self.assertEqual(entry["discovery"], "commons-official-source-v1")

    def test_imported_homonyms_and_old_designs_are_not_auto_selected(self):
        report, _ = build_outputs(Path(__file__).resolve().parents[1])
        stations = {row["id"]: row for row in report["stations"]}
        for identity in ("ifm", "jawhara-fm", "radio-kelma-fm", "radio-orient"):
            with self.subTest(identity=identity):
                row = stations["radio.tn." + identity]
                self.assertEqual(row["status"], "unmatched")
                self.assertEqual(row["reason"], "low_visual_score")
        self.assertEqual(stations["radio.tn.knooz-fm"]["selected_page_id"], 39444265)


if __name__ == "__main__":
    unittest.main()
