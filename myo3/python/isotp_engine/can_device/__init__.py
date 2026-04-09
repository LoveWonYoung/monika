from __future__ import annotations

import importlib
import warnings
from typing import Dict, Optional

from .fake import FakeEcu
from .interface import CanDeviceInterface
from .worker import CanTpClient, CanTpWorker

_BACKEND_MODULES = {
    "toomoss": ".backends.toomoss",
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

_EXPORT_TO_BACKEND = {
    "Toomoss": "toomoss",
    "Pcan": "pcan",
    "Vector": "vector",
    "TSMaster": "tsmaster",
}

_OPTIONAL_EXPORTS = {"UdsoncanIsoTpConnection": ".udsoncan_connection"}


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


def __getattr__(name: str):
    if name in _EXPORT_TO_BACKEND:
        try:
            return get_backend(_EXPORT_TO_BACKEND[name])
        except (ImportError, OSError, RuntimeError):
            return None

    if name == "UdsoncanIsoTpConnection":
        try:
            return _load_symbol(_OPTIONAL_EXPORTS[name], name)
        except (ImportError, OSError, RuntimeError):
            return None

    if name == "MyHwDeviceInterface":
        warnings.warn(
            "MyHwDeviceInterface is deprecated; use CanDeviceInterface instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return CanDeviceInterface

    if name == "MyHwDeviceWithTpEngine":
        warnings.warn(
            "MyHwDeviceWithTpEngine is deprecated; use CanTpClient instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return CanTpClient

    if name == "TpWorker":
        warnings.warn(
            "TpWorker is deprecated; use CanTpWorker instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return CanTpWorker

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CanDeviceInterface",
    "CanTpClient",
    "CanTpWorker",
    "FakeEcu",
    "Toomoss",
    "Pcan",
    "Vector",
    "TSMaster",
    "UdsoncanIsoTpConnection",
    "available_backends",
    "get_backend",
    "MyHwDeviceInterface",
    "MyHwDeviceWithTpEngine",
    "TpWorker",
]
