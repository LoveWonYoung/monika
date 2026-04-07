# Python 使用说明

此目录是 Python 侧代码根目录（`myo3/py/`）。

## 目录结构

- `bindings/`：推荐入口（`bindings.isotp` / `bindings.lintp`）
- `lib/`：兼容层（保留历史导入路径）
- `can_device/`：CAN 设备与客户端
- `lin_device/`：LIN 设备与客户端
- `tests/`：单元测试

## 后端选择（重要）

`lib/isotp_engine_ctypes.py` 与 `lib/lintp_engine_ctypes.py` 默认行为：

- `MONIKA_TP_BACKEND=auto`（默认）：优先 PyO3 模块 `isotp_engine`，失败回退 ctypes 动态库
- `MONIKA_TP_BACKEND=pyo3`：强制 PyO3
- `MONIKA_TP_BACKEND=ctypes`：强制 ctypes

查看当前后端：

```python
from lib import isotp_engine_ctypes as iso
from lib import lintp_engine_ctypes as lin
print(iso.TP_BACKEND, lin.LIN_TP_BACKEND)
```

## 构建

在 `myo3/` 目录执行：

```bash
# 构建 C 动态库
cargo build --release

# 安装 Python 扩展
maturin develop
```

动态库默认搜索路径（ctypes 模式）：
- `myo3/py/bin/<platform-lib>`
- `myo3/target/release/<platform-lib>`

## 运行

在 `myo3/py/` 目录执行：

```bash
python3 -m can_device.main
python3 -m lin_device.main
```

## 测试

在仓库根目录执行：

```bash
PYTHONPATH=myo3/py python3 -m unittest discover -s myo3/py/tests -p "test_*.py" -v
```
