import logging
import time
from collections import deque
from ctypes import byref, c_uint
from typing import Deque, Optional

from ....common.types import RawCanMsg

from ....hw.errors import DeviceInitError, DeviceNotFoundError, DeviceOpenError, DeviceSendError
from ...interface import CanDeviceInterface
from .sdk import (
    CANFD_GetCANSpeedArg,
    CANFD_GetMsg,
    CANFD_Init,
    CANFD_INIT_CONFIG,
    CANFD_MSG,
    CANFD_MSG_FLAG_FDF,
    CANFD_MSG_FLAG_IDE,
    CANFD_MSG_FLAG_ID_MASK,
    CANFD_SendMsg,
    CANFD_StartGetMsg,
    CANFD_StopGetMsg,
    CANFD_SUCCESS,
)
from ....hw.toomoss_usb_device import USB_CloseDevice, USB_OpenDevice, USB_ScanDevice

ToomossCAN1 = 0
ToomossCAN2 = 1
CANFD_DLC_TO_LEN = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
CANFD_LEN_TO_DLC = {length: dlc for dlc, length in enumerate(CANFD_DLC_TO_LEN)}
logger = logging.getLogger(__name__)


def _len_to_device_dlc(payload_len: int, is_fd: bool) -> int:
    if is_fd:
        if payload_len > 64:
            raise ValueError(f"CAN FD payload length must be <= 64, got {payload_len}")
        return payload_len
    if payload_len > 8:
        raise ValueError(f"Classical CAN payload length must be <= 8, got {payload_len}")
    return payload_len


def _len_to_std_dlc(payload_len: int, is_fd: bool) -> int:
    if is_fd:
        dlc = CANFD_LEN_TO_DLC.get(payload_len)
        if dlc is None:
            raise ValueError(f"Invalid CAN FD payload length for standard DLC code: {payload_len}")
        return dlc
    if payload_len > 8:
        raise ValueError(f"Classical CAN payload length must be <= 8, got {payload_len}")
    return payload_len


def _dlc_to_len(dlc: int) -> int:
    if 0 <= dlc <= 64:
        return dlc
    if 0 <= dlc < len(CANFD_DLC_TO_LEN):
        return CANFD_DLC_TO_LEN[dlc]
    return 0


def _format_can_frame(can_id: int, data: bytes, is_fd: bool) -> str:
    frame_type = "CAN-FD" if is_fd else "CAN"
    data_hex = data.hex(" ") if data else ""
    dlc = _len_to_std_dlc(len(data), is_fd)
    return f"ID=0x{can_id:03X} Type={frame_type} DLC={dlc:02} Data=[{data_hex.upper()}]"


def _print_can_frame(direction: str, can_id: int, data: bytes, is_fd: bool) -> None:
    now = time.time()
    # ts = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int((now % 1) * 1000):03d}"
    message = f"{direction} {_format_can_frame(can_id, data, is_fd)}"
    if logging.getLogger().hasHandlers():
        logger.info(message)
    else:
        print(message)


