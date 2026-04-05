# IsoTpEngine 接入说明（Rust TP 引擎 + Python ctypes）

本文说明：如何在真实 CAN/CAN-FD 设备上接入本仓库的 ISO-TP 传输层引擎。

## 1. 作用范围

- 本引擎只做 ISO-TP 分段/重组（TP-only）。
- UDS 服务语义仍在 Python（或其他宿主语言）处理。
- 不包含硬件 I/O；宿主负责喂 RX CAN 帧、发送 TX CAN 帧。

## 2. 构建动态库

在仓库根目录执行：

```bash
cargo build --release
```

产物：
- macOS: `target/release/libisotp_engine.dylib`
- Linux: `target/release/libisotp_engine.so`
- Windows: `target/release/isotp_engine.dll`

`python/isotp_engine_ctypes.py` 默认从 `../target/release/` 载入对应动态库。

## 3. 需要调用的接口

Python 绑定（`python/isotp_engine_ctypes.py`）：

- `IsoTpEngine(req_id, resp_id, func_id, is_fd, cfg)`
- `tx_uds_msg(payload, functional=False, ts_ms=...)`
- `on_can_frame(can_id, data, is_fd, ts_ms=...)`
- `on_can_frames([(can_id, data, is_fd), ...], ts_ms=...)`（批量注入）
- `tick(ts_ms=...)`
- `pop_tx_can_frame()`
- `pop_tx_can_frames(max_frames=..., buf_cap=64)`（批量弹出）
- `rx_uds_msg()`
- `pop_error()`

C ABI 返回语义：
- `0`: 正常（且“无可弹出项”）
- `1`: 成功弹出一项（用于 `pop_*`）
- `<0`: 错误码

## 4. ID 含义

- `req_id`：测试仪 -> ECU 的物理请求 ID（如 `0x7E0`）
- `resp_id`：ECU -> 测试仪的物理响应 ID（如 `0x7E8`）
- `func_id`：功能寻址广播 ID（如 `0x7DF`）

注意：
- `on_can_frame()` 只处理匹配当前会话的帧（`id == resp_id` 且 `is_fd` 一致）。
- 不匹配帧会被忽略，不会报错。

## 5. 配置项（`TpConfig`）

默认值：
- `n_bs_ms = 1000`
- `n_cr_ms = 1000`
- `stmin_ms = 20`
- `block_size = 0`
- `tx_padding = Dlc`

说明：
- `block_size=0` 表示对端可连续发送/接收，不按块等待 FC。
- `tx_padding=Dlc` 时，CAN-FD 会按 DLC 档位补齐（8/12/16/20/24/32/48/64）。
- 当前实现里 `Raw` 与 `Min8` 都会至少补齐到 8 字节（按当前项目约定）。
- 当前实现里接收侧单条 ISO-TP PDU 最大为 8KB（8192 字节），超限首帧会被拒绝并上报解析错误。

## 6. 主循环顺序（必须）

建议持续按这个顺序执行：

1. 从硬件读取 CAN/CAN-FD 帧。
2. 对每帧调用 `on_can_frame(...)` 注入引擎。
3. 调用 `tick(...)` 推进超时与状态机。
4. 循环调用 `pop_tx_can_frame()`，把待发帧写回硬件总线。
5. 循环调用 `rx_uds_msg()`，取完整 UDS 负载。
6. 循环调用 `pop_error()`，记录异步传输错误。

`tick()` 调用频率会直接影响吞吐和超时精度，建议固定高频调用（如 1ms 周期）。

## 7. 最小实机模板

```python
import time
from isotp_engine_ctypes import IsoTpEngine, TpConfig, monotonic_ms


def run(bus):
    # bus API 为示意：
    # - bus.recv_nonblocking() -> 可迭代 frame
    # - bus.send(id, data, is_fd)
    tp = IsoTpEngine(
        req_id=0x7E0,
        resp_id=0x7E8,
        func_id=0x7DF,
        is_fd=False,  # CAN-FD 会话请设为 True
        cfg=TpConfig(n_bs_ms=1000, n_cr_ms=1000, stmin_ms=0, block_size=0),
    )

    tp.tx_uds_msg(bytes([0x22, 0xF1, 0x90]), functional=False, ts_ms=monotonic_ms())

    try:
        while True:
            now = monotonic_ms()

            for fr in bus.recv_nonblocking():
                tp.on_can_frame(
                    can_id=fr.can_id,
                    data=fr.data,
                    is_fd=fr.is_fd,
                    ts_ms=now,
                )

            tp.tick(ts_ms=now)

            while True:
                out = tp.pop_tx_can_frame()
                if out is None:
                    break
                can_id, data, is_fd = out
                bus.send(can_id, data, is_fd)

            while True:
                uds = tp.rx_uds_msg()
                if uds is None:
                    break
                print("UDS RX:", uds.hex(" "))

            while True:
                err = tp.pop_error()
                if err is None:
                    break
                print("ISO-TP error code:", err)

            time.sleep(0.001)
    finally:
        tp.close()
```

## 8. 关键注意事项

- 时间戳必须使用单调时钟（`monotonic_ms()`），不要用墙上时钟。
- 即使总线暂时无数据，也要持续调用 `tick()`。
- 多帧发送必须收到 ECU 的 FC（`0x30 ...`）才会继续发 CF。
- 功能寻址仅支持单帧负载（multi-frame functional 不支持）。
- 单个引擎实例同一时刻只支持一个在途发送（否则 `TxBusy`）。
- 同一个引擎实例是单线程模型，不要并发调用其方法。

## 9. 如需后台线程

可使用 `IsoTpEngineWorker`（`python/isotp_engine_ctypes.py`）让一个线程独占引擎。其他线程通过队列通信，不要直接并发调引擎方法。
