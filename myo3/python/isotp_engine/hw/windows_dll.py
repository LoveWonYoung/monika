import ctypes
import os
import platform
from ctypes.util import find_library
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

try:
    import winreg
except Exception:  # pragma: no cover - non-Windows runtimes
    winreg = None


def _iter_registry_strings(subkeys: Sequence[str], value_names: Sequence[str]) -> Iterable[str]:
    if winreg is None:
        return []

    roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    access_base = getattr(winreg, "KEY_READ", 0)

    values = []
    for root in roots:
        for subkey in subkeys:
            for view in views:
                try:
                    hkey = winreg.OpenKey(root, subkey, 0, access_base | view)
                except OSError:
                    continue
                with hkey:
                    for value_name in value_names:
                        try:
                            query_name = None if value_name == "" else value_name
                            raw, _ = winreg.QueryValueEx(hkey, query_name)
                        except OSError:
                            continue
                        if isinstance(raw, str) and raw.strip():
                            values.append(raw.strip())
    return values


def _normalize_dll_names(dll_names: Sequence[str]) -> List[str]:
    names: List[str] = []
    seen = set()
    for raw in dll_names:
        if not raw:
            continue
        for name in (raw, raw if raw.lower().endswith(".dll") else f"{raw}.dll"):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    return names


def _dedupe_candidates(candidates: Iterable[str]) -> List[str]:
    seen = set()
    unique: List[str] = []
    for candidate in candidates:
        key = os.path.normcase(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def build_windows_dll_candidates(
    dll_names: Sequence[str],
    registry_subkeys: Sequence[str],
    registry_value_names: Sequence[str],
    search_dirs: Sequence[Path],
    prefer_registry: bool = True,
) -> List[str]:
    normalized_names = _normalize_dll_names(dll_names)

    registry_candidates: List[str] = []
    for raw in _iter_registry_strings(registry_subkeys, registry_value_names):
        expanded = os.path.expandvars(raw).strip("\" ")
        path = Path(expanded)
        if path.suffix.lower() == ".dll":
            if path.is_file():
                registry_candidates.append(str(path))
            continue
        for dll_name in normalized_names:
            for candidate in (path / dll_name, path / "bin" / dll_name):
                if candidate.is_file():
                    registry_candidates.append(str(candidate))

    search_candidates: List[str] = []
    for search_dir in search_dirs:
        for dll_name in normalized_names:
            candidate = search_dir / dll_name
            if candidate.is_file():
                search_candidates.append(str(candidate))

    if prefer_registry:
        file_candidates = registry_candidates + search_candidates
    else:
        file_candidates = search_candidates + registry_candidates

    findlib_candidates: List[str] = []
    for dll_name in normalized_names:
        stem = Path(dll_name).stem
        lib_path = find_library(stem)
        if lib_path:
            findlib_candidates.append(lib_path)

    fallback_names = list(normalized_names)
    return _dedupe_candidates(file_candidates + findlib_candidates + fallback_names)


def load_windows_dll(
    dll_names: Sequence[str],
    registry_subkeys: Sequence[str],
    registry_value_names: Sequence[str],
    search_dirs: Sequence[Path],
    prefer_registry: bool = True,
    loader: Optional[Callable[[str], object]] = None,
) -> object:
    if platform.system() != "Windows":
        raise RuntimeError("Windows DLL loading is only supported on Windows")

    candidates = build_windows_dll_candidates(
        dll_names=dll_names,
        registry_subkeys=registry_subkeys,
        registry_value_names=registry_value_names,
        search_dirs=search_dirs,
        prefer_registry=prefer_registry,
    )
    if not candidates:
        raise RuntimeError(f"No DLL candidates generated for names: {list(dll_names)}")

    load = loader or ctypes.windll.LoadLibrary
    last_exc: Optional[BaseException] = None
    for candidate in candidates:
        try:
            return load(candidate)
        except OSError as exc:
            last_exc = exc

    raise RuntimeError(f"Failed to load DLL from candidates: {candidates}") from last_exc
