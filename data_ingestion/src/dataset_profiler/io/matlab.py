from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
import hashlib

import numpy as np


class MatlabLoadError(RuntimeError):
    pass


@dataclass
class LoadedMatlab:
    variables: dict[str, Any]
    loader: str
    format: str


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def matlab_format(path: Path) -> str:
    with path.open("rb") as stream:
        header = stream.read(128)
    if header.startswith(b"MATLAB 7.3 MAT-file") or header.startswith(b"\x89HDF"):
        return "matlab-v7.3-hdf5"
    if header.startswith(b"MATLAB"):
        return "matlab-v5"
    return "matlab-v4-or-earlier"


def load_matlab(path: str | Path) -> LoadedMatlab:
    source = Path(path)
    detected = matlab_format(source)
    if detected != "matlab-v7.3-hdf5":
        try:
            from scipy.io import loadmat

            values = loadmat(source, struct_as_record=False, squeeze_me=False)
            return LoadedMatlab(
                variables={k: v for k, v in values.items() if not k.startswith("__")},
                loader="scipy.io.loadmat",
                format=detected,
            )
        except (NotImplementedError, ValueError) as error:
            if "7.3" not in str(error) and detected != "matlab-v7.3-hdf5":
                raise MatlabLoadError(f"Could not load {source}: {error}") from error

    try:
        import h5py
    except ImportError as error:
        raise MatlabLoadError(
            f"{source} is an HDF5/MATLAB v7.3 file; install the 'hdf5' extra"
        ) from error

    def materialize(node: Any, handle: Any) -> Any:
        if isinstance(node, h5py.Dataset):
            array = np.asarray(node)
            if h5py.check_dtype(ref=node.dtype) is not None:
                resolved = np.empty(array.shape, dtype=object)
                for index, reference in np.ndenumerate(array):
                    resolved[index] = materialize(handle[reference], handle) if reference else None
                return resolved
            # MATLAB stores array dimensions in the reverse order in v7.3 files.
            return array.transpose(tuple(reversed(range(array.ndim)))) if array.ndim > 1 else array
        return {name: materialize(child, handle) for name, child in node.items()}

    try:
        with h5py.File(source, "r") as handle:
            values = {
                name: materialize(node, handle)
                for name, node in handle.items()
                if name != "#refs#"
            }
    except OSError as error:
        raise MatlabLoadError(f"Could not load {source}: {error}") from error
    return LoadedMatlab(values, "h5py", "matlab-v7.3-hdf5")


def iter_arrays(value: Any, prefix: str = "") -> Iterator[tuple[str, np.ndarray]]:
    if isinstance(value, np.ndarray):
        if value.dtype.names:
            for field_name in value.dtype.names:
                yield from iter_arrays(value[field_name], f"{prefix}.{field_name}")
        elif value.dtype == object:
            for index, item in np.ndenumerate(value):
                suffix = ",".join(map(str, index))
                yield from iter_arrays(item, f"{prefix}[{suffix}]")
        else:
            yield prefix, value
    elif isinstance(value, dict):
        for name, item in value.items():
            yield from iter_arrays(item, f"{prefix}.{name}" if prefix else name)
    elif hasattr(value, "_fieldnames"):
        for name in value._fieldnames:
            yield from iter_arrays(getattr(value, name), f"{prefix}.{name}")


def describe_nested(value: Any, depth: int = 0, max_depth: int = 5) -> Any:
    if depth >= max_depth:
        return {"type": type(value).__name__, "truncated": True}
    if isinstance(value, dict):
        return {
            "type": "dict",
            "fields": {
                str(k): describe_nested(v, depth + 1, max_depth)
                for k, v in value.items()
            },
        }
    if hasattr(value, "_fieldnames"):
        return {
            "type": "matlab_struct",
            "fields": {
                name: describe_nested(getattr(value, name), depth + 1, max_depth)
                for name in value._fieldnames
            },
        }
    if isinstance(value, np.ndarray):
        result: dict[str, Any] = {
            "type": "ndarray",
            "shape": list(value.shape),
            "dtype": str(value.dtype),
        }
        if value.dtype.names:
            result["fields"] = list(value.dtype.names)
        elif value.dtype == object and value.size <= 20:
            result["items"] = [
                describe_nested(item, depth + 1, max_depth) for item in value.flat
            ]
        return result
    return {"type": type(value).__name__, "value": repr(value)[:200]}
