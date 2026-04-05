# Python ctypes binding

## Build Rust library

```bash
cd /Users/lianmin/Documents/GitHub/canbuskit/IsoTpEngine
cargo build --release
```

macOS output:
- `target/release/libisotp_engine.dylib`

Linux output:
- `target/release/libisotp_engine.so`

Windows output:
- `target/release/isotp_engine.dll`

## Run demo

```bash
cd /Users/lianmin/Documents/GitHub/canbuskit/IsoTpEngine/python
python3 demo.py
```

`isotp_engine_ctypes.py` will load the default library path:
- `../target/release/<platform-lib-name>`

If needed, pass `lib_path="..."` to `IsoTpEngine(...)`.

## Stress simulation (dual-end, large payload)

This script creates two Rust ISO-TP endpoints in one Python process:
- tester endpoint (`0x7E0 -> 0x7E8`)
- ecu endpoint (`0x7E8 -> 0x7E0`)

It pumps CAN frames between both sides, feeds `tick()`, and validates request/response payload integrity.

```bash
cd /Users/lianmin/Documents/GitHub/canbuskit/IsoTpEngine
python3 python/stress_sim.py --cases 2000 --max-req-len 4095 --max-rsp-len 4095
```

## CAN / CAN-FD pack-unpack tests

```bash
cd /Users/lianmin/Documents/GitHub/canbuskit/IsoTpEngine
python3 -m unittest discover -s python/tests -p "test_*.py" -v
```

## Handle UDS NRC 0x78 (ResponsePending)

`isotp_engine_ctypes.py` now provides:
- `IsoTpEngine.wait_uds_final_response(step_once=..., ...)`
- `IsoTpEngineWorker.wait_uds_final_response(...)`
- `tx_uds_msg(..., response_timeout_ms=...)` (optional blocking wait)

Behavior:
- If response is `7F xx 78`, it continues waiting.
- If response is other negative response, it raises `UdsNegativeResponseError`.
- If final positive response arrives, it returns payload bytes.
- Optional `response_matcher` can filter out stale/unrelated responses.
- `flush_before_send=True` (default in timeout mode) clears queued old UDS payloads before sending.

## Minimal `txfunc/rxfunc` bridge template

Use:
- `step_once(tp, rxfunc, txfunc, ...)`
- `send_uds_and_wait_final(tp, payload, rxfunc, txfunc, ...)`

See runnable template:
- `python/real_device_template.py`

Your `rxfunc()` should be non-blocking and return `None` when no frame is available.
