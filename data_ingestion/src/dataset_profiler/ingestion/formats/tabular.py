from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from ..jobs import ResourceFormat


class FormatAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    dtype: str
    nullable: bool


@dataclass(frozen=True)
class TableProfile:
    format: ResourceFormat
    row_count: int
    columns: tuple[ColumnProfile, ...]
    row_groups: int | None = None


@dataclass(frozen=True)
class TableData:
    columns: dict[str, np.ndarray]
    source_rows: np.ndarray


class TabularAdapter:
    def __init__(
        self,
        *,
        max_rows: int = 2_000_000,
        max_columns: int = 10_000,
        max_cell_bytes: int = 1_000_000,
    ):
        self.max_rows = max_rows
        self.max_columns = max_columns
        self.max_cell_bytes = max_cell_bytes

    def inspect(self, path: Path, format_: ResourceFormat) -> TableProfile:
        if format_ in {ResourceFormat.CSV, ResourceFormat.TSV}:
            return self._inspect_delimited(path, format_)
        if format_ is ResourceFormat.PARQUET:
            parquet = pq.ParquetFile(path)
            metadata = parquet.metadata
            if metadata.num_rows > self.max_rows or metadata.num_columns > self.max_columns:
                raise FormatAdapterError("Parquet resource exceeds configured shape limits")
            columns = tuple(
                ColumnProfile(field.name, str(field.type), field.nullable)
                for field in parquet.schema_arrow
            )
            return TableProfile(format_, metadata.num_rows, columns, metadata.num_row_groups)
        raise FormatAdapterError("Tabular adapter received another format")

    def read_columns(
        self,
        path: Path,
        format_: ResourceFormat,
        columns: list[str],
    ) -> TableData:
        if not columns or len(columns) != len(set(columns)):
            raise FormatAdapterError("Projected columns must be unique and non-empty")
        profile = self.inspect(path, format_)
        available = {column.name for column in profile.columns}
        if any(column not in available for column in columns):
            raise FormatAdapterError("Projected column is absent from the resource")
        if format_ in {ResourceFormat.CSV, ResourceFormat.TSV}:
            return self._read_delimited(path, format_, columns)
        table = pq.ParquetFile(path).read(columns=columns, use_threads=False)
        values = {
            name: np.asarray(table[name].to_pylist(), dtype=object)
            for name in columns
        }
        return TableData(values, np.arange(1, table.num_rows + 1, dtype=np.int64))

    def _inspect_delimited(self, path: Path, format_: ResourceFormat) -> TableProfile:
        delimiter = "," if format_ is ResourceFormat.CSV else "\t"
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, delimiter=delimiter, strict=True)
            try:
                header = next(reader)
            except (StopIteration, csv.Error) as exc:
                raise FormatAdapterError("Delimited resource has no valid header") from exc
            self._validate_header(header)
            samples: list[list[str]] = []
            count = 0
            try:
                for row in reader:
                    count += 1
                    if count > self.max_rows:
                        raise FormatAdapterError("Delimited resource exceeds the row limit")
                    self._validate_row(row, len(header))
                    if len(samples) < 1_000:
                        samples.append(row)
            except csv.Error as exc:
                raise FormatAdapterError("Delimited resource contains malformed quoting") from exc
        columns = tuple(
            ColumnProfile(
                name,
                self._infer_dtype([row[index] for row in samples]),
                any(row[index] == "" for row in samples),
            )
            for index, name in enumerate(header)
        )
        return TableProfile(format_, count, columns)

    def _read_delimited(
        self,
        path: Path,
        format_: ResourceFormat,
        columns: list[str],
    ) -> TableData:
        delimiter = "," if format_ is ResourceFormat.CSV else "\t"
        projected: dict[str, list[object]] = {column: [] for column in columns}
        source_rows: list[int] = []
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, delimiter=delimiter, strict=True)
            header = next(reader)
            indices = {column: header.index(column) for column in columns}
            try:
                for source_row, row in enumerate(reader, start=2):
                    if len(source_rows) >= self.max_rows:
                        raise FormatAdapterError("Delimited resource exceeds the row limit")
                    self._validate_row(row, len(header))
                    for column, index in indices.items():
                        projected[column].append(self._parse_scalar(row[index]))
                    source_rows.append(source_row)
            except csv.Error as exc:
                raise FormatAdapterError("Delimited resource contains malformed quoting") from exc
        return TableData(
            {name: np.asarray(values, dtype=object) for name, values in projected.items()},
            np.asarray(source_rows, dtype=np.int64),
        )

    def _validate_header(self, header: list[str]) -> None:
        if not header or len(header) > self.max_columns:
            raise FormatAdapterError("Delimited header exceeds configured column limits")
        if any(not name.strip() or len(name.encode()) > self.max_cell_bytes for name in header):
            raise FormatAdapterError("Delimited header contains an invalid column name")
        if len(header) != len(set(header)):
            raise FormatAdapterError("Delimited header contains duplicate columns")

    def _validate_row(self, row: list[str], width: int) -> None:
        if len(row) != width:
            raise FormatAdapterError("Delimited row width differs from the header")
        if any(len(value.encode()) > self.max_cell_bytes for value in row):
            raise FormatAdapterError("Delimited cell exceeds the configured byte limit")

    @staticmethod
    def _parse_scalar(value: str) -> object:
        if value == "":
            return None
        for parser in (int, float):
            try:
                return parser(value)
            except ValueError:
                pass
        return value

    @classmethod
    def _infer_dtype(cls, values: list[str]) -> str:
        present = [value for value in values if value != ""]
        if not present:
            return "unknown"
        parsed = [cls._parse_scalar(value) for value in present]
        if all(isinstance(value, int) for value in parsed):
            return "int64"
        if all(isinstance(value, (int, float)) for value in parsed):
            return "float64"
        return "string"
