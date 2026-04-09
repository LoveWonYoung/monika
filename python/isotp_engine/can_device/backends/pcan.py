import logging
from collections import deque
from typing import Deque, Optional, Union

from ...common.types import RawCanMsg
from ...hw.errors import DeviceInitError, DeviceOpenError, DeviceSendError
from ..interface import CanDeviceInterface
from .pcan_sdk import (
    CANFD_DLC_TO_LEN,
    CANFD_LEN_TO_DLC,
    PCAN_BITRATES,
    PCAN_CHANNEL_NAMES,
    PCAN_ERROR_BUSHEAVY,
    PCAN_ERROR_BUSLIGHT,
    PCAN_ERROR_BUSOFF,
    PCAN_ERROR_BUSPASSIVE,
    PCAN_ERROR_OK,
    PCAN_ERROR_QRCVEMPTY,
    PCAN_MESSAGE_ERRFRAME,
    PCAN_MESSAGE_EXTENDED,
    PCAN_MESSAGE_FD,
    PCAN_MESSAGE_RTR,
    PCAN_MESSAGE_STANDARD,
    PCAN_USBBUS1,
    PCANBasicDLL,
    TPCANMsg,
    TPCANMsgFD,
)

logger = logging.getLogger(__name__)


def _format_can_frame(can_id: int, data: bytes, is_fd: bool) -> str:
    frame_type = "CAN-FD" if is_fd else "CAN"
    dlc = CANFD_LEN_TO_DLC[len(data)] if is_fd else len(data)
    return f"ID=0x{can_id:X} Type={frame_type} DLC={dlc} Data=[{data.hex(' ')}]"


