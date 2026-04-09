from __future__ import annotations

import importlib
from typing import Dict


class BackendRegistry:
    def __init__(self, kind: str, entries: dict[str, tuple[str, str]]):
        self._kind = kind
        self._entries = {name.lower(): value for name, value in entries.items()}

    def get(self, name: str, package: str):
        backend = name.lower()
        if backend not in self._entries:
            raise KeyError(f"unknown {self._kind} backend: {name}")
        module_name, symbol = self._entries[backend]
        module = importlib.import_module(module_name, package)
        return getattr(module, symbol)

    def available(self, package: str) -> Dict[str, bool]:
        out: Dict[str, bool] = {}
        for name in self._entries:
            try:
                self.get(name, package)
            except (ImportError, OSError, RuntimeError):
                out[name] = False
            else:
                out[name] = True
        return out
