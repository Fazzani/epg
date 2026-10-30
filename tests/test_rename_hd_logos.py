import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rename_hd_logos import rename_logos


class RenameHdLogosTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "logosChannels").mkdir()
        self.manifest_path = self.root / "logo_manifest.json"
        self.manifest_path.write_text(
            json.dumps(
                {
                    "entries": [],
                    "preferred_images": {"duplicationhd": "duplicationhd.png"},
                }
            ),
            encoding="utf-8",
        )

    def test_rename_delete_and_update_manifest_without_sources(self):
        folder = self.root / "logosChannels"
        for name, content in {
            "uniqueHD.png": b"unique",
            "duplicationhd.png": b"old",
            "duplication.png": b"keep",
            "wiki.png": b"wiki",
            "wikihd.png": b"wiki",
            "notesHD.txt": b"text",
        }.items():
            (folder / name).write_bytes(content)
        entries = [
            {
                "id": identity,
                "logo": f"logosChannels/{filename}",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for identity, filename, content in [
                ("tv.xx.unique", "uniqueHD.png", b"unique"),
                ("tv.xx.wiki", "wikihd.png", b"wiki"),
            ]
        ]
        self.manifest_path.write_text(
            json.dumps(
                {
                    "entries": entries,
                    "preferred_images": {"duplicationhd": "duplicationhd.png"},
                }
            ),
            encoding="utf-8",
        )
        with patch("rename_hd_logos.update_catalog.run") as update:
            self.assertEqual(rename_logos(self.root), (1, 2))
        update.assert_called_once_with(self.root)
        self.assertEqual((folder / "unique.png").read_bytes(), b"unique")
        self.assertEqual((folder / "duplication.png").read_bytes(), b"keep")
        self.assertFalse((folder / "duplicationhd.png").exists())
        self.assertTrue((folder / "notesHD.txt").exists())
        manifest = json.loads(self.manifest_path.read_text())
        self.assertEqual(manifest["preferred_images"], {})
        self.assertEqual(
            [entry["logo"] for entry in manifest["entries"]],
            ["logosChannels/unique.png", "logosChannels/wiki.png"],
        )
        self.assertEqual(
            [entry["sha256"] for entry in manifest["entries"]],
            [entry["sha256"] for entry in entries],
        )
        self.assertFalse((self.root / "sources").exists())

    def test_conflicting_catalog_destination_aborts_before_changes(self):
        folder = self.root / "logosChannels"
        (folder / "wikihd.png").write_bytes(b"selected")
        (folder / "wiki.png").write_bytes(b"different")
        (folder / "otherhd.png").write_bytes(b"other")
        self.manifest_path.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "logo": "logosChannels/wikihd.png",
                            "sha256": hashlib.sha256(b"selected").hexdigest(),
                        }
                    ],
                    "preferred_images": {},
                }
            ),
            encoding="utf-8",
        )
        before = self.manifest_path.read_bytes()
        with patch("rename_hd_logos.update_catalog.run") as update:
            with self.assertRaisesRegex(ValueError, "different content"):
                rename_logos(self.root)
        update.assert_not_called()
        self.assertEqual((folder / "wikihd.png").read_bytes(), b"selected")
        self.assertEqual((folder / "wiki.png").read_bytes(), b"different")
        self.assertTrue((folder / "otherhd.png").exists())
        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_nested_hd_names_abort_before_changes(self):
        folder = self.root / "logosChannels"
        (folder / "logohdhd.png").write_bytes(b"first")
        (folder / "logohd.png").write_bytes(b"second")
        with self.assertRaisesRegex(ValueError, "still end in HD"):
            rename_logos(self.root)
        self.assertEqual((folder / "logohdhd.png").read_bytes(), b"first")
        self.assertEqual((folder / "logohd.png").read_bytes(), b"second")


if __name__ == "__main__":
    unittest.main()
