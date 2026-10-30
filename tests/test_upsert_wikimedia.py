import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from upsert_wikimedia import prepare_upserts


class UpsertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "sources/wikimedia").mkdir(parents=True)
        (self.root / "logosChannels").mkdir()
        self.png = b"already validated Wikimedia PNG"
        self.asset = {
            "page_id": 42,
            "logo": "logosChannels/commons-42.png",
            "logo_sha256": hashlib.sha256(self.png).hexdigest(),
        }
        self.entry = {
            "page_id": 42,
            "id": "tv.tn.example",
            "name": "Example",
            "type": "tv",
            "country": "TN",
            "aliases": [],
            "logo": "logosChannels/example.png",
            "legacy_paths": ["logosChannels/example-hd.png"],
        }

    def prepare(self, entries=None):
        path = self.root / "sources/wikimedia/selections.json"
        path.write_text(
            json.dumps({"entries": [self.entry] if entries is None else entries})
        )
        return prepare_upserts(
            self.root, {"assets": [self.asset]}, {self.asset["logo"]: self.png}
        )

    def publish(self, files):
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def test_upsert_retains_paths_backs_up_originals_and_is_idempotent(self):
        canonical = self.root / self.entry["logo"]
        canonical.write_bytes(b"previous original")
        files = self.prepare()
        self.assertEqual(canonical.read_bytes(), b"previous original")
        self.assertEqual(files[self.entry["logo"]], self.png)
        self.assertEqual(files[self.entry["legacy_paths"][0]], self.png)
        self.publish(files)
        document = json.loads(files["sources/wikimedia/upserts.json"])
        record = next(
            row for row in document["upserts"] if row["logo"] == self.entry["logo"]
        )
        self.assertEqual(
            (self.root / record["previous_logo"]).read_bytes(), b"previous original"
        )
        repeated = self.prepare()
        self.assertEqual(
            repeated["sources/wikimedia/upserts.json"],
            files["sources/wikimedia/upserts.json"],
        )
        self.assertFalse(any("legacy-originals/" in key for key in repeated))

    def test_new_revision_replaces_existing_target_and_preserves_old_bytes(self):
        self.publish(self.prepare())
        old = self.png
        self.png = b"new Wikimedia revision PNG"
        self.asset["logo_sha256"] = hashlib.sha256(self.png).hexdigest()
        files = self.prepare()
        record = next(
            row
            for row in json.loads(files["sources/wikimedia/upserts.json"])["upserts"]
            if row["logo"] == self.entry["logo"]
        )
        self.assertEqual(files[record["previous_logo"]], old)
        self.assertEqual(files[self.entry["logo"]], self.png)

    def test_unsafe_or_ambiguous_selection_publishes_nothing(self):
        for target in [
            "../escape.png",
            "logosChannels/radio-tn-example.png",
            "logosChannels/commons-99.png",
        ]:
            with self.subTest(target=target):
                self.entry["logo"] = target
                with self.assertRaises(ValueError):
                    self.prepare()
        self.entry["logo"] = "logosChannels/example.png"
        other = dict(self.entry, id="tv.tn.other")
        with self.assertRaisesRegex(ValueError, "shared"):
            self.prepare([self.entry, other])
        self.assertEqual(list((self.root / "logosChannels").iterdir()), [])

    def test_wrong_source_hash_cannot_replace_existing_image(self):
        target = self.root / self.entry["logo"]
        target.write_bytes(b"original")
        self.asset["logo_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.prepare()
        self.assertEqual(target.read_bytes(), b"original")

    def test_removed_selection_retires_provenance_without_deleting_images(self):
        self.publish(self.prepare())
        target = self.root / self.entry["logo"]
        before = target.read_bytes()
        removed = self.prepare(entries=[])
        provenance = json.loads(removed["sources/wikimedia/upserts.json"])
        self.assertEqual(provenance["upserts"], [])
        self.assertEqual(len(provenance["retired_upserts"]), 2)
        self.assertEqual(target.read_bytes(), before)
        self.publish(removed)
        self.assertEqual(self.prepare(entries=[]), removed)


if __name__ == "__main__":
    unittest.main()
