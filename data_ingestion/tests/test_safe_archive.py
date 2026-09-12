import hashlib
import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

import zstandard

from dataset_profiler.ingestion import ArchiveError, SafeArchiveExtractor


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SafeArchiveExtractorTests(unittest.TestCase):
    def test_valid_nested_zip_is_atomic_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "signals.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("batch/signals.csv", "time,joint\n0,1\n")
                archive.writestr("README.md", "recorded signals")
            extractor = SafeArchiveExtractor(root / "cache")

            first = extractor.extract(archive_path, digest(archive_path))
            second = extractor.extract(archive_path, digest(archive_path))

            self.assertEqual(first, second)
            self.assertEqual(
                [file.relative_path for file in first.files],
                ["batch/signals.csv", "README.md"],
            )
            target = root / "cache" / "extracted" / first.directory_key.removeprefix("sha256/")
            self.assertEqual((target / "batch/signals.csv").read_text(), "time,joint\n0,1\n")
            self.assertEqual(list((root / "cache" / "extraction-staging").iterdir()), [])

    def test_zip_traversal_links_and_bombs_fail_without_ready_directory(self) -> None:
        cases: list[tuple[str, str, bytes, int]] = []
        traversal = io.BytesIO()
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("../escape.csv", "bad")
        cases.append(("traversal", "unsafe", traversal.getvalue(), 200))

        linked = io.BytesIO()
        with zipfile.ZipFile(linked, "w") as archive:
            info = zipfile.ZipInfo("signals.csv")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "target")
        cases.append(("links", "links", linked.getvalue(), 200))

        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("large.csv", b"0" * 10_000)
        cases.append(("bomb", "expansion ratio", bomb.getvalue(), 2))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, expected, content, ratio in cases:
                archive_path = root / f"{name}.zip"
                archive_path.write_bytes(content)
                extractor = SafeArchiveExtractor(root / "cache", max_expansion_ratio=ratio)
                with self.assertRaisesRegex(ArchiveError, expected):
                    extractor.extract(archive_path, digest(archive_path))
                target = root / "cache" / "extracted" / digest(archive_path)[:2] / digest(archive_path)
                self.assertFalse(target.exists())

    def test_tar_links_and_special_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "linked.tar"
            with tarfile.open(archive_path, "w") as archive:
                member = tarfile.TarInfo("signals.csv")
                member.type = tarfile.SYMTYPE
                member.linkname = "elsewhere"
                archive.addfile(member)
            extractor = SafeArchiveExtractor(root / "cache")

            with self.assertRaisesRegex(ArchiveError, "special files"):
                extractor.extract(archive_path, digest(archive_path))

    def test_tar_zst_is_bounded_and_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tar_bytes = io.BytesIO()
            content = b"time,joint\n0,1\n"
            with tarfile.open(fileobj=tar_bytes, mode="w") as archive:
                member = tarfile.TarInfo("nested/signals.csv")
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            archive_path = root / "signals.tar.zst"
            archive_path.write_bytes(zstandard.ZstdCompressor().compress(tar_bytes.getvalue()))
            extractor = SafeArchiveExtractor(root / "cache")

            result = extractor.extract(archive_path, digest(archive_path))

            self.assertEqual(result.files[0].relative_path, "nested/signals.csv")
            self.assertEqual(result.files[0].content_sha256, hashlib.sha256(content).hexdigest())


if __name__ == "__main__":
    unittest.main()
