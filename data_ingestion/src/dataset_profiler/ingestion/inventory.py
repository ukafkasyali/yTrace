from __future__ import annotations

import csv
import hashlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .formats.arrays import NumericArrayAdapter
from .formats.tabular import FormatAdapterError, TabularAdapter
from .jobs import ResourceFormat, ResourceProfile


class InventoryError(RuntimeError):
    pass


class ResourceInventory:
    def __init__(
        self,
        *,
        max_file_bytes: int = 25_000_000_000,
        max_probe_bytes: int = 65_536,
        max_columns: int = 10_000,
        tabular: TabularAdapter | None = None,
        arrays: NumericArrayAdapter | None = None,
    ):
        self.max_file_bytes = max_file_bytes
        self.max_probe_bytes = max_probe_bytes
        self.max_columns = max_columns
        self.tabular = tabular or TabularAdapter(max_columns=max_columns)
        self.arrays = arrays or NumericArrayAdapter()

    def inspect(
        self,
        *,
        ingestion_id: str,
        asset_id: str,
        logical_path: str,
        path: Path,
    ) -> ResourceProfile:
        source = path.resolve(strict=True)
        if not source.is_file():
            raise InventoryError("Resource is not a regular file")
        size = source.stat().st_size
        if size > self.max_file_bytes:
            raise InventoryError("Resource exceeds the configured inventory size limit")
        content_sha256 = self._sha256(source)
        format_, details = self._probe(source, logical_path, size)
        if format_ is not ResourceFormat.UNSUPPORTED:
            try:
                details |= self._schema_details(source, format_)
            except FormatAdapterError as exc:
                format_ = ResourceFormat.UNSUPPORTED
                details = {"reason": "FORMAT_SCHEMA_INVALID", "message": str(exc)}
        material = f"{ingestion_id}|{asset_id}|{logical_path}|{content_sha256}"
        return ResourceProfile(
            resource_id=f"res_{hashlib.sha256(material.encode()).hexdigest()[:24]}",
            ingestion_id=ingestion_id,
            asset_id=asset_id,
            logical_path=logical_path,
            size_bytes=size,
            content_sha256=content_sha256,
            format=format_,
            details=details,
            inspected_at=datetime.now(UTC),
        )

    def _schema_details(self, path: Path, format_: ResourceFormat) -> dict:
        if format_ in {ResourceFormat.CSV, ResourceFormat.TSV, ResourceFormat.PARQUET}:
            profile = self.tabular.inspect(path, format_)
            return {
                "rowCount": profile.row_count,
                "rowGroups": profile.row_groups,
                "columns": [
                    {
                        "name": column.name,
                        "dtype": column.dtype,
                        "nullable": column.nullable,
                    }
                    for column in profile.columns
                ],
            }
        profile = self.arrays.inspect(path, format_)
        return {
            "arrays": [
                {
                    "name": array.name,
                    "shape": list(array.shape),
                    "dtype": array.dtype,
                    "sizeBytes": array.size_bytes,
                }
                for array in profile.arrays
            ]
        }

    def _probe(
        self,
        path: Path,
        logical_path: str,
        size: int,
    ) -> tuple[ResourceFormat, dict]:
        extension = self._extension(logical_path)
        with path.open("rb") as source:
            prefix = source.read(min(size, self.max_probe_bytes))
            suffix = b""
            if size >= 4:
                source.seek(-4, 2)
                suffix = source.read(4)
        magic_format = self._magic_format(prefix, suffix)
        if magic_format is not None:
            if extension not in self._extensions_for(magic_format):
                return ResourceFormat.UNSUPPORTED, {
                    "reason": "EXTENSION_MAGIC_MISMATCH",
                    "detectedFormat": magic_format.value,
                    "extension": extension,
                }
            return magic_format, {"probeBytes": len(prefix)}
        if extension in {".csv", ".tsv"}:
            return self._probe_delimited(prefix, extension)
        return ResourceFormat.UNSUPPORTED, {
            "reason": "UNSUPPORTED_FORMAT",
            "extension": extension,
        }

    def _probe_delimited(
        self,
        prefix: bytes,
        extension: str,
    ) -> tuple[ResourceFormat, dict]:
        try:
            text = prefix.decode("utf-8-sig")
        except UnicodeDecodeError:
            return ResourceFormat.UNSUPPORTED, {"reason": "DELIMITED_TEXT_NOT_UTF8"}
        lines = [line for line in text.splitlines() if line.strip()][:20]
        if len(lines) < 2:
            return ResourceFormat.UNSUPPORTED, {"reason": "DELIMITED_TEXT_TOO_SHORT"}
        expected = "," if extension == ".csv" else "\t"
        try:
            dialect = csv.Sniffer().sniff("\n".join(lines), delimiters=",\t;|")
            rows = list(csv.reader(lines, dialect))
        except csv.Error:
            return ResourceFormat.UNSUPPORTED, {"reason": "DELIMITED_DIALECT_INVALID"}
        widths = {len(row) for row in rows}
        if dialect.delimiter != expected:
            return ResourceFormat.UNSUPPORTED, {
                "reason": "EXTENSION_DIALECT_MISMATCH",
                "detectedDelimiter": dialect.delimiter,
            }
        if len(widths) != 1 or not widths or next(iter(widths)) > self.max_columns:
            return ResourceFormat.UNSUPPORTED, {"reason": "DELIMITED_ROW_WIDTH_INVALID"}
        width = next(iter(widths))
        if width < 2:
            return ResourceFormat.UNSUPPORTED, {"reason": "DELIMITED_SINGLE_COLUMN"}
        format_ = ResourceFormat.CSV if extension == ".csv" else ResourceFormat.TSV
        return format_, {
            "delimiter": dialect.delimiter,
            "sampledRows": len(rows),
            "columnCount": width,
            "header": rows[0][: min(width, 100)],
        }

    @staticmethod
    def _magic_format(prefix: bytes, suffix: bytes) -> ResourceFormat | None:
        if prefix.startswith(b"PAR1") and suffix == b"PAR1":
            return ResourceFormat.PARQUET
        if prefix.startswith(b"\x93NUMPY"):
            return ResourceFormat.NPY
        if prefix.startswith(b"PK\x03\x04"):
            return ResourceFormat.NPZ
        if prefix.startswith(b"MATLAB 5.0 MAT-file"):
            return ResourceFormat.MAT
        if any(prefix[offset : offset + 8] == b"\x89HDF\r\n\x1a\n" for offset in (0, 512, 1024, 2048)):
            return ResourceFormat.HDF5
        return None

    @staticmethod
    def _extensions_for(format_: ResourceFormat) -> set[str]:
        return {
            ResourceFormat.PARQUET: {".parquet"},
            ResourceFormat.NPY: {".npy"},
            ResourceFormat.NPZ: {".npz"},
            ResourceFormat.MAT: {".mat"},
            ResourceFormat.HDF5: {".h5", ".hdf5", ".mat"},
        }[format_]

    @staticmethod
    def _extension(logical_path: str) -> str:
        return PurePosixPath(logical_path.casefold()).suffix

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
