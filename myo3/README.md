# myo3 Python package

This directory contains a mixed Rust/Python project built with `maturin`.
The Rust side provides the transport engine, and the Python side provides the installable package layout, device adapters, demos, and tests.

## Layout

This directory is the real project root for the mixed Rust/Python package.
The repository root only acts as a thin workspace shell.

Top-level directories:

- `src/` — Rust transport engine and PyO3 exports
- `python/isotp_engine/` — installable Python package
- `tests/` — Python unit tests
- `examples/` — runnable source-tree demos
- `scripts/` — manual / ad-hoc scripts

Installed package layout:

- `isotp_engine` — top-level Python package
- `isotp_engine._native` — Rust extension module built by maturin / PyO3
- `isotp_engine.bindings` — high-level transport API
- `isotp_engine.can_device` — CAN device workers, interfaces, fake devices, backends
- `isotp_engine.lin_device` — LIN device workers, interfaces, and backends
- `isotp_engine.hw` — shared hardware-facing helpers
- `isotp_engine.common` — shared data types
- `isotp_engine.utils` — utility helpers

## Recommended mental model

Think of the project in layers:

1. **Rust transport engine**
   - ISO-TP / LIN-TP logic
   - exposed to Python as `isotp_engine._native`

2. **Python bindings layer**
   - `isotp_engine.bindings`
   - friendly Python wrapper around `_native`

3. **Device worker layer**
   - `isotp_engine.can_device.worker`
   - `isotp_engine.lin_device.worker`
   - bridges hardware read/write functions with transport engine workers

4. **Backend layer**
   - `isotp_engine.can_device.backends`
   - `isotp_engine.lin_device.backends`
   - concrete device implementations such as Toomoss

5. **Shared utilities / types**
   - `isotp_engine.common`
   - `isotp_engine.hw`
   - `isotp_engine.utils`

---

## Development setup

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

## Build wheel

From `myo3/`:

```bash
python -m maturin build
```

Built wheels will appear under `target/wheels/`.

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
python examples/can_demo.py
python examples/lin_demo.py
python examples/udsoncan_demo.py
```

## Import examples

```python
from isotp_engine.bindings import IsoTpEngine, LinTpEngine, TpConfig, LinTpConfig
from isotp_engine.can_device.worker import CanTpWorker
from isotp_engine.can_device.fake import FakeEcu
from isotp_engine.can_device import UdsoncanIsoTpConnection
from isotp_engine.lin_device.worker import LinTpWorker
from isotp_engine.common.types import RawCanMsg, RawLinMsg
```

## udsoncan integration

If you use `python-udsoncan`, you can use `UdsoncanIsoTpConnection` as a custom `BaseConnection` implementation.
This keeps TP in Rust (`IsoTpEngine`) and lets `udsoncan.Client` keep UDS service logic/timing.

```python
import udsoncan.configs
from udsoncan.client import Client
from isotp_engine.can_device import Toomoss, UdsoncanIsoTpConnection

cfg = udsoncan.configs.default_client_config.copy()
cfg["request_timeout"] = 5.0

with Toomoss() as hw:
    conn = UdsoncanIsoTpConnection(hw=hw, req_id=0x5B1, resp_id=0x5B9, func_id=0x7DF, is_fd=True)
    with Client(conn, config=cfg) as client:
        response = client.read_data_by_identifier(0xF194)
        print(response)
```

---

## Where to add a new device backend

Short version:

- **new CAN device** → add it under `python/isotp_engine/can_device/backends/`
- **new LIN device** → add it under `python/isotp_engine/lin_device/backends/`
- **shared low-level helpers** → add them under `python/isotp_engine/hw/`

### Example: adding a PCAN backend

If you want to support **PCAN**, I would put it here:

```text
python/isotp_engine/can_device/backends/pcan.py
```

If the PCAN implementation needs vendor-specific ctypes definitions or helper wrappers, you can split it like this:

```text
python/isotp_engine/can_device/backends/
  pcan.py
  pcan_basic.py
