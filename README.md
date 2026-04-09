# isotp_engine

Rust-backed ISO-TP / LIN-TP engine with Python bindings.

## Repository layout

- `src/` — Rust transport engine and PyO3 exports
- `python/isotp_engine/` — installable Python package
- `tests/` — Python unit tests
- `examples/` — runnable source-tree demos
- `.github/workflows/CI.yml` — wheel / sdist build pipeline

## Development setup

Create and activate a virtual environment from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the mixed project in editable mode:

```bash
python -m pip install --upgrade pip setuptools wheel maturin
python -m maturin develop
```

## Common tasks

```bash
make dev
make test
make build
```

Note: `uv.lock` is currently just a leftover workspace lockfile and is not part of the main maturin/Cargo build flow.

Equivalent manual commands:

```bash
python -m pip install --upgrade pip setuptools wheel maturin
python -m maturin develop
python -m unittest discover -s tests -t . -p 'test_*.py' -v
python -m maturin build
```

## Verify install

```bash
python -c "import isotp_engine; print(isotp_engine.__file__)"
python -c "from isotp_engine.bindings import IsoTpEngine, LinTpEngine; print(IsoTpEngine, LinTpEngine)"
```

## Demo entrypoints

```bash
isotp-engine can
isotp-engine lin
python -m isotp_engine can
python -m isotp_engine lin
python examples/can_demo.py
python examples/lin_demo.py
python examples/udsoncan_demo.py
```

## Package layout

Installed package layout:

- `isotp_engine` — top-level Python package
- `isotp_engine._native` — Rust extension module built by maturin / PyO3
- `isotp_engine.bindings` — high-level transport API
- `isotp_engine.can_device` — CAN device workers, interfaces, fake devices, backends
- `isotp_engine.lin_device` — LIN device workers, interfaces, and backends
- `isotp_engine.hw` — shared hardware-facing helpers
- `isotp_engine.common` — shared data types
- `isotp_engine.utils` — utility helpers

## Import examples

```python
from isotp_engine.bindings import IsoTpEngine, LinTpEngine, TpConfig, LinTpConfig
from isotp_engine.can_device.worker import CanTpWorker
from isotp_engine.can_device.fake import FakeEcu
from isotp_engine.can_device.udsoncan_connection import UdsoncanIsoTpConnection
from isotp_engine.lin_device.worker import LinTpWorker
from isotp_engine.common.types import RawCanMsg, RawLinMsg
```

## udsoncan integration

```python
import udsoncan.configs
from udsoncan.client import Client
from isotp_engine.can_device.backends.toomoss import Toomoss
from isotp_engine.can_device.udsoncan_connection import UdsoncanIsoTpConnection

cfg = udsoncan.configs.default_client_config.copy()
cfg["request_timeout"] = 5.0

with Toomoss() as hw:
    conn = UdsoncanIsoTpConnection(hw=hw, req_id=0x5B1, resp_id=0x5B9, func_id=0x7DF, is_fd=True)
    with Client(conn, config=cfg) as client:
        response = client.read_data_by_identifier(0xF194)
        print(response)
```

## Backend probing

```python
from isotp_engine.can_device import available_backends as can_backends
from isotp_engine.lin_device import available_backends as lin_backends

print(can_backends())
print(lin_backends())
```

## Where to add a new device backend

- new CAN device → `python/isotp_engine/can_device/backends/`
- new LIN device → `python/isotp_engine/lin_device/backends/`
- shared low-level helpers → `python/isotp_engine/hw/`

Suggested split for larger vendor integrations:

```text
python/isotp_engine/can_device/backends/
  pcan/
    __init__.py
    adapter.py
    sdk.py
  vector/
    __init__.py
    adapter.py
    sdk.py
  tsmaster/
    __init__.py
    adapter.py
    sdk.py
```

## Test

```bash
python -m unittest discover -s tests -t . -p 'test_*.py' -v
```

Suggested layout:

```text
tests/
  bindings/
  lin_device/
  integration/
```
