import logging
from collections import deque
from ctypes import byref, c_uint
from typing import Deque, Optional

from core.types import RawLinMsg

from ..exceptions import DeviceInitError, DeviceNotFoundError, DeviceOpenError, DeviceSendError
from .toomoss_usb_device import USB_CloseDevice, USB_OpenDevice, USB_ScanDevice
from .toomoss_usb2lin_ex import (
    LIN_EX_CHECK_EXT,
    LIN_EX_CHECK_STD,
    LIN_EX_Init,
    LIN_EX_MASTER,
    LIN_EX_MSG,
    LIN_EX_MSG_TYPE_BK,
    LIN_EX_MSG_TYPE_MR,
    LIN_EX_MSG_TYPE_MW,
    LIN_EX_MasterSync,
    LIN_EX_SUCCESS,
)

ToomossLIN1 = 0
ToomossLIN2 = 1

LIN_MASTER_DIAGNOSTIC_FRAME_ID = 0x3C
LIN_SLAVE_DIAGNOSTIC_FRAME_ID = 0x3D

logger = logging.getLogger(__name__)


def _format_lin_frame(frame_id: int, data: bytes, check_type: int) -> str:
    data_hex = data.hex(" ") if data else ""
    return f"ID=0x{frame_id:02X} Len={len(data):02d} CheckType={check_type} Data=[{data_hex}]"


def _print_lin_frame(direction: str, frame_id: int, data: bytes, check_type: int) -> None:
    message = f"{direction} {_format_lin_frame(frame_id, data, check_type)}"
    if logging.getLogger().hasHandlers():
        logger.info(message)
    else:
        print(message)


class ToomossLin:
    """Hardware adapter over USB2XXX LIN_EX API (Master mode)."""

    def __init__(
        self,
        channel: int = ToomossLIN1,
        baudrate: int = 19200,
        master: bool = True,
        rx_buffer_size: int = 1024,
        log_frames: bool = True,
    ):
        self._channel = int(channel)
        self._baudrate = int(baudrate)
        self._master = LIN_EX_MASTER if master else 0
        self._buf: Deque[RawLinMsg] = deque(maxlen=max(1, rx_buffer_size))
        self._log_frames = log_frames
        self._dropped_frames = 0

        self._dev_handles = (c_uint * 20)()
        self._dev_handle: Optional[int] = None
        self._closed = False

        self._open()

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    def _open(self) -> None:
        ret = USB_ScanDevice(byref(self._dev_handles))
        if ret == 0:
            raise DeviceNotFoundError("No USB2XXX device connected")

        self._dev_handle = int(self._dev_handles[0])
        if not bool(USB_OpenDevice(self._dev_handle)):
            raise DeviceOpenError(f"Failed to open USB2XXX device handle={self._dev_handle}")

        ret = LIN_EX_Init(self._dev_handle, self._channel, self._baudrate, self._master)
        if ret != LIN_EX_SUCCESS:
            self.close()
            raise DeviceInitError(
                f"Failed to initialize LIN channel={self._channel}, baudrate={self._baudrate}, code={ret}"
            )

    def close(self) -> None:
        if self._closed:
            return

        if self._dev_handle is not None:
            USB_CloseDevice(self._dev_handle)
            self._dev_handle = None

        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _resolve_check_type(self, frame_id: int, check_type: Optional[int]) -> int:
        if check_type is not None:
            return int(check_type)
        if frame_id in (LIN_MASTER_DIAGNOSTIC_FRAME_ID, LIN_SLAVE_DIAGNOSTIC_FRAME_ID):
            return LIN_EX_CHECK_STD
        return LIN_EX_CHECK_EXT

    def write_message(self, frame_id: int, data: bytes, check_type: Optional[int] = None) -> None:
        if self._dev_handle is None:
            raise DeviceOpenError("Device is not open")
        if len(data) > 8:
            raise DeviceSendError(f"LIN payload length must be <= 8, got {len(data)}")

        msg_arr = (LIN_EX_MSG * 1)()
        out_arr = (LIN_EX_MSG * 1)()
        msg = msg_arr[0]
        msg.MsgType = LIN_EX_MSG_TYPE_MW
        msg.DataLen = len(data)
        msg.PID = int(frame_id) & 0xFF
        msg.CheckType = self._resolve_check_type(frame_id, check_type)
        for i, b in enumerate(data):
            msg.Data[i] = b

        ret = LIN_EX_MasterSync(self._dev_handle, self._channel, msg_arr, out_arr, 1)
        if ret <= 0:
            raise DeviceSendError(
                f"Failed to send LIN frame: {_format_lin_frame(frame_id, data, int(msg.CheckType))}, ret={ret}"
            )

        if self._log_frames:
            _print_lin_frame("TX", frame_id, data, int(msg.CheckType))

    # Alias for bridge compatibility.
    def txfn(self, frame_id: int, data: bytes) -> None:
        self.write_message(frame_id=frame_id, data=data)

    def request_slave_response(self, frame_id: int) -> Optional[RawLinMsg]:
        if self._dev_handle is None:
            raise DeviceOpenError("Device is not open")

        msg_arr = (LIN_EX_MSG * 1)()
        out_arr = (LIN_EX_MSG * 1)()
        msg = msg_arr[0]
        msg.MsgType = LIN_EX_MSG_TYPE_MR
        msg.PID = int(frame_id) & 0xFF

        ret = LIN_EX_MasterSync(self._dev_handle, self._channel, msg_arr, out_arr, 1)
        if ret <= 0:
            return None

        out = out_arr[0]
        data_len = max(0, min(8, int(out.DataLen)))
        data = bytes(out.Data[:data_len])
        rx = RawLinMsg(id=int(frame_id) & 0xFF, data=data)

        if self._log_frames:
            _print_lin_frame("RX", frame_id, data, int(out.CheckType))

        if len(self._buf) == self._buf.maxlen:
            self._dropped_frames += 1
        self._buf.append(rx)
        return rx

    def rxfn(self) -> Optional[RawLinMsg]:
        if self._buf:
            return self._buf.popleft()
        return None

    def lin_break(self, break_bits: int = 20) -> bool:
        if self._dev_handle is None:
            raise DeviceOpenError("Device is not open")

        msg_arr = (LIN_EX_MSG * 1)()
        out_arr = (LIN_EX_MSG * 1)()
        msg = msg_arr[0]
        msg.MsgType = LIN_EX_MSG_TYPE_BK
        msg.Timestamp = int(break_bits) & 0xFFFFFFFF
        ret = LIN_EX_MasterSync(self._dev_handle, self._channel, msg_arr, out_arr, 1)
        return ret > 0


__all__ = [
    "ToomossLIN1",
    "ToomossLIN2",
    "LIN_MASTER_DIAGNOSTIC_FRAME_ID",
    "LIN_SLAVE_DIAGNOSTIC_FRAME_ID",
    "ToomossLin",
]
