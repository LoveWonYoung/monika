# Python ctypes binding

## Directory split (CAN / LIN decoupled)

- `can_device/`: CAN hardware + ISO-TP client/worker (independent entrypoint)
- `lin_device/`: LIN hardware + LIN-TP worker (independent entrypoint)
- `bindings/`: FFI binding facade (`bindings.isotp`, `bindings.lintp`)
- `lib/`: legacy ctypes implementation files (kept for compatibility)

Recommended new imports:
- `from can_device import CanTpClient, CanTpWorker, Toomoss`
- `from lin_device import LinTpWorker, ToomossLin`
- `from bindings.isotp import IsoTpEngine, IsoTpEngineWorker, TpConfig`
- `from bindings.lintp import LinTpEngine, LinTpEngineWorker, LinTpConfig`

## Build Rust library

```bash
# from repo root
cargo build --release
```

macOS output:
- `target/release/libisotp_engine.dylib`

Linux output:
- `target/release/libisotp_engine.so`

Windows output:
- `target/release/isotp_engine.dll`

## Run independently (CAN / LIN)

```bash
# from python/ directory
python3 -m can_device.main
python3 -m lin_device.main
```

Legacy entrypoint is still available:

```bash
# from python/ directory
python3 -m main
```

## Run demo (ISO-TP worker-only sample)

```bash
# from python/ directory
python3 -m lib.demo
```

`isotp_engine_ctypes.py` 与 `lintp_engine_ctypes.py` 默认库路径：
- `./bin/<platform-lib-name>`
- `../target/release/<platform-lib-name>`

If needed, pass `lib_path="..."` to `IsoTpEngine(...)` or `LinTpEngine(...)`.

## Stress simulation (dual-end, large payload)

```bash
# from python/ directory
python3 -m lib.stress_sim --cases 2000 --max-req-len 4095 --max-rsp-len 4095
```

`IsoTpEngine` supports batch FFI:
- `on_can_frames([(can_id, data, is_fd), ...], ts_ms=...)`
- `pop_tx_can_frames(max_frames=..., buf_cap=64)`

## Tests

```bash
# from python/ directory
python3 -m unittest discover -s tests -p "test_*.py" -v
```

## Handle UDS NRC 0x78 (ResponsePending)

`isotp_engine_ctypes.py` provides:
- `IsoTpEngine.wait_uds_final_response(step_once=..., ...)`
- `IsoTpEngineWorker.wait_uds_final_response(...)`
- `tx_uds_msg(..., response_timeout_ms=...)` (optional blocking wait)

Behavior:
- If response is `7F xx 78`, it continues waiting.
- If response is other negative response, it raises `UdsNegativeResponseError`.
- If final positive response arrives, it returns payload bytes.
- Optional `response_matcher` can filter stale/unrelated responses.
- `flush_before_send=True` (default in timeout mode) clears queued old UDS payloads before sending.

## Minimal `txfunc/rxfunc` bridge template

Use:
- `step_once(tp, rxfunc, txfunc, ...)`
- `send_uds_and_wait_final(tp, payload, rxfunc, txfunc, ...)`
- `step_once_lin(tp, rxfunc, txfunc, ...)`
- `send_uds_and_wait_final_lin(tp, payload, rxfunc, txfunc, ...)`

See template:
- `lib/real_device_template.py`

Your `rxfunc()` should be non-blocking and return `None` when no frame is available.
