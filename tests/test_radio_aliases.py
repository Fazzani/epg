import copy
import json
from pathlib import Path
import unittest

from update_index import radio_name_aliases


class RadioAliasTests(unittest.TestCase):
    def station(self, name, identity="radio.tn.example", **changes):
        station = {
            "id": identity,
            "name": name,
            "type": "radio",
            "country": None,
            "aliases": [],
            "logo": "logosChannels/example.png",
            "page_url": "https://example.test/unrelated",
        }
        station.update(changes)
        return station

    def test_decimal_fm_frequency_is_removed_only_in_broadcast_range(self):
        for frequency in ["65.0", "100.6", "100,6 MHz", "108.0"]:
            with self.subTest(frequency=frequency):
                entries, audit = radio_name_aliases([self.station("IFM " + frequency)])
                self.assertEqual(entries[0]["aliases"], ["IFM"])
                self.assertEqual(
                    audit[0]["added"], [{"alias": "IFM", "reason": "fm_frequency"}]
                )
        for name in ["IFM 64.9", "IFM 108.1", "IFM 100", "Radio 24", "Bretagne 5"]:
            with self.subTest(name=name):
                entries, audit = radio_name_aliases([self.station(name)])
                self.assertEqual(entries[0]["aliases"], [])
                self.assertEqual(audit, [])

    def test_bilingual_names_retain_region_and_web_stream_qualifiers(self):
        for name, expected in [
            ("Radio Gafsa إذاعة قفصة", "Radio Gafsa"),
            ("Radio Tunis Nationale الإذاعة الوطنية التونسية", "Radio Tunis Nationale"),
            ("Zitouna FM إذاعة الزيتونة", "Zitouna FM"),
            ("Radio Quran - إذاعة القرآن", "Radio Quran"),
            ("IFM Rire & Chansons إذاعة", "IFM Rire & Chansons"),
        ]:
            with self.subTest(name=name):
                entries, audit = radio_name_aliases([self.station(name)])
                self.assertEqual(entries[0]["aliases"], [expected])
                self.assertEqual(audit[0]["added"][0]["reason"], "bilingual_name")
        for name in [
            "Radio Gafsa",
            "IFM Rire & Chansons",
            "Étoile FM Web",
            "Étoile 2",
            "إذاعة تونس الوطنية",
        ]:
            with self.subTest(name=name):
                self.assertEqual(radio_name_aliases([self.station(name)])[1], [])

    def test_radio_prefix_requires_matching_source_slug(self):
        for slug, expected in [
            ("diwan-fm", ["Diwan FM"]),
            ("radio-diwan-fm", []),
            ("diwan", []),
            ("unrelated", []),
        ]:
            with self.subTest(slug=slug):
                entries, _ = radio_name_aliases(
                    [
                        self.station(
                            "Radio Diwan FM", page_url="https://example.test/" + slug
                        )
                    ]
                )
                self.assertEqual(entries[0]["aliases"], expected)

    def test_collision_with_name_alias_or_other_proposal_abstains(self):
        for second in [
            self.station("IFM", "radio.fr.other", country="FR"),
            self.station("Other", "radio.tn.other", aliases=["i-fm"]),
            self.station("IFM 99.0", "radio.tn.other"),
        ]:
            with self.subTest(second=second):
                stations = [self.station("IFM 100.6"), second]
                entries, audit = radio_name_aliases(stations)
                first = next(e for e in entries if e["id"] == stations[0]["id"])
                self.assertEqual(first["aliases"], [])
                decision = next(e for e in audit if e["id"] == first["id"])
                self.assertEqual(decision["added"], [])
                self.assertEqual(
                    decision["rejected"], [{"alias": "IFM", "reason": "ambiguous_name"}]
                )
                self.assertEqual(
                    (entries, audit), radio_name_aliases(list(reversed(stations)))
                )

    def test_enrichment_preserves_source_identity_and_does_not_mutate_inputs(self):
        stations = [
            self.station("IFM 100.6", aliases=["Ancien nom"], logo_sha256="a" * 64),
            self.station("Radio Gafsa إذاعة قفصة", "radio.tn.gafsa"),
        ]
        before = copy.deepcopy(stations)
        entries, audit = radio_name_aliases(stations)
        self.assertEqual(stations, before)
        for original in stations:
            enriched = next(e for e in entries if e["id"] == original["id"])
            self.assertEqual(
                {k: v for k, v in enriched.items() if k != "aliases"},
                {k: v for k, v in original.items() if k != "aliases"},
            )
            self.assertEqual(
                enriched["aliases"][: len(original["aliases"])], original["aliases"]
            )
            self.assertIsNot(enriched["aliases"], original["aliases"])
        self.assertEqual((entries, audit), radio_name_aliases(list(reversed(stations))))
        self.assertEqual(radio_name_aliases(entries), (entries, []))

    def test_existing_spelling_alias_is_not_duplicated(self):
        station = self.station("IFM 100.6", aliases=["i-fm"])
        self.assertEqual(radio_name_aliases([station]), ([station], []))

    def test_collection_names_receive_only_supported_aliases_without_country_inference(
        self,
    ):
        root = Path(__file__).resolve().parents[1]
        stations = json.loads(
            (root / "sources/radios/manifest.json").read_text("utf-8")
        )["stations"]
        enriched, _ = radio_name_aliases(stations)
        by_name = {station["name"]: station for station in enriched}
        for name, alias in [
            ("IFM 100.6", "IFM"),
            ("Radio Gafsa إذاعة قفصة", "Radio Gafsa"),
            ("Zitouna FM إذاعة الزيتونة", "Zitouna FM"),
        ]:
            with self.subTest(name=name):
                self.assertIn(alias, by_name[name]["aliases"])
                self.assertIsNone(by_name[name]["country"])
        self.assertNotIn("IFM", by_name["IFM Rire & Chansons"]["aliases"])


if __name__ == "__main__":
    unittest.main()