class Pcan(CanDeviceInterface):
    """PEAK PCAN adapter backend."""

    def __init__(self, channel: Union[int, str] = "PCAN_USBBUS1", bitrate: int = 500000, is_fd: bool = False, fd_bitrate: bytes = b"f_clock=80000000,nom_brp=10,nom_tseg1=5,nom_tseg2=2,nom_sjw=1,data_brp=4,data_tseg1=7,data_tseg2=2,data_sjw=1", rx_buffer_size: int = 1024, poll_batch_size: int = 128, log_frames: bool = True):
        self._channel = self._parse_channel(channel)
        self._bitrate = int(bitrate)
        self._is_fd = bool(is_fd)
        self._fd_bitrate = bytes(fd_bitrate)
        self._buf: Deque[RawCanMsg] = deque(maxlen=max(1, int(rx_buffer_size)))
        self._poll_batch_size = max(1, int(poll_batch_size))
        self._log_frames = bool(log_frames)
        self._dropped_frames = 0
        self._closed = False
        self._dll = PCANBasicDLL()
        self._open()

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    @staticmethod
    def _parse_channel(channel: Union[int, str]) -> int:
        if isinstance(channel, int):
            return int(channel)
        channel_name = str(channel).strip()
        if channel_name in PCAN_CHANNEL_NAMES:
            return int(PCAN_CHANNEL_NAMES[channel_name])
        raise DeviceInitError(f"Unsupported PCAN channel: {channel!r}")

    def _open(self) -> None:
        if self._is_fd:
            ret = self._dll.initialize_fd(self._channel, self._fd_bitrate)
        else:
            bitrate_code = PCAN_BITRATES.get(self._bitrate)
            if bitrate_code is None:
                raise DeviceInitError(f"Unsupported PCAN bitrate: {self._bitrate}")
            ret = self._dll.initialize(self._channel, bitrate_code)
        if ret != PCAN_ERROR_OK:
            raise DeviceInitError(f"Failed to initialize PCAN channel: {self._dll.error_text(ret)}")

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._dll.uninitialize(self._channel)
        finally:
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
        if self._closed:
            raise DeviceOpenError("PCAN device is closed")
        if is_fd and not self._is_fd:
            raise DeviceSendError("PCAN channel is not initialized in CAN-FD mode")
        if (not is_fd) and len(data) > 8:
            raise DeviceSendError(f"Classical CAN payload length must be <= 8, got {len(data)}")
        if is_fd and len(data) > 64:
            raise DeviceSendError(f"CAN-FD payload length must be <= 64, got {len(data)}")

        msg_type = PCAN_MESSAGE_EXTENDED if int(can_id) > 0x7FF else PCAN_MESSAGE_STANDARD
        if is_fd:
            msg_type |= PCAN_MESSAGE_FD
            dlc = CANFD_LEN_TO_DLC.get(len(data))
            if dlc is None:
                raise DeviceSendError(f"Invalid CAN-FD payload length for DLC conversion: {len(data)}")
            msg = TPCANMsgFD()
            msg.ID = int(can_id) & 0x1FFFFFFF
            msg.MSGTYPE = msg_type
            msg.DLC = dlc
            for i, b in enumerate(data):
                msg.DATA[i] = b
            ret = self._dll.write_fd(self._channel, msg)
        else:
            msg = TPCANMsg()
            msg.ID = int(can_id) & 0x1FFFFFFF
            msg.MSGTYPE = msg_type
            msg.LEN = len(data)
            for i, b in enumerate(data):
                msg.DATA[i] = b
            ret = self._dll.write(self._channel, msg)

        if ret != PCAN_ERROR_OK:
            raise DeviceSendError(f"Failed to send CAN frame: {self._dll.error_text(ret)}")
        if self._log_frames:
            logger.info("TX %s", _format_can_frame(can_id, data, is_fd))

    def rxfn(self) -> Optional[RawCanMsg]:
        if self._closed:
            raise DeviceOpenError("PCAN device is closed")
        if self._buf:
            return self._buf.popleft()
        for _ in range(self._poll_batch_size):
            msg = self._read_one()
            if msg is None:
                break
            if len(self._buf) == self._buf.maxlen:
                self._dropped_frames += 1
            self._buf.append(msg)
        if self._buf:
            return self._buf.popleft()
        return None

    def _read_one(self) -> Optional[RawCanMsg]:
        if self._is_fd:
            status, msg = self._dll.read_fd(self._channel)
            if status == PCAN_ERROR_QRCVEMPTY:
                return None
            if status != PCAN_ERROR_OK:
                self._handle_status(status)
                return None
            msg_type = int(msg.MSGTYPE)
            if msg_type & (PCAN_MESSAGE_RTR | PCAN_MESSAGE_ERRFRAME):
                return None
            data_len = CANFD_DLC_TO_LEN[msg.DLC] if 0 <= int(msg.DLC) < len(CANFD_DLC_TO_LEN) else 0
            payload = bytes(msg.DATA[:data_len])
            if not payload:
                return None
            can_id = int(msg.ID) & 0x1FFFFFFF
            if self._log_frames:
                logger.info("RX %s", _format_can_frame(can_id, payload, True))
            return RawCanMsg(id=can_id, data=payload, isfd=True)

        status, msg = self._dll.read(self._channel)
        if status == PCAN_ERROR_QRCVEMPTY:
            return None
        if status != PCAN_ERROR_OK:
            self._handle_status(status)
            return None
        msg_type = int(msg.MSGTYPE)
        if msg_type & (PCAN_MESSAGE_RTR | PCAN_MESSAGE_ERRFRAME):
            return None
        data_len = max(0, min(8, int(msg.LEN)))
        payload = bytes(msg.DATA[:data_len])
        if not payload:
            return None
        can_id = int(msg.ID) & 0x1FFFFFFF
        if self._log_frames:
            logger.info("RX %s", _format_can_frame(can_id, payload, False))
        return RawCanMsg(id=can_id, data=payload, isfd=False)

    def _handle_status(self, status: int) -> None:
        if status & (PCAN_ERROR_BUSLIGHT | PCAN_ERROR_BUSHEAVY | PCAN_ERROR_BUSPASSIVE | PCAN_ERROR_BUSOFF):
            logger.warning("PCAN bus status: %s", self._dll.error_text(status))
            return
        raise DeviceOpenError(f"Failed to read PCAN frame: {self._dll.error_text(status)}")


__all__ = ["Pcan", "PCAN_USBBUS1"]
