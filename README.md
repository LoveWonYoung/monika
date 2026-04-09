# monika

Mixed Rust/Python workspace for the `isotp_engine` transport stack.

## Repository layout

- `myo3/` — main mixed Rust/Python project
- `myo3/src/` — Rust ISO-TP / LIN-TP engine and PyO3 exports
- `myo3/python/isotp_engine/` — installable Python package
- `myo3/tests/` — Python tests
- `myo3/examples/` — source-tree demos
- `myo3/scripts/` — ad-hoc local scripts

## Quick start

```bash
make dev
make test
```

Equivalent manual commands:

```bash
cd myo3
python -m pip install --upgrade pip setuptools wheel maturin
python -m maturin develop
python -m unittest discover -s tests -p 'test_*.py' -v
```

## Documentation

Detailed package layout, usage examples, and backend notes live in:

- `myo3/README.md`

## Notes

- The repository root is a lightweight workspace shell.
- The main implementation and packaging logic live under `myo3/`.
