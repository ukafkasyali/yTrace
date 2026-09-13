from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.io import loadmat, whosmat

from ..jobs import ResourceFormat
from .tabular import FormatAdapterError


@dataclass(frozen=True)
class ArrayProfile:
    name: str
    shape: tuple[int, ...]
    dtype: str
    size_bytes: int


@dataclass(frozen=True)
class ArrayContainerProfile:
    format: ResourceFormat
    arrays: tuple[ArrayProfile, ...]


@dataclass(frozen=True)
class ArrayData:
    arrays: dict[str, np.ndarray]


class NumericArrayAdapter:
    def __init__(
        self,
        *,
        max_arrays: int = 1_000,
        max_depth: int = 20,
        max_elements_per_array: int = 100_000_000,
        max_total_bytes: int = 2_000_000_000,
        max_npz_expansion_ratio: int = 200,
    ):
        self.max_arrays = max_arrays
        self.max_depth = max_depth
        self.max_elements_per_array = max_elements_per_array
        self.max_total_bytes = max_total_bytes
        self.max_npz_expansion_ratio = max_npz_expansion_ratio

    def inspect(self, path: Path, format_: ResourceFormat) -> ArrayContainerProfile:
        if format_ is ResourceFormat.NPY:
            array = self._safe_npy(path)
            arrays = (self._profile("array", array),)
        elif format_ is ResourceFormat.NPZ:
            self._validate_npz_container(path)
            with np.load(path, allow_pickle=False) as container:
                if len(container.files) > self.max_arrays:
                    raise FormatAdapterError("NPZ contains too many arrays")
                arrays = tuple(
                    self._profile(name, self._safe_npz_array(container, name))
                    for name in sorted(container.files)
                )
        elif format_ is ResourceFormat.MAT:
            arrays = self._inspect_mat(path)
        elif format_ is ResourceFormat.HDF5:
            arrays = self._inspect_hdf5(path)
        else:
            raise FormatAdapterError("Numeric array adapter received another format")
        self._validate_total(arrays)
        if not arrays:
            raise FormatAdapterError("Numeric container has no supported arrays")
        return ArrayContainerProfile(format_, arrays)

    def read_arrays(
        self,
        path: Path,
        format_: ResourceFormat,
        names: list[str],
    ) -> ArrayData:
        if not names or len(names) != len(set(names)):
            raise FormatAdapterError("Selected array names must be unique and non-empty")
        profile = self.inspect(path, format_)
        available = {array.name for array in profile.arrays}
        if any(name not in available for name in names):
            raise FormatAdapterError("Selected array is absent or unsupported")
        if format_ is ResourceFormat.NPY:
            values = {"array": np.asarray(self._safe_npy(path))}
        elif format_ is ResourceFormat.NPZ:
            with np.load(path, allow_pickle=False) as container:
                values = {name: np.asarray(self._safe_npz_array(container, name)) for name in names}
        elif format_ is ResourceFormat.MAT:
            payload = loadmat(path, variable_names=names, squeeze_me=False, struct_as_record=True)
            values = {name: self._validated_array(name, payload[name]) for name in names}
        else:
            values = self._read_hdf5(path, names)
        return ArrayData(values)

    def _safe_npy(self, path: Path) -> np.ndarray:
        try:
            array = np.load(path, mmap_mode="r", allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise FormatAdapterError("NPY header or payload is unsafe") from exc
        return self._validated_array("array", array)

    def _safe_npz_array(self, container, name: str) -> np.ndarray:
        try:
            return self._validated_array(name, container[name])
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise FormatAdapterError("NPZ array payload is unsafe") from exc

    def _validate_npz_container(self, path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > self.max_arrays:
                    raise FormatAdapterError("NPZ contains too many members")
                total = 0
                compressed = 0
                for member in members:
                    if member.is_dir() or not member.filename.endswith(".npy"):
                        raise FormatAdapterError("NPZ contains a non-array member")
                    total += member.file_size
                    compressed += max(member.compress_size, 1)
                if total > self.max_total_bytes or total > compressed * self.max_npz_expansion_ratio:
                    raise FormatAdapterError("NPZ exceeds configured expansion limits")
        except zipfile.BadZipFile as exc:
            raise FormatAdapterError("NPZ container is invalid") from exc

    def _inspect_mat(self, path: Path) -> tuple[ArrayProfile, ...]:
        try:
            entries = whosmat(path)
        except (OSError, ValueError) as exc:
            raise FormatAdapterError("MAT resource could not be inspected") from exc
        if len(entries) > self.max_arrays:
            raise FormatAdapterError("MAT resource contains too many variables")
        supported = []
        for name, shape, matlab_type in entries:
            if matlab_type in {"cell", "struct", "object", "char", "function", "opaque"}:
                continue
            elements = self._elements(shape)
            supported.append(ArrayProfile(name, tuple(shape), matlab_type, elements * 8))
        return tuple(sorted(supported, key=lambda item: item.name))

    def _inspect_hdf5(self, path: Path) -> tuple[ArrayProfile, ...]:
        try:
            import h5py
        except ImportError as exc:
            raise FormatAdapterError("HDF5 support requires the hdf5 package extra") from exc
        arrays: list[ArrayProfile] = []
        visited = 0
        try:
            with h5py.File(path, "r") as container:
                def visitor(name, item):
                    nonlocal visited
                    visited += 1
                    if visited > self.max_arrays * 4 or len(Path(name).parts) > self.max_depth:
                        raise FormatAdapterError("HDF5 traversal exceeds configured limits")
                    if isinstance(item, h5py.Dataset) and np.issubdtype(item.dtype, np.number):
                        arrays.append(
                            ArrayProfile(
                                name,
                                tuple(item.shape),
                                str(item.dtype),
                                self._elements(item.shape) * item.dtype.itemsize,
                            )
                        )

                container.visititems(visitor)
        except OSError as exc:
            raise FormatAdapterError("HDF5 resource could not be inspected") from exc
        if len(arrays) > self.max_arrays:
            raise FormatAdapterError("HDF5 resource contains too many numeric datasets")
        return tuple(sorted(arrays, key=lambda item: item.name))

    def _read_hdf5(self, path: Path, names: list[str]) -> dict[str, np.ndarray]:
        try:
            import h5py
        except ImportError as exc:
            raise FormatAdapterError("HDF5 support requires the hdf5 package extra") from exc
        try:
            with h5py.File(path, "r") as container:
                return {
                    name: self._validated_array(name, np.asarray(container[name])) for name in names
                }
        except (OSError, KeyError) as exc:
            raise FormatAdapterError("HDF5 selected dataset could not be read") from exc

    def _validated_array(self, name: str, array: np.ndarray) -> np.ndarray:
        if not np.issubdtype(array.dtype, np.number) or array.dtype.hasobject:
            raise FormatAdapterError(f"Array {name!r} is not a safe numeric dtype")
        elements = self._elements(array.shape)
        if elements * array.dtype.itemsize > self.max_total_bytes:
            raise FormatAdapterError(f"Array {name!r} exceeds the configured byte limit")
        return array

    def _profile(self, name: str, array: np.ndarray) -> ArrayProfile:
        return ArrayProfile(name, tuple(array.shape), str(array.dtype), int(array.nbytes))

    def _elements(self, shape) -> int:
        elements = 1
        for dimension in shape:
            if dimension < 0:
                raise FormatAdapterError("Array has an invalid shape")
            elements *= int(dimension)
            if elements > self.max_elements_per_array:
                raise FormatAdapterError("Array exceeds the configured element limit")
        return elements

    def _validate_total(self, arrays: tuple[ArrayProfile, ...]) -> None:
        if sum(array.size_bytes for array in arrays) > self.max_total_bytes:
            raise FormatAdapterError("Numeric container exceeds the configured byte limit")
