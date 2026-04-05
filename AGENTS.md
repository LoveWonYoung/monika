# IsoTpEngine Agent Notes

## Scope
- This crate is **TP-only** (ISO-TP segmentation/reassembly).
- UDS service semantics stay in Python (or other host language).
- Hardware I/O is out of scope; host app feeds RX CAN frames and drains TX CAN frames.

## Runtime Model
- Single-threaded, non-blocking API.
- No internal threads, no async runtime.
- Host drives time using `tick(ts_ms)` with monotonic milliseconds.

## Public API Contract
- `IsoTpEngine::init(req_id, resp_id, func_id, is_fd, cfg)` initializes a client-side TP engine.
- `tx_uds_msg(payload, functional, ts_ms)` enqueues a UDS payload for TP send.
- `on_can_frame(id, data, is_fd, ts_ms)` ingests raw CAN/CAN-FD frames read by host.
- `tick(ts_ms)` advances timers (N_Bs/N_Cr/STmin-driven behavior).
- `pop_tx_can_frame()` drains CAN frames ready for host transmit.
- `rx_uds_msg()` drains completed TP payloads.
- `pop_error()` drains asynchronous TP errors.

## C ABI (for Python ctypes/cffi)
- `isotp_default_config() -> IsoTpConfigC`
- `isotp_engine_new(...) -> i32` and `isotp_engine_free(engine)`
- `isotp_on_can_frame(...) -> i32`
- `isotp_tx_uds_msg(...) -> i32`
- `isotp_tick(...) -> i32`
- `isotp_pop_tx_can_frame(...) -> i32` (returns `1` when one frame is produced, `0` when queue is empty)
- `isotp_rx_uds_msg(...) -> i32` (returns `1` when one TP payload is available, `0` when queue is empty)
- `isotp_pop_error(...) -> i32` (returns `1` when one error code is available, `0` when queue is empty)

## Simplifications (Intentional)
- Addressing mode is fixed to normal IDs (`req_id`, `resp_id`, `func_id`).
- Supports CAN and CAN FD only.
- Functional addressing supports single-frame payloads only.
- No padding policy yet (raw minimal payload length output).
- No external synchronization primitives; caller is responsible for thread safety.

## Integration Expectations
- Host loop order should be: feed RX frames -> call `tick()` -> drain TX queue -> poll RX UDS queue.
- `ts_ms` should come from monotonic clock, not wall clock.
- Errors from `on_can_frame`/`tick` should be logged and `pop_error()` should be polled.

## Future Extensions
- Python bindings (PyO3 or cffi bridge).
- Optional CAN FD DLC padding policy.
- Optional stricter acceptance/filtering rules.
