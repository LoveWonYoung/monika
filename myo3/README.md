# myo3 Python package

This directory contains a mixed Rust/Python project built with `maturin`.

## Layout

- `src/` — Rust transport engine and PyO3 exports
- `python/isotp_engine/` — installable Python package
- `tests/` — Python unit tests
- `scripts/` — manual / ad-hoc scripts

Installed package layout:

- `isotp_engine` — top-level Python package
- `isotp_engine._native` — Rust extension module
- `isotp_engine.bindings` — high-level binding API
- `isotp_engine.can_device` — CAN device adapters and workers
- `isotp_engine.lin_device` — LIN device adapters and workers
- `isotp_engine.hw` — hardware-facing helpers
- `isotp_engine.core` — shared data types
- `isotp_engine.utils` — utility helpers

## Development

Create and activate a virtual environment from the repository root:

```bash
cd /Users/wonyoung/Documents/MyOpenClawWorkSpace/monika
python3 -m venv .venv
source .venv/bin/activate
```

Install the mixed project in editable mode:

```bash
cd myo3
python -m pip install --upgrade pip setuptools wheel maturin
python -m maturin develop
```

## Verify install

```bash
python -c "import isotp_engine; print(isotp_engine.__file__)"
python -c "from isotp_engine.bindings import IsoTpEngine, LinTpEngine; print(IsoTpEngine, LinTpEngine)"
```

## Run tests

From `myo3/`:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

## Demo entrypoints

Run package demos with module syntax:

```bash
python -m isotp_engine can
python -m isotp_engine lin
```

Or invoke submodules directly:

```bash
python -m isotp_engine.can_device.main
python -m isotp_engine.lin_device.main
```

## Import examples

```python
from isotp_engine.bindings import IsoTpEngine, LinTpEngine, TpConfig, LinTpConfig
from isotp_engine.can_device.worker import CanTpWorker
from isotp_engine.can_device.fake import FakeEcu
from isotp_engine.lin_device.worker import LinTpWorker
```

## Notes

- The legacy `py/` source tree has been retired in favor of `python/isotp_engine/`.
- Tests no longer rely on `PYTHONPATH=myo3/py` hacks.
- `maturin` is configured to build the Rust extension as `isotp_engine._native`.
