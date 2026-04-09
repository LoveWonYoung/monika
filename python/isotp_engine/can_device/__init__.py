from __future__ import annotations

import importlib
from typing import Dict

from .fake import FakeEcu
from .interface import CanDeviceInterface
from .worker import CanTpClient, CanTpWorker

_BACKEND_MODULES = {
    "toomoss": ".backends.toomoss_canfd",
    "pcan": ".backends.pcan",
    "vector": ".backends.vector",
    "tsmaster": ".backends.tsmaster",
}

_BACKEND_CLASS_NAMES = {
    "toomoss": "Toomoss",
    "pcan": "Pcan",
    "vector": "Vector",
    "tsmaster": "TSMaster",
}


def _load_symbol(module_name: str, symbol: str):
    module = importlib.import_module(module_name, __name__)
    return getattr(module, symbol)


def get_backend(name: str):
    backend = name.lower()
    if backend not in _BACKEND_MODULES:
        raise KeyError(f"unknown CAN backend: {name}")
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


__all__ = [
    "CanDeviceInterface",
    "CanTpClient",
    "CanTpWorker",
    "FakeEcu",
    "available_backends",
    "get_backend",
]
