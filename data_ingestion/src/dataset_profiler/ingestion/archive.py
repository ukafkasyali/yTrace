from __future__ import annotations

import hashlib
import json
import os
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import zstandard


class ArchiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtractedFile:
    relative_path: str
    size_bytes: int
    content_sha256: str


@dataclass(frozen=True)
class ExtractionResult:
    content_sha256: str
    directory_key: str
    files: tuple[ExtractedFile, ...]


class SafeArchiveExtractor:
    def __init__(
        self,
        cache_dir: Path,
        *,
        max_files: int = 10_000,
        max_expanded_bytes: int = 100_000_000_000,
        max_member_bytes: int = 25_000_000_000,
        max_expansion_ratio: int = 200,
        max_path_length: int = 1_024,
        max_depth: int = 20,
    ):
        self.cache_dir = cache_dir.resolve()
        self.extracted_dir = self.cache_dir / "extracted"
        self.staging_dir = self.cache_dir / "extraction-staging"
        self.extracted_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files
        self.max_expanded_bytes = max_expanded_bytes
        self.max_member_bytes = max_member_bytes
        self.max_expansion_ratio = max_expansion_ratio
        self.max_path_length = max_path_length
        self.max_depth = max_depth

    def extract(self, archive_path: Path, content_sha256: str) -> ExtractionResult:
        archive_path = archive_path.resolve(strict=True)
        if not archive_path.is_file() or self._sha256(archive_path) != content_sha256:
            raise ArchiveError("Archive content does not match its verified cache identity")
        target = self.extracted_dir / content_sha256[:2] / content_sha256
        reused = self._load_existing(target, content_sha256)
        if reused is not None:
            return reused
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.staging_dir) as temporary:
            root = Path(temporary) / "files"
            root.mkdir()
            if zipfile.is_zipfile(archive_path):
                files = self._extract_zip(archive_path, root)
            elif self._magic(archive_path) == b"\x28\xb5\x2f\xfd":
                files = self._extract_tar_zst(archive_path, root, Path(temporary))
            elif tarfile.is_tarfile(archive_path):
                files = self._extract_tar(archive_path, root)
            else:
                raise ArchiveError("Verified content is not a supported archive")
            receipt = {
                "schemaVersion": "1.0",
                "contentSha256": content_sha256,
                "files": [file.__dict__ for file in files],
            }
            (root / ".trace-extraction.json").write_text(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            if target.exists():
                reused = self._load_existing(target, content_sha256)
                if reused is None:
                    raise ArchiveError("Existing extraction cache failed integrity validation")
                return reused
            os.replace(root, target)
        return ExtractionResult(
            content_sha256=content_sha256,
            directory_key=f"sha256/{content_sha256[:2]}/{content_sha256}",
            files=files,
        )

    def _extract_zip(self, archive_path: Path, root: Path) -> tuple[ExtractedFile, ...]:
        extracted: list[ExtractedFile] = []
        total = 0
        seen: set[str] = set()
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                relative = self._relative_path(member.filename)
                if relative in seen:
                    raise ArchiveError("Archive contains duplicate paths")
                seen.add(relative)
                mode = member.external_attr >> 16
                if member.flag_bits & 0x1:
                    raise ArchiveError("Encrypted archive members are unsupported")
                if stat.S_ISLNK(mode):
                    raise ArchiveError("Archive links are not allowed")
                if member.is_dir():
                    (root / relative).mkdir(parents=True, exist_ok=True)
                    continue
                total = self._check_limits(len(extracted), total, member.file_size, archive_path)
                with archive.open(member) as source:
                    extracted.append(self._write_member(root, relative, source, member.file_size))
        return tuple(extracted)

    def _extract_tar(self, archive_path: Path, root: Path) -> tuple[ExtractedFile, ...]:
        with tarfile.open(archive_path, mode="r:*") as archive:
            return self._extract_tar_members(archive, archive_path, root)

    def _extract_tar_zst(
        self,
        archive_path: Path,
        root: Path,
        temporary: Path,
    ) -> tuple[ExtractedFile, ...]:
        tar_path = temporary / "expanded.tar"
        limit = self.max_expanded_bytes + self.max_files * 2_048 + 1_000_000
        written = 0
        try:
            with (
                archive_path.open("rb") as compressed,
                tar_path.open("xb") as output,
                zstandard.ZstdDecompressor().stream_reader(compressed) as reader,
            ):
                while chunk := reader.read(1024 * 1024):
                    written += len(chunk)
                    if written > limit:
                        raise ArchiveError("Compressed archive exceeds expansion limits")
                    output.write(chunk)
        except zstandard.ZstdError as exc:
            raise ArchiveError("Zstandard archive is invalid") from exc
        with tarfile.open(tar_path, mode="r:") as archive:
            return self._extract_tar_members(archive, archive_path, root)

    def _extract_tar_members(
        self,
        archive: tarfile.TarFile,
        source_path: Path,
        root: Path,
    ) -> tuple[ExtractedFile, ...]:
        extracted: list[ExtractedFile] = []
        total = 0
        seen: set[str] = set()
        for member in archive:
            relative = self._relative_path(member.name)
            if relative in seen:
                raise ArchiveError("Archive contains duplicate paths")
            seen.add(relative)
            if member.isdir():
                (root / relative).mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ArchiveError("Archive links and special files are not allowed")
            total = self._check_limits(len(extracted), total, member.size, source_path)
            source = archive.extractfile(member)
            if source is None:
                raise ArchiveError("Archive member could not be read")
            with source:
                extracted.append(self._write_member(root, relative, source, member.size))
        return tuple(extracted)

    def _check_limits(self, count: int, total: int, size: int, archive_path: Path) -> int:
        if count >= self.max_files:
            raise ArchiveError("Archive contains too many files")
        if size < 0 or size > self.max_member_bytes:
            raise ArchiveError("Archive member exceeds the configured size limit")
        total += size
        if total > self.max_expanded_bytes:
            raise ArchiveError("Archive exceeds the configured expanded-size limit")
        compressed_size = max(archive_path.stat().st_size, 1)
        if total > compressed_size * self.max_expansion_ratio:
            raise ArchiveError("Archive exceeds the configured expansion ratio")
        return total

    def _relative_path(self, raw: str) -> str:
        if "\x00" in raw or "\\" in raw or len(raw) > self.max_path_length:
            raise ArchiveError("Archive member path is unsafe")
        path = PurePosixPath(raw)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ArchiveError("Archive member path is unsafe")
        if len(path.parts) > self.max_depth:
            raise ArchiveError("Archive member path is too deeply nested")
        return path.as_posix()

    @staticmethod
    def _write_member(root: Path, relative: str, source, expected_size: int) -> ExtractedFile:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        written = 0
        with destination.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > expected_size:
                    raise ArchiveError("Archive member exceeded its declared size")
                output.write(chunk)
                digest.update(chunk)
        if written != expected_size:
            raise ArchiveError("Archive member was truncated")
        return ExtractedFile(relative, written, digest.hexdigest())

    def _load_existing(self, target: Path, content_sha256: str) -> ExtractionResult | None:
        marker = target / ".trace-extraction.json"
        if not marker.is_file():
            return None
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if payload.get("contentSha256") != content_sha256:
                return None
            files = tuple(ExtractedFile(**item) for item in payload["files"])
            for file in files:
                path = target / file.relative_path
                if (
                    not path.is_file()
                    or path.stat().st_size != file.size_bytes
                    or self._sha256(path) != file.content_sha256
                ):
                    return None
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        return ExtractionResult(
            content_sha256,
            f"sha256/{content_sha256[:2]}/{content_sha256}",
            files,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _magic(path: Path) -> bytes:
        with path.open("rb") as source:
            return source.read(4)
