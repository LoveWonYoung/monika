# Python ctypes binding

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

## Run demo

```bash
# from this python workspace root
python -m lib.demo
```

`isotp_engine_ctypes.py` 与 `lintp_engine_ctypes.py` 都会加载默认库路径：
- `./bin/<platform-lib-name>`
- `../target/release/<platform-lib-name>`

If needed, pass `lib_path="..."` to `IsoTpEngine(...)` or `LinTpEngine(...)`.

## Stress simulation (dual-end, large payload)

This script creates two Rust ISO-TP endpoints in one Python process:
- tester endpoint (`0x7E0 -> 0x7E8`)
- ecu endpoint (`0x7E8 -> 0x7E0`)

It pumps CAN frames between both sides, feeds `tick()`, and validates request/response payload integrity.

```bash
# from this python workspace root
python -m lib.stress_sim --cases 2000 --max-req-len 4095 --max-rsp-len 4095
```

`IsoTpEngine` 现已支持批量 FFI：
- `on_can_frames([(can_id, data, is_fd), ...], ts_ms=...)`
- `pop_tx_can_frames(max_frames=..., buf_cap=64)`

`step_once(...)` 与 `stress_sim.py` 默认优先走批量路径（旧单帧接口仍可用）。

## CAN / CAN-FD pack-unpack tests

```bash
# from this python workspace root
python -m unittest discover -s tests -p "test_*.py" -v
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
- `step_once_lin(tp, rxfunc, txfunc, ...)`（from `lintp_engine_ctypes.py`）
- `send_uds_and_wait_final_lin(tp, payload, rxfunc, txfunc, ...)`（from `lintp_engine_ctypes.py`）

See runnable template:
- `lib/real_device_template.py`

Your `rxfunc()` should be non-blocking and return `None` when no frame is available.

`LinTpEngine` 主要接口：
- `on_lin_frame(frame_id, data, ts_ms=...)`
- `tx_uds_msg(payload, functional=False, ts_ms=...)`
- `tick(ts_ms=...)`
- `pop_tx_lin_frame()`
- `rx_uds_msg()`
- `pop_error()`

`LinTpEngineWorker`（后台线程独占引擎，接口风格与 `IsoTpEngineWorker` 一致）：
- `on_lin_frame(frame_id, data)`
- `tx_uds_msg(payload, functional=False, response_timeout_ms=...)`
- `pop_tx_lin_frame(timeout_s=...)`
- `pop_rx_uds_msg(timeout_s=...)`
- `wait_uds_final_response(...)`
- `pop_error(timeout_s=...)`

## Toomoss LIN (Master) bridge

`ToomossLin` 位于 `devices/toomoss/toomoss_usb2lin.py`，参考 `toomoss.go` 的调用方式，封装了：
- `write_message(frame_id, data)`（对应 `MW`）
- `request_slave_response(frame_id)`（对应 `MR`）
- `lin_break()`（对应 `BK`）

可直接配合 `devices.tp_clients.LinTpWorker`：

```python
from devices.toomoss import ToomossLin
from devices.tp_clients import LinTpWorker

with ToomossLin(channel=0, baudrate=19200, master=True) as hw:
    with LinTpWorker(
        hw=hw,
        req_frame_id=0x3C,
        resp_frame_id=0x3D,
        req_nad=0x10,
        func_nad=0x7F,
        resp_poll_interval_ms=15,  # 3D header poll period, typical values: 10/15/20
    ) as dev:
        rsp = dev.uds_request(bytes([0x22, 0xF1, 0x90]))
        print(rsp.hex(" "))
```

推荐导入：
- `from devices.toomoss import ToomossLin`
- `from devices import ToomossLin`
