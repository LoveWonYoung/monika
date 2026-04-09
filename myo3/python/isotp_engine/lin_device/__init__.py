from __future__ import annotations

import importlib
from typing import Dict

from .interface import LinMasterDeviceInterface
from .worker import LinTpWorker

_BACKEND_MODULES = {
    "toomoss": ".backends.toomoss",
}

_BACKEND_CLASS_NAMES = {
    "toomoss": "ToomossLin",
}

_EXPORT_TO_BACKEND = {
    "ToomossLin": "toomoss",
}


def _load_symbol(module_name: str, symbol: str):
    module = importlib.import_module(module_name, __name__)
    return getattr(module, symbol)


def get_backend(name: str):
    backend = name.lower()
    if backend not in _BACKEND_MODULES:
        raise KeyError(f"unknown LIN backend: {name}")
    return _load_symbol(_BACKEND_MODULES[backend], _BACKEND_CLASS_NAMES[backend])


def available_backends() -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for name in _BACKEND_MODULES:
        try:
            get_backend(name)
        except (ImportError, OSError, RuntimeError):
            out[name] = False
        else:
            out[name] = True
    return out


def __getattr__(name: str):
    if name in _EXPORT_TO_BACKEND:
        try:
            return get_backend(_EXPORT_TO_BACKEND[name])
        except (ImportError, OSError, RuntimeError):
            return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "LinMasterDeviceInterface",
    "LinTpWorker",
    "ToomossLin",
    "available_backends",
    "get_backend",
]