class Toomoss(CanDeviceInterface):
    """Hardware adapter over USB2XXX CAN/CAN-FD API.

    `min_tx_interval_s` enforces a minimum time between the start of consecutive sends
    (default 2 ms) for devices that require spacing. Set to 0 to disable.

    `min_rx_poll_interval_s` optionally spaces calls to the USB `GetMsg` path only (not
    when draining the internal buffer); default 0 does not add RX latency.
    """

    def __init__(
        self,
        channel: int = ToomossCAN1,
        rx_buffer_size: int = 1024,
        poll_batch_size: int = 1024,
        log_frames: bool = True,
        min_tx_interval_s: float = 0.002,
        min_rx_poll_interval_s: float = 0.0,
    ):
        self._channel = channel
        self._buf: Deque[RawCanMsg] = deque(maxlen=max(1, rx_buffer_size))
        self._poll_batch_size = max(1, poll_batch_size)
        self._log_frames = log_frames
        self._dropped_frames = 0
        self._min_tx_interval_s = max(0.0, float(min_tx_interval_s))
        self._min_rx_poll_interval_s = max(0.0, float(min_rx_poll_interval_s))
        self._last_tx_monotonic: Optional[float] = None
        self._last_getmsg_monotonic: Optional[float] = None

        self._dev_handles = (c_uint * 20)()
        self._dev_handle: Optional[int] = None
        self._started = False
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

        can_cfg = CANFD_INIT_CONFIG()
        can_cfg.Mode=         0
        can_cfg.RetrySend=    1
        can_cfg.ISOCRCEnable= 1
        can_cfg.ResEnable=    1
        can_cfg.NBT_BRP=      1
        can_cfg.NBT_SEG1=     59
        can_cfg.NBT_SEG2=     20
        can_cfg.NBT_SJW=      2
        can_cfg.DBT_BRP=      1
        can_cfg.DBT_SEG1=     14
        can_cfg.DBT_SEG2=     5
        can_cfg.DBT_SJW=      2
        ret = CANFD_GetCANSpeedArg(self._dev_handle, byref(can_cfg), 500000, 2000000)
        if ret != CANFD_SUCCESS:
            self.close()
            raise DeviceInitError(f"Failed to get CAN speed arguments, code={ret}")

        ret = CANFD_Init(self._dev_handle, self._channel, byref(can_cfg))
        if ret != CANFD_SUCCESS:
            self.close()
            raise DeviceInitError(f"Failed to initialize CAN channel={self._channel}, code={ret}")

        ret = CANFD_StartGetMsg(self._dev_handle, self._channel)
        if ret != CANFD_SUCCESS:
            self.close()
            raise DeviceInitError(f"Failed to start RX on CAN channel={self._channel}, code={ret}")
        self._started = True

    def close(self) -> None:
        if self._closed:
            return

        if self._dev_handle is not None and self._started:
            CANFD_StopGetMsg(self._dev_handle, self._channel)
            self._started = False

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

    def txfn(self, can_id: int, data: bytes, is_fd: bool) -> None:
        if self._dev_handle is None:
            raise DeviceOpenError("Device is not open")

        if self._min_tx_interval_s > 0 and self._last_tx_monotonic is not None:
            wait = self._min_tx_interval_s - (time.perf_counter() - self._last_tx_monotonic)
            if wait > 0:
                time.sleep(wait)

        if self._log_frames:
            _print_can_frame("TX", can_id, data, is_fd)

        dlc = _len_to_device_dlc(len(data), is_fd)

        msg_arr = (CANFD_MSG * 1)()
        msg = msg_arr[0]
        msg.DLC = dlc
        msg.Flags = CANFD_MSG_FLAG_FDF if is_fd else 0

        raw_id = int(can_id) & CANFD_MSG_FLAG_ID_MASK
        if raw_id > 0x7FF:
            raw_id |= CANFD_MSG_FLAG_IDE
        msg.ID = raw_id

        for i, b in enumerate(data):
            msg.Data[i] = b

        sent_num = CANFD_SendMsg(self._dev_handle, self._channel, byref(msg_arr), 1)
        if sent_num < 0:
            raise DeviceSendError(f"Failed to send CAN frame: {_format_can_frame(can_id, data, is_fd)}")

        self._last_tx_monotonic = time.perf_counter()

    def rxfn(self) -> Optional[RawCanMsg]:
        if self._dev_handle is None:
            raise DeviceOpenError("Device is not open")

        if self._buf:
            return self._buf.popleft()

        if self._min_rx_poll_interval_s > 0:
            now = time.perf_counter()
            if self._last_getmsg_monotonic is not None:
                wait = self._min_rx_poll_interval_s - (now - self._last_getmsg_monotonic)
                if wait > 0:
                    time.sleep(wait)

        msg_buf = (CANFD_MSG * self._poll_batch_size)()
        frames = CANFD_GetMsg(self._dev_handle, self._channel, byref(msg_buf), self._poll_batch_size)
        self._last_getmsg_monotonic = time.perf_counter()
        if frames <= 0:
            return None

        for i in range(frames):
            frame = msg_buf[i]
            data_len = max(0, min(64, _dlc_to_len(int(frame.DLC))))
            can_id = int(frame.ID) & CANFD_MSG_FLAG_ID_MASK
            is_fd = bool(int(frame.Flags) & CANFD_MSG_FLAG_FDF)
            data = bytes(frame.Data[:data_len])
            if self._log_frames:
                _print_can_frame("RX", can_id, data, is_fd)

            # Some USB2XXX devices occasionally report glitch frames with DLC=0.
            # ISO-TP cannot consume empty CAN payloads, so drop them at the HW adapter boundary.
            if not data:
                self._dropped_frames += 1
                logger.warning(
                    "Dropped empty RX frame from Toomoss: ID=0x%X Flags=0x%X DLC=%s",
                    int(frame.ID) & CANFD_MSG_FLAG_ID_MASK,
                    int(frame.Flags),
                    int(frame.DLC),
                )
                continue

            if len(self._buf) == self._buf.maxlen:
                self._dropped_frames += 1
            self._buf.append(RawCanMsg(id=can_id, data=data, isfd=is_fd))

        if self._buf:
            return self._buf.popleft()
        return None

