# IsoTpEngine 接入说明（Rust TP 引擎 + Python）

本文档对应当前仓库结构：
- 唯一 Rust 工程在 `myo3/`
- Rust crate / Python 扩展模块名：`isotp_engine`
- Python 代码在 `myo3/py/`

## 1. 作用范围

- 本引擎只做 ISO-TP / LIN-TP 分段与重组（TP-only）。
- UDS 服务语义仍在 Python（或其他宿主语言）处理。
- 不包含硬件 I/O；宿主负责喂 RX 帧并发送 TX 帧。

## 2. 构建产物

### 2.1 构建 C 可用动态库（cdylib）

在仓库根目录执行：

```bash
cargo build --release --manifest-path myo3/Cargo.toml
```

产物位置：
- macOS: `myo3/target/release/libisotp_engine.dylib`
- Linux: `myo3/target/release/libisotp_engine.so`
- Windows: `myo3/target/release/isotp_engine.dll`

头文件：`myo3/isotp_engine.h`

### 2.2 构建并安装 Python 扩展（PyO3）

在 `myo3/` 目录执行：

```bash
maturin develop
```

安装后可直接：

```python
import isotp_engine
```

## 3. Python 使用方式（兼容层）

Python 业务代码建议从这里导入：
- `myo3/py/bindings/isotp.py`
- `myo3/py/bindings/lintp.py`

或直接使用兼容层：
- `myo3/py/lib/isotp_engine_ctypes.py`
- `myo3/py/lib/lintp_engine_ctypes.py`

后端选择逻辑：
- 默认 `MONIKA_TP_BACKEND=auto`：优先 `isotp_engine`（PyO3），失败回退 `ctypes`
- `MONIKA_TP_BACKEND=pyo3`：强制 PyO3
- `MONIKA_TP_BACKEND=ctypes`：强制 ctypes

快速查看当前后端：

```python
from lib import isotp_engine_ctypes as iso
from lib import lintp_engine_ctypes as lin
print(iso.TP_BACKEND, lin.LIN_TP_BACKEND)
```

## 4. 主循环顺序（必须）

建议持续按这个顺序：

1. 从硬件读取 RX 帧并喂给 `on_can_frame` / `on_lin_frame`
2. 调用 `tick(ts_ms)` 推进状态机
3. 循环 `pop_tx_*` 并下发硬件
4. 循环 `rx_uds_msg()` 取完整 TP 负载
5. 循环 `pop_error()` 记录异步错误

时间戳请使用单调时钟（`monotonic_ms()`）。

## 5. 常见接口

ISO-TP：
- `IsoTpEngine(req_id, resp_id, func_id, is_fd, cfg)`
- `on_can_frame(...)`, `on_can_frames(...)`
- `tx_uds_msg(...)`
- `tick(...)`
- `pop_tx_can_frame()`, `pop_tx_can_frames(...)`
- `rx_uds_msg()`, `pop_error()`

LIN-TP：
- `LinTpEngine(req_frame_id, resp_frame_id, req_nad, func_nad, cfg)`
- `set_nad(...)`
- `on_lin_frame(...)`
- `tx_uds_msg(...)`
- `tick(...)`
- `pop_tx_lin_frame()`
- `rx_uds_msg()`, `pop_error()`

## 6. 运行测试

```bash
PYTHONPATH=myo3/py python3 -m unittest \
  myo3/py/tests/test_can_canfd_pack_unpack.py \
  myo3/py/tests/test_lin_pack_unpack.py \
  myo3/py/tests/test_lin_tp_worker_bridge.py
```

## 7. 关键限制

- 单个引擎实例是单线程模型，不要并发调用。
- 功能寻址仅支持单帧负载。
- 同一引擎同一时刻仅一个在途发送，否则返回 `TxBusy`。