```

Suggested responsibilities:

- `pcan.py`
  - high-level backend class, e.g. `PcanCanDevice`
  - implements the read/write behavior expected by `CanTpWorker`
  - converts vendor frames into `RawCanMsg`

- `pcan_basic.py`
  - raw ctypes / SDK constants / structure definitions
  - thin wrapper over vendor API

If some code is reusable across multiple backends, move that shared part into `isotp_engine.hw` instead of duplicating it.

---

## What interface a new CAN device should satisfy

A CAN backend should match the interface used by `CanTpWorker`.
Practically, that means implementing methods compatible with:

```python
class CanDeviceInterface:
    def rxfn(self) -> Optional[RawCanMsg]:
        ...

    def txfn(self, can_id: int, data: bytes, is_fd: bool) -> None:
        ...
```

So for a new backend such as PCAN, the important part is:

- `rxfn()` returns one `RawCanMsg` or `None`
- `txfn(can_id, data, is_fd)` sends one CAN / CAN-FD frame

You can also probe backend availability at runtime with:

```python
from isotp_engine.can_device import available_backends
print(available_backends())
```

Optional but recommended:

- context manager support: `__enter__` / `__exit__`
- `close()` method
- device-specific configuration in `__init__`
- consistent exceptions using `isotp_engine.hw.errors`

A very typical shape would be:

```python
from isotp_engine.common.types import RawCanMsg
from isotp_engine.can_device.interface import CanDeviceInterface


class PcanCanDevice(CanDeviceInterface):
    def __init__(self, channel: str = "PCAN_USBBUS1", bitrate: int = 500000):
        ...

    def rxfn(self):
        # poll one frame from PCAN SDK
        # return RawCanMsg(...) or None
        ...

    def txfn(self, can_id: int, data: bytes, is_fd: bool) -> None:
        # send one frame through PCAN SDK
        ...

    def close(self) -> None:
        ...
```

Then usage stays clean:

```python
from isotp_engine.can_device.worker import CanTpWorker
from isotp_engine.can_device.backends.pcan import PcanCanDevice

with PcanCanDevice(channel="PCAN_USBBUS1", bitrate=500000) as hw:
    with CanTpWorker(hw=hw, req_id=0x7E0, resp_id=0x7E8, func_id=0x7DF) as dev:
        rsp = dev.uds_request(bytes([0x22, 0xF1, 0x90]))
        print(rsp)
```

---

## What interface a new LIN device should satisfy

A LIN backend used by `LinTpWorker` should provide methods compatible with:

- `request_slave_response(frame_id)`
- `rxfn()`
- `txfn(frame_id, data)`

So a future LIN backend would live under:

```text
python/isotp_engine/lin_device/backends/
```

and implement the same expectations as the current Toomoss LIN backend.

A matching runtime probe also exists for LIN backends:

```python
from isotp_engine.lin_device import available_backends
print(available_backends())
```

---

## When to put code in `hw/` instead of `backends/`

Put code in `hw/` when it is:

- shared by multiple backends
- low-level vendor-neutral helper code
- common error types
- common device loading / library loading logic

Put code in `backends/` when it is:

- specific to one hardware vendor
- specific to one transport side (CAN vs LIN)
- mainly an adapter from vendor API to this package's worker interface

A good rule of thumb:

- **vendor-specific adapter** → `backends/`
- **shared plumbing** → `hw/`

---

## Recommended pattern for adding a new backend

If you add PCAN later, I would do it in this order:

1. Add `python/isotp_engine/can_device/backends/pcan.py`
2. Add SDK-specific wrapper if needed, e.g. `pcan_basic.py`
3. Make it implement `CanDeviceInterface`
4. Add a small fake-free unit/integration test if practical
5. Optionally add an example under `examples/pcan_demo.py`
6. Export it in `python/isotp_engine/can_device/__init__.py` if you want it public

---

## Notes

- The legacy `py/` source tree has been retired in favor of `python/isotp_engine/`.
- Tests no longer rely on `PYTHONPATH=myo3/py` hacks.
- `maturin` is configured to build the Rust extension as `isotp_engine._native`.
- The package layout is now intended for long-term maintenance, not just local script execution.
