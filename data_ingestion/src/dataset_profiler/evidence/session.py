"""Profile-first, budgeted evidence queries with an optional bounded source excerpt."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np

from ..io.hdf5 import read_hdf5_excerpt
from ..io.matlab import load_matlab, sha256_file
from ..models import ChannelStats, DatasetProfile, VariableProfile
from .models import (
    Evidence,
    EvidenceBudget,
    DocumentationSearchResponse,
    DocumentationSource,
    EvidenceError,
    EvidenceErrorCode,
    EvidenceKind,
    EvidenceLimits,
    EvidenceResponse,
    EvidenceUsage,
)


_DOCUMENTATION_SUFFIXES = {".md": "markdown", ".txt": "text"}
_SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class _LoadedDocumentation:
    source_id: str
    display_name: str
    document_type: str
    status: str
    size_bytes: int | None = None
    line_count: int | None = None
    sha256: str | None = None
    text: str | None = None
    error_code: EvidenceErrorCode | None = None


class EvidenceSession:
    """Expose only bounded, deterministic evidence to a future semantic agent.

    Source excerpts are disabled unless the trusted host opts in at session creation. Even then,
    callers identify only profiled runs and variables; source paths are resolved internally.
    """

    def __init__(
        self,
        profile: DatasetProfile,
        *,
        limits: EvidenceLimits | None = None,
        budget: EvidenceBudget | None = None,
        allow_bounded_source_excerpts: bool = False,
        documentation_sources: list[DocumentationSource] | tuple[DocumentationSource, ...] | None = None,
    ) -> None:
        self._profile = profile
        self.limits = limits or EvidenceLimits()
        self.budget = budget or EvidenceBudget()
        self._validate_configuration()
        self._allow_excerpts = allow_bounded_source_excerpts
        self._total_queries = 0
        self._excerpt_queries = 0
        self._excerpt_values = 0
        self._documentation_queries = 0
        self._documentation_chars = 0
        self._profile_fingerprint = hashlib.sha256(profile.to_json().encode()).hexdigest()[:16]
        self._registered_documentation_count = len(documentation_sources or ())
        self._documentation = self._load_documentation(documentation_sources or ())

    @property
    def usage(self) -> EvidenceUsage:
        return EvidenceUsage(
            self._total_queries,
            self._excerpt_queries,
            self._excerpt_values,
            self._documentation_queries,
            self._documentation_chars,
        )

    def documentation_sources(self) -> EvidenceResponse:
        """Describe explicitly registered documents without exposing their paths or contents."""
        kind = EvidenceKind.DOCUMENTATION_SOURCES
        if error := self._begin(kind):
            return error
        items = []
        for source in self._documentation:
            item: dict[str, Any] = {
                "source_id": source.source_id,
                "display_name": source.display_name,
                "document_type": source.document_type,
                "status": source.status,
            }
            if source.size_bytes is not None:
                item["size_bytes"] = source.size_bytes
            if source.line_count is not None:
                item["line_count"] = source.line_count
            if source.sha256 is not None:
                item["sha256"] = source.sha256
            if source.error_code is not None:
                item["error_code"] = source.error_code.value
            items.append(item)
        value = {
            "items": items,
            "returned": len(items),
            "registered": self._registered_documentation_count,
            "truncated": len(items) < self._registered_documentation_count,
        }
        return self._evidence(kind, "documentation_registry", "dataset", None, value)

    def documentation_search(
        self,
        query: str,
        *,
        max_results: int = 3,
    ) -> DocumentationSearchResponse:
        """Return bounded literal/token matches from host-registered text documents."""
        kind = EvidenceKind.DOCUMENTATION
        if error := self._begin(kind):
            return error
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            return self._error(
                EvidenceErrorCode.INVALID_QUERY,
                kind,
                "query must be a non-empty string of at most 200 characters",
            )
        if (isinstance(max_results, bool) or not isinstance(max_results, int)
                or max_results <= 0):
            return self._error(
                EvidenceErrorCode.INVALID_QUERY,
                kind,
                "max_results must be a positive integer",
            )
        if self._documentation_queries >= self.budget.max_documentation_queries:
            return self._error(
                EvidenceErrorCode.QUERY_BUDGET_EXCEEDED,
                kind,
                "The session's documentation-query budget is exhausted.",
                limit=self.budget.max_documentation_queries,
                used=self._documentation_queries,
            )
        if self._documentation_chars >= self.budget.max_total_documentation_chars:
            return self._error(
                EvidenceErrorCode.DOCUMENTATION_BUDGET_EXCEEDED,
                kind,
                "The session's documentation-character budget is exhausted.",
                limit=self.budget.max_total_documentation_chars,
                used=self._documentation_chars,
            )
        self._documentation_queries += 1
        available = [source for source in self._documentation if source.text is not None]
        if not available:
            statuses = {source.status for source in self._documentation}
            if statuses == {"unsupported"}:
                code = EvidenceErrorCode.UNSUPPORTED_DOCUMENTATION
            elif statuses == {"invalid"}:
                code = EvidenceErrorCode.INVALID_DOCUMENTATION_SOURCE
            else:
                code = EvidenceErrorCode.UNAVAILABLE_DOCUMENTATION
            return self._error(
                code,
                kind,
                "No readable, supported documentation is available for this session.",
                registered=self._registered_documentation_count,
            )

        cleaned_query = " ".join(query.strip().split())
        normalized_query = cleaned_query.casefold()
        query_tokens = re.findall(r"\w+", normalized_query)
        candidates: list[tuple[int, str, int, _LoadedDocumentation, int, int, str, bool]] = []
        for source in available:
            assert source.text is not None
            lines = source.text.splitlines()
            for index, line in enumerate(lines):
                rank = self._documentation_match_rank(line, cleaned_query,
                                                      normalized_query, query_tokens)
                if rank is None:
                    continue
                start = max(0, index - 1)
                end = min(len(lines), index + 2)
                raw_excerpt = "\n".join(lines[start:end])
                excerpt, truncated = self._bounded_excerpt_text(
                    raw_excerpt,
                    line,
                    cleaned_query,
                    self.limits.max_documentation_excerpt_chars,
                )
                candidates.append((rank, source.source_id, index, source, start, end,
                                   excerpt, truncated))

        candidates.sort(key=lambda item: (item[0], item[1].casefold(), item[2]))
        result_limit = min(max_results, self.limits.max_documentation_results)
        results: list[Evidence] = []
        returned_chars = 0
        seen_locations: set[tuple[str, int, int]] = set()
        for rank, _, index, source, start, end, excerpt, truncated in candidates:
            location = (source.source_id, start, end)
            if location in seen_locations:
                continue
            remaining_query_chars = self.limits.max_documentation_response_chars - returned_chars
            remaining_session_chars = (
                self.budget.max_total_documentation_chars
                - self._documentation_chars
                - returned_chars
            )
            allowance = min(remaining_query_chars, remaining_session_chars)
            if allowance <= 0:
                break
            if len(excerpt) > allowance:
                excerpt = excerpt[:allowance].rstrip() + ("…" if allowance > 1 else "")
                excerpt = excerpt[:allowance]
                truncated = True
            value = {
                "query": cleaned_query,
                "source_id": source.source_id,
                "display_name": source.display_name,
                "document_type": source.document_type,
                "excerpt": excerpt,
                "line_start": start + 1,
                "line_end": end,
                "match_line": index + 1,
                "excerpt_truncated": truncated,
            }
            evidence = self._evidence(
                kind,
                source.source_id,
                "documentation",
                cleaned_query,
                value,
                query={"query": normalized_query, "source_sha256": source.sha256,
                       "line_start": start + 1, "line_end": end},
                metadata={"document_sha256": source.sha256},
            )
            if isinstance(evidence, EvidenceError):
                return evidence
            results.append(evidence)
            seen_locations.add(location)
            returned_chars += len(excerpt)
            if len(results) >= result_limit:
                break

        self._documentation_chars += returned_chars
        return results

    def dataset_summary(self) -> EvidenceResponse:
        kind = EvidenceKind.DATASET_SUMMARY
        if error := self._begin(kind):
            return error
        variables = sorted(self._profile.observed_structure.get("variable_schema", {}))
        variable_page = self._bounded_list(variables, self.limits.max_metadata_entries)
        rates = sorted({run.sampling_rate_hz for run in self._profile.runs
                        if run.sampling_rate_hz is not None})
        lengths = sorted({run.sequence_length for run in self._profile.runs
                          if run.sequence_length is not None})
        schema = self._profile.observed_structure.get("variable_schema", {})
        invalid = {
            "nan_count": sum(variable.nan_count for file in self._profile.files
                             for variable in file.variables),
            "inf_count": sum(variable.inf_count for file in self._profile.files
                             for variable in file.variables),
        }
        unknowns = self._bounded_list(self._profile.unknowns, self.limits.max_metadata_entries)
        value = {
            "profile_schema_version": self._profile.schema_version,
            "dataset_id": self._profile.dataset_id,
            "source_subsets": [self._profile.source.get("scope")]
            if self._profile.source.get("scope") else [],
            "run_count": len(self._profile.runs),
            "variables": variable_page,
            "file_formats": dict(self._profile.discovery.get("file_formats")
                                 or self._profile.discovery.get("matlab_formats", {})),
            "sampling_rates_hz": rates,
            "sampling_rate_consistent": len(rates) <= 1 and len(rates) > 0,
            "sequence_lengths": lengths,
            "sequence_length_consistent": len(lengths) <= 1 and len(lengths) > 0,
            "variable_schema_consistent": all(len(observations) == 1
                                               for observations in schema.values()),
            "invalid_values": invalid,
            "quality_issue_counts": self._profile.quality.counts_by_severity,
            "unresolved_questions": unknowns,
        }
        return self._evidence(kind, "dataset_profile", "all_runs", None, value)

    def variable_schema(self, name: str) -> EvidenceResponse:
        kind = EvidenceKind.VARIABLE_SCHEMA
        if error := self._begin(kind):
            return error
        if error := self._validate_name(name, kind):
            return error
        schema = self._profile.observed_structure.get("variable_schema", {})
        if name not in schema:
            return self._error(EvidenceErrorCode.UNKNOWN_VARIABLE, kind,
                               f"Variable {name!r} is not present in DatasetProfile.", variable=name)
        summary = self._signal_summary(name)
        observations = schema[name]
        run_ids = sorted({file.source_run_id for file in self._profile.files
                          if any(variable.name == name for variable in file.variables)})
        source_files = sorted(file.relative_path for file in self._profile.files
                              if any(variable.name == name for variable in file.variables))
        channel_counts = [] if summary is None else summary.get("data_channel_count") or []
        sequence_lengths = sorted({run.sequence_length for run in self._profile.runs
                                   if run.sequence_length is not None})
        logical_shape = None
        if len(channel_counts) == 1 and len(sequence_lengths) == 1:
            logical_shape = [sequence_lengths[0], channel_counts[0]]
        axis_sources = sorted({run.metadata.get("time_axis_source") for run in self._profile.runs
                               if run.metadata.get("time_axis_source")})
        value = {
            "exists": True,
            "observed_shapes": [item.get("shape") for item in observations],
            "logical_shape": logical_shape,
            "dtypes": sorted({str(item.get("dtype")) for item in observations}),
            "ndim": sorted({int(item.get("ndim")) for item in observations}),
            "channel_counts": channel_counts,
            "sequence_lengths": sequence_lengths,
            "sampling_rates_hz": sorted({run.sampling_rate_hz for run in self._profile.runs
                                          if run.sampling_rate_hz is not None}),
            "has_embedded_time_row": bool(summary and summary.get("has_embedded_time_row")),
            "channel_axis": summary.get("channel_axis") if summary else None,
            "sample_axis": summary.get("sample_axis") if summary else None,
            "associated_time_axes": axis_sources,
            "profiler_semantic_hint": summary.get("inferred_semantic_name") if summary else None,
            "consistent_across_runs": len(observations) == 1,
            "run_coverage": self._bounded_list(run_ids, self.limits.max_metadata_entries),
            "source_file_coverage": self._bounded_list(source_files,
                                                       self.limits.max_metadata_entries),
        }
        return self._evidence(kind, "dataset_profile", "all_runs", name, value)

    def signal_statistics(self, name: str) -> EvidenceResponse:
        kind = EvidenceKind.SIGNAL_STATISTICS
        if error := self._begin(kind):
            return error
        if error := self._validate_name(name, kind):
            return error
        variables = self._variables(name)
        if not variables:
            return self._error(EvidenceErrorCode.UNKNOWN_VARIABLE, kind,
                               f"Variable {name!r} is not present in DatasetProfile.", variable=name)
        summary = self._signal_summary(name)
        offset = 1 if summary and summary.get("has_embedded_time_row") else 0
        by_channel: dict[int, list[ChannelStats]] = {}
        for variable in variables:
            for stats in variable.channel_stats[offset:]:
                logical_index = stats.index - offset
                by_channel.setdefault(logical_index, []).append(stats)
        aggregated = [self._aggregate_channel(index, entries, offset)
                      for index, entries in sorted(by_channel.items())]
        returned = aggregated[:self.limits.max_statistics_entries]
        global_stats = self._aggregate_summaries(aggregated)
        value = {
            "dtype": sorted({variable.dtype for variable in variables}),
            "file_observations": len(variables),
            "global": global_stats,
            "per_channel": returned,
            "returned_channels": len(returned),
            "available_channels": len(aggregated),
            "truncated": len(returned) < len(aggregated),
        }
        if not aggregated:
            value["global"] = {
                "count": sum(variable.size for variable in variables),
                "minimum": _minimum(variable.minimum for variable in variables),
                "maximum": _maximum(variable.maximum for variable in variables),
                "mean": None,
                "standard_deviation": None,
                "nan_count": sum(variable.nan_count for variable in variables),
                "inf_count": sum(variable.inf_count for variable in variables),
            }
        return self._evidence(kind, "dataset_profile", "all_runs", name, value)

    def metadata_summary(self) -> EvidenceResponse:
        kind = EvidenceKind.METADATA_SUMMARY
        if error := self._begin(kind):
            return error
        run_ids = sorted(run.source_run_id for run in self._profile.runs)
        files = sorted(file.relative_path for file in self._profile.files)
        variables = sorted(self._profile.observed_structure.get("variable_schema", {}))
        events = [event for run in self._profile.runs for event in run.events]
        event_sources = sorted(
            item["observed_name"] for item in self._profile.signals
            if "event" in str(item.get("inferred_semantic_name", {}).get("value", ""))
        )
        indices = [event.observed_index for event in events]
        timestamps = [event.inferred_time_seconds for event in events
                      if event.inferred_time_seconds is not None]
        labels = sorted({str(event.interpretation.value) for event in events})
        value = {
            "source_subset": self._profile.source.get("scope"),
            "run_ids": self._bounded_list(run_ids, self.limits.max_metadata_entries),
            "source_files": self._bounded_list(files, self.limits.max_metadata_entries),
            "variable_names": self._bounded_list(variables, self.limits.max_metadata_entries),
            "metadata_sources": self._bounded_list(
                sorted(self._profile.metadata.get("run_metadata_sources", [])),
                self.limits.max_metadata_entries,
            ),
            "dataset_specific_hints": self._profile.metadata.get("dataset_specific_hints"),
            "events": {
                "count": len(events),
                "source_variables": self._bounded_list(event_sources,
                                                       self.limits.max_metadata_entries),
                "semantic_labels": self._bounded_list(labels,
                                                       self.limits.max_metadata_entries),
                "observed_index_min": min(indices) if indices else None,
                "observed_index_max": max(indices) if indices else None,
                "timestamp_seconds_min": min(timestamps) if timestamps else None,
                "timestamp_seconds_max": max(timestamps) if timestamps else None,
                "sequence_length_bounds": sorted({run.sequence_length for run in self._profile.runs
                                                  if run.sequence_length is not None}),
            },
        }
        return self._evidence(kind, "dataset_profile", "all_runs", None, value)

    def bounded_excerpt(
        self,
        variable: str,
        run_id: str,
        *,
        start: int,
        length: int,
        channels: tuple[int, ...] | None = None,
    ) -> EvidenceResponse:
        kind = EvidenceKind.BOUNDED_EXCERPT
        if error := self._begin(kind):
            return error
        if error := self._validate_name(variable, kind):
            return error
        if variable not in self._profile.observed_structure.get("variable_schema", {}):
            return self._error(EvidenceErrorCode.UNKNOWN_VARIABLE, kind,
                               f"Variable {variable!r} is not present in DatasetProfile.",
                               variable=variable)
        if (not isinstance(run_id, str) or not run_id or len(run_id) > 200):
            return self._error(EvidenceErrorCode.INVALID_QUERY, kind,
                               "run_id must be a non-empty string of at most 200 characters")
        if run_id not in {run.source_run_id for run in self._profile.runs}:
            return self._error(EvidenceErrorCode.UNKNOWN_RUN, kind,
                               f"Run {run_id!r} is not present in DatasetProfile.", run_id=run_id)
        if (isinstance(start, bool) or isinstance(length, bool)
                or not isinstance(start, int) or not isinstance(length, int)
                or start < 0 or length <= 0):
            return self._error(EvidenceErrorCode.INVALID_QUERY, kind,
                               "start must be non-negative and length must be a positive integer.")
        if length > self.limits.max_excerpt_samples:
            return self._error(EvidenceErrorCode.EXCERPT_TOO_LARGE, kind,
                               "Requested excerpt exceeds the per-query sample limit.",
                               requested=length, limit=self.limits.max_excerpt_samples)
        if channels is not None and (not channels or any(
                isinstance(channel, bool) or not isinstance(channel, int) or channel < 0
                for channel in channels)):
            return self._error(EvidenceErrorCode.INVALID_QUERY, kind,
                               "channels must contain non-negative integer channel indices.")
        summary = self._signal_summary(variable)
        counts = summary.get("data_channel_count") if summary else None
        available_channels = counts[0] if counts and len(counts) == 1 else 1
        selected = tuple(range(available_channels)) if channels is None else channels
        if len(selected) > self.limits.max_excerpt_channels:
            return self._error(EvidenceErrorCode.CHANNEL_LIMIT_EXCEEDED, kind,
                               "Requested excerpt exceeds the per-query channel limit.",
                               requested=len(selected), limit=self.limits.max_excerpt_channels)
        if len(set(selected)) != len(selected) or any(channel >= available_channels
                                                      for channel in selected):
            return self._error(EvidenceErrorCode.INVALID_QUERY, kind,
                               "A requested channel is duplicated or outside the profiled range.",
                               available_channels=available_channels)
        value_count = length * len(selected)
        if error := self._check_excerpt_budget(value_count, kind):
            return error
        if not self._allow_excerpts:
            return self._error(EvidenceErrorCode.UNAVAILABLE_EVIDENCE, kind,
                               "Bounded source excerpts were not enabled by the session host.")
        try:
            values, dtype, available_samples = self._read_excerpt(
                variable, run_id, start, length, selected
            )
        except (OSError, ValueError):
            return self._error(EvidenceErrorCode.UNAVAILABLE_EVIDENCE, kind,
                               "The bounded excerpt could not be produced from the profiled source.")
        value = {
            "run_id": run_id,
            "variable": variable,
            "start": start,
            "length": length,
            "channels": list(selected),
            "dtype": dtype,
            "values": values,
            "returned_values": value_count,
            "available_samples": available_samples,
            "truncated": False,
        }
        result = self._evidence(kind, "bounded_profile_source", run_id, variable, value,
                                query={"run_id": run_id, "start": start, "length": length,
                                       "channels": selected})
        if isinstance(result, Evidence):
            self._excerpt_queries += 1
            self._excerpt_values += value_count
        return result

    def _read_excerpt(self, variable: str, run_id: str, start: int, length: int,
                      channels: tuple[int, ...]) -> tuple[list[list[Any]], str, int]:
        matches = [file for file in self._profile.files if file.source_run_id == run_id
                   and any(item.name == variable for item in file.variables)]
        if len(matches) != 1:
            raise ValueError(f"expected one profiled source for {variable!r}, found {len(matches)}")
        root_value = self._profile.source.get("path")
        if not isinstance(root_value, str) or not root_value:
            raise ValueError("profile has no trusted source root")
        root = Path(root_value).resolve()
        source = (root / matches[0].relative_path).resolve()
        try:
            source.relative_to(root)
        except ValueError as error:
            raise ValueError("profiled source resolves outside the trusted dataset root") from error
        if not source.is_file() or sha256_file(source) != matches[0].sha256:
            raise ValueError("profiled source is missing or has changed since profiling")
        summary = self._signal_summary(variable)
        if source.suffix.casefold() in {".h5", ".hdf5"}:
            excerpt, dtype, available_samples = read_hdf5_excerpt(
                source, variable, start, length, channels,
                summary.get("channel_axis") if summary else None,
            )
            values = [[_json_scalar(value) for value in row] for row in excerpt]
            return values, dtype, available_samples
        raw = load_matlab(source).variables.get(variable)
        if not isinstance(raw, np.ndarray) or raw.ndim not in {1, 2}:
            raise ValueError("excerpt source is not a one- or two-dimensional array")
        if not np.issubdtype(raw.dtype, np.number):
            raise ValueError("excerpt source is not numeric")
        array = raw.reshape(1, -1) if raw.ndim == 1 else raw
        if summary and summary.get("has_embedded_time_row"):
            array = array[1:]
        if start + length > array.shape[1]:
            raise ValueError(f"requested range ends at {start + length}, but only {array.shape[1]} samples exist")
        excerpt = array[np.asarray(channels), start:start + length]
        values = [[_json_scalar(value) for value in row] for row in excerpt]
        return values, str(raw.dtype), int(array.shape[1])

    def _load_documentation(
        self,
        registrations: list[DocumentationSource] | tuple[DocumentationSource, ...],
    ) -> tuple[_LoadedDocumentation, ...]:
        indexed = list(enumerate(registrations))
        indexed.sort(key=lambda item: (
            item[1].source_id.casefold()
            if isinstance(item[1], DocumentationSource)
               and isinstance(item[1].source_id, str)
            else f"~invalid-{item[0]:08d}"
        ))
        selected = indexed[:self.limits.max_documentation_sources]
        loaded: list[_LoadedDocumentation] = []
        seen_ids: set[str] = set()
        for original_index, registration in selected:
            if not isinstance(registration, DocumentationSource):
                loaded.append(_LoadedDocumentation(
                    f"invalid-{original_index + 1}",
                    f"Invalid source {original_index + 1}",
                    "unknown",
                    "invalid",
                    error_code=EvidenceErrorCode.INVALID_DOCUMENTATION_SOURCE,
                ))
                continue
            source_id = registration.source_id
            display_name = registration.display_name or (
                Path(registration.path).name
                if isinstance(registration.path, (str, Path)) else source_id
            )
            if (not isinstance(source_id, str) or not _SOURCE_ID_PATTERN.fullmatch(source_id)
                    or source_id.casefold() in seen_ids
                    or not isinstance(registration.path, (str, Path))
                    or not isinstance(display_name, str) or not display_name.strip()
                    or len(display_name) > 200):
                loaded.append(_LoadedDocumentation(
                    source_id if isinstance(source_id, str) and source_id else
                    f"invalid-{original_index + 1}",
                    display_name if isinstance(display_name, str) and display_name else
                    f"Invalid source {original_index + 1}",
                    "unknown",
                    "invalid",
                    error_code=EvidenceErrorCode.INVALID_DOCUMENTATION_SOURCE,
                ))
                continue
            seen_ids.add(source_id.casefold())
            suffix = Path(registration.path).suffix.casefold()
            document_type = _DOCUMENTATION_SUFFIXES.get(suffix, suffix.removeprefix(".") or "unknown")
            if suffix not in _DOCUMENTATION_SUFFIXES:
                loaded.append(_LoadedDocumentation(
                    source_id, display_name.strip(), document_type, "unsupported",
                    error_code=EvidenceErrorCode.UNSUPPORTED_DOCUMENTATION,
                ))
                continue
            try:
                path = Path(registration.path).resolve(strict=True)
                if not path.is_file():
                    raise OSError("documentation source is not a regular file")
                size = path.stat().st_size
                if size > self.limits.max_document_bytes:
                    raise ValueError("documentation source exceeds the configured size limit")
                raw = path.read_bytes()
                text = raw.decode("utf-8")
                if "\x00" in text:
                    raise ValueError("documentation source contains NUL characters")
            except (OSError, RuntimeError, TypeError, UnicodeError, ValueError):
                loaded.append(_LoadedDocumentation(
                    source_id, display_name.strip(), document_type, "invalid",
                    error_code=EvidenceErrorCode.INVALID_DOCUMENTATION_SOURCE,
                ))
                continue
            loaded.append(_LoadedDocumentation(
                source_id,
                display_name.strip(),
                document_type,
                "available",
                size_bytes=len(raw),
                line_count=len(text.splitlines()),
                sha256=hashlib.sha256(raw).hexdigest(),
                text=text,
            ))
        return tuple(loaded)

    @staticmethod
    def _documentation_match_rank(
        line: str,
        query: str,
        normalized_query: str,
        query_tokens: list[str],
    ) -> int | None:
        if query in line:
            return 0
        normalized_line = " ".join(line.casefold().split())
        if normalized_query in normalized_line:
            return 1
        line_tokens = set(re.findall(r"\w+", normalized_line))
        if query_tokens and all(token in line_tokens for token in query_tokens):
            return 2
        return None

    @staticmethod
    def _bounded_excerpt_text(
        excerpt: str,
        matching_line: str,
        query: str,
        limit: int,
    ) -> tuple[str, bool]:
        if len(excerpt) <= limit:
            return excerpt, False
        match_in_excerpt = excerpt.casefold().find(query.casefold())
        if match_in_excerpt < 0:
            match_in_excerpt = excerpt.find(matching_line)
        center = max(match_in_excerpt, 0) + min(len(query), limit) // 2
        start = max(0, center - limit // 2)
        end = min(len(excerpt), start + limit)
        start = max(0, end - limit)
        bounded = excerpt[start:end]
        if start:
            bounded = "…" + bounded[1:]
        if end < len(excerpt):
            bounded = bounded[:-1] + "…"
        return bounded, True

    def _validate_configuration(self) -> None:
        values = (*asdict(self.limits).values(), *asdict(self.budget).values())
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in values):
            raise ValueError("All evidence limits and budgets must be positive integers")

    def _begin(self, kind: EvidenceKind) -> EvidenceError | None:
        if self._total_queries >= self.budget.max_total_queries:
            return self._error(EvidenceErrorCode.QUERY_BUDGET_EXCEEDED, kind,
                               "The session's total evidence-query budget is exhausted.",
                               limit=self.budget.max_total_queries, used=self._total_queries)
        self._total_queries += 1
        return None

    def _check_excerpt_budget(self, value_count: int,
                              kind: EvidenceKind) -> EvidenceError | None:
        if self._excerpt_queries >= self.budget.max_excerpt_queries:
            return self._error(EvidenceErrorCode.EXCERPT_BUDGET_EXCEEDED, kind,
                               "The session's excerpt-query budget is exhausted.",
                               limit=self.budget.max_excerpt_queries, used=self._excerpt_queries)
        if self._excerpt_values + value_count > self.budget.max_total_excerpt_values:
            return self._error(EvidenceErrorCode.SAMPLE_BUDGET_EXCEEDED, kind,
                               "The session's total excerpt-value budget would be exceeded.",
                               requested=value_count, used=self._excerpt_values,
                               limit=self.budget.max_total_excerpt_values)
        return None

    def _evidence(self, kind: EvidenceKind, source: str, scope: str, target: str | None,
                  value: dict[str, Any], *, query: dict[str, Any] | None = None,
                  metadata: dict[str, Any] | None = None) -> EvidenceResponse:
        evidence_id = self._evidence_id(kind, target, query or {}, value)
        evidence_metadata = {"profile_fingerprint": self._profile_fingerprint}
        evidence_metadata.update(metadata or {})
        item = Evidence(evidence_id, kind, source, scope, target, value,
                        evidence_metadata)
        size = len(item.to_json(indent=None).encode())
        if size > self.limits.max_response_bytes:
            return self._error(EvidenceErrorCode.RESPONSE_TOO_LARGE, kind,
                               "Evidence response exceeds the configured serialized-size limit.",
                               response_bytes=size, limit=self.limits.max_response_bytes)
        return item

    def _evidence_id(self, kind: EvidenceKind, target: str | None,
                     query: dict[str, Any], value: dict[str, Any]) -> str:
        identity = json.dumps({"profile": self._profile_fingerprint, "kind": kind,
                               "target": target, "query": query, "value": value}, sort_keys=True,
                              separators=(",", ":"))
        suffix = hashlib.sha256(identity.encode()).hexdigest()[:12]
        target_slug = re.sub(r"[^a-z0-9]+", "_", (target or "dataset").casefold()).strip("_")
        return f"ev_{kind.value}_{target_slug}_{suffix}"

    def _error(self, code: EvidenceErrorCode, query: EvidenceKind, message: str,
               **details: Any) -> EvidenceError:
        return EvidenceError(code, query, message, details)

    def _validate_name(self, name: str, kind: EvidenceKind) -> EvidenceError | None:
        if not isinstance(name, str) or not name or len(name) > 200:
            return self._error(EvidenceErrorCode.INVALID_QUERY, kind,
                               "variable must be a non-empty string of at most 200 characters")
        return None

    def _variables(self, name: str) -> list[VariableProfile]:
        return [variable for file in self._profile.files for variable in file.variables
                if variable.name == name]

    def _signal_summary(self, name: str) -> dict[str, Any] | None:
        return next((item for item in self._profile.signals
                     if item.get("observed_name") == name), None)

    @staticmethod
    def _bounded_list(values: list[Any], limit: int) -> dict[str, Any]:
        returned = values[:limit]
        return {"items": returned, "returned": len(returned), "available": len(values),
                "truncated": len(returned) < len(values)}

    @staticmethod
    def _aggregate_channel(index: int, entries: list[ChannelStats],
                           source_offset: int) -> dict[str, Any]:
        finite_count = sum(item.count - item.nan_count - item.inf_count for item in entries)
        weighted_sum = sum((item.mean or 0.0) *
                           (item.count - item.nan_count - item.inf_count) for item in entries)
        second_moment_sum = sum(
            ((item.standard_deviation or 0.0) ** 2 + (item.mean or 0.0) ** 2)
            * (item.count - item.nan_count - item.inf_count)
            for item in entries
        )
        mean = weighted_sum / finite_count if finite_count else None
        variance = max(second_moment_sum / finite_count - mean ** 2, 0.0) \
            if finite_count and mean is not None else None
        return {
            "channel_index": index,
            "source_index": index + source_offset,
            "count": sum(item.count for item in entries),
            "minimum": _minimum(item.minimum for item in entries),
            "maximum": _maximum(item.maximum for item in entries),
            "mean": mean,
            "standard_deviation": math.sqrt(variance) if variance is not None else None,
            "nan_count": sum(item.nan_count for item in entries),
            "inf_count": sum(item.inf_count for item in entries),
        }

    @staticmethod
    def _aggregate_summaries(entries: list[dict[str, Any]]) -> dict[str, Any]:
        finite_count = sum(item["count"] - item["nan_count"] - item["inf_count"]
                           for item in entries)
        weighted_sum = sum((item["mean"] or 0.0)
                           * (item["count"] - item["nan_count"] - item["inf_count"])
                           for item in entries)
        second_moment_sum = sum(
            ((item["standard_deviation"] or 0.0) ** 2 + (item["mean"] or 0.0) ** 2)
            * (item["count"] - item["nan_count"] - item["inf_count"])
            for item in entries
        )
        mean = weighted_sum / finite_count if finite_count else None
        variance = max(second_moment_sum / finite_count - mean ** 2, 0.0) \
            if finite_count and mean is not None else None
        return {
            "count": sum(item["count"] for item in entries),
            "minimum": _minimum(item["minimum"] for item in entries),
            "maximum": _maximum(item["maximum"] for item in entries),
            "mean": mean,
            "standard_deviation": math.sqrt(variance) if variance is not None else None,
            "nan_count": sum(item["nan_count"] for item in entries),
            "inf_count": sum(item["inf_count"] for item in entries),
        }


def _minimum(values: Any) -> float | None:
    finite = [value for value in values if value is not None]
    return min(finite) if finite else None


def _maximum(values: Any) -> float | None:
    finite = [value for value in values if value is not None]
    return max(finite) if finite else None


def _json_scalar(value: Any) -> Any:
    scalar = value.item() if isinstance(value, np.generic) else value
    if isinstance(scalar, complex):
        return {"real": scalar.real, "imag": scalar.imag}
    if isinstance(scalar, float) and not math.isfinite(scalar):
        return "NaN" if math.isnan(scalar) else ("Infinity" if scalar > 0 else "-Infinity")
    return scalar
