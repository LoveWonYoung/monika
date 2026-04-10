import ctypes
import logging
from collections import deque
from ctypes import byref
from typing import Deque, Optional, Union

from ....common.types import RawCanMsg
from ....hw.errors import DeviceInitError, DeviceOpenError, DeviceSendError
from ...interface import CanDeviceInterface
from .sdk import (
    CANFD_DLC_TO_LEN,
    CANFD_LEN_TO_DLC,
    XL_BUS_TYPE_CAN,
    XL_CAN_EV_TAG_RX_OK,
    XL_CAN_EV_TAG_TX_MSG,
    XL_CAN_EXT_MSG_ID,
    XL_CAN_MSG_FLAG_ERROR_FRAME,
    XL_CAN_MSG_FLAG_REMOTE_FRAME,
    XL_CAN_RXMSG_FLAG_EDL,
    XL_CAN_RXMSG_FLAG_EF,
    XL_CAN_RXMSG_FLAG_RTR,
    XL_CAN_TXMSG_FLAG_EDL,
    XL_ERR_QUEUE_IS_EMPTY,
    XL_INTERFACE_VERSION,
    XL_INTERFACE_VERSION_V4,
    XL_INVALID_PORTHANDLE,
    XL_RECEIVE_MSG,
    XL_TRANSMIT_MSG,
    XLaccess,
    XLcanFdConf,
    XLcanRxEvent,
    XLcanTxEvent,
    XLevent,
    XLportHandle,
    VectorXLDll,
)

logger = logging.getLogger(__name__)


def _format_can_frame(can_id: int, data: bytes, is_fd: bool) -> str:
    frame_type = "CAN-FD" if is_fd else "CAN"
    dlc = CANFD_LEN_TO_DLC[len(data)] if is_fd else len(data)
    return f"ID=0x{can_id:X} Type={frame_type} DLC={dlc} Data=[{data.hex(' ')}]"


class Vector(CanDeviceInterface):
    """Vector XL API backend."""

    def __init__(self, channel: int = 0, channel_index: Optional[int] = None, app_name: Optional[str] = "CANalyzer", bitrate: int = 500000, is_fd: bool = False, data_bitrate: Optional[int] = None, rx_queue_size: int = 2**14, rx_buffer_size: int = 1024, poll_batch_size: int = 128, log_frames: bool = True, sjw_abr: int = 2, tseg1_abr: int = 6, tseg2_abr: int = 3, sjw_dbr: int = 2, tseg1_dbr: int = 6, tseg2_dbr: int = 3):
        self._channel = int(channel)
        self._channel_index = channel_index
        self._app_name = app_name
        self._bitrate = int(bitrate)
        self._is_fd = bool(is_fd)
        self._data_bitrate = int(data_bitrate or bitrate)
        self._rx_queue_size = max(16, int(rx_queue_size))
        self._buf: Deque[RawCanMsg] = deque(maxlen=max(1, int(rx_buffer_size)))
        self._poll_batch_size = max(1, int(poll_batch_size))
        self._log_frames = bool(log_frames)
        self._dropped_frames = 0
        self._closed = False
        self._sjw_abr = int(sjw_abr)
        self._tseg1_abr = int(tseg1_abr)
        self._tseg2_abr = int(tseg2_abr)
        self._sjw_dbr = int(sjw_dbr)
        self._tseg1_dbr = int(tseg1_dbr)
        self._tseg2_dbr = int(tseg2_dbr)
        self._dll = VectorXLDll()
        self._port = XLportHandle(XL_INVALID_PORTHANDLE)
        self._mask = XLaccess(0)
        self._open()

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    def _open(self) -> None:
        self._dll.check(int(self._dll._dll.xlOpenDriver()), "xlOpenDriver")
        try:
            channel_index = self._resolve_channel_index()
            mask_value = 1 << channel_index
            self._mask = XLaccess(mask_value)
            requested_permission = XLaccess(mask_value)
            app_name = self._app_name.encode("ascii", errors="ignore") if self._app_name else b""
            interface_version = XL_INTERFACE_VERSION_V4 if self._is_fd else XL_INTERFACE_VERSION
            status = int(self._dll._dll.xlOpenPort(byref(self._port), app_name, self._mask, byref(requested_permission), self._rx_queue_size, interface_version, XL_BUS_TYPE_CAN))
            self._dll.check(status, "xlOpenPort")
            config_mask_value = int(requested_permission.value) & int(self._mask.value)
            if config_mask_value == 0:
                config_mask_value = int(self._mask.value)
            config_mask = XLaccess(config_mask_value)
            if self._is_fd:
                conf = XLcanFdConf()
                conf.arbitrationBitRate = self._bitrate
                conf.sjwAbr = self._sjw_abr
                conf.tseg1Abr = self._tseg1_abr
                conf.tseg2Abr = self._tseg2_abr
                conf.dataBitRate = self._data_bitrate
                conf.sjwDbr = self._sjw_dbr
                conf.tseg1Dbr = self._tseg1_dbr
                conf.tseg2Dbr = self._tseg2_dbr
                self._dll.check(int(self._dll._dll.xlCanFdSetConfiguration(self._port, config_mask, byref(conf))), "xlCanFdSetConfiguration")
            else:
                self._dll.check(int(self._dll._dll.xlCanSetChannelBitrate(self._port, config_mask, self._bitrate)), "xlCanSetChannelBitrate")
            self._dll.check(int(self._dll._dll.xlCanSetChannelMode(self._port, self._mask, 0, 0)), "xlCanSetChannelMode")
            self._dll.check(int(self._dll._dll.xlActivateChannel(self._port, self._mask, XL_BUS_TYPE_CAN, 0)), "xlActivateChannel")
        except Exception as exc:
            self.close()
            if isinstance(exc, DeviceInitError):
                raise
            if isinstance(exc, DeviceOpenError):
                raise DeviceInitError(str(exc)) from exc
            raise

    def _resolve_channel_index(self) -> int:
        if self._channel_index is not None:
            return int(self._channel_index)
        if self._app_name:
            hw_type = ctypes.c_uint(0)
            hw_index = ctypes.c_uint(0)
            hw_channel = ctypes.c_uint(0)
            status = int(self._dll._dll.xlGetApplConfig(self._app_name.encode("ascii", errors="ignore"), ctypes.c_uint(self._channel), byref(hw_type), byref(hw_index), byref(hw_channel), XL_BUS_TYPE_CAN))
            self._dll.check(status, "xlGetApplConfig")
            idx = int(self._dll._dll.xlGetChannelIndex(int(hw_type.value), int(hw_index.value), int(hw_channel.value)))
            if idx < 0:
                raise DeviceInitError(f"Invalid Vector channel index returned for {self._app_name}:{self._channel}")
            return idx
        return int(self._channel)

    def close(self) -> None:
        if self._closed:
            return
        try:
            if int(self._port.value) != XL_INVALID_PORTHANDLE:
                try:
                    self._dll._dll.xlDeactivateChannel(self._port, self._mask)
                except Exception:
                    pass
                try:
                    self._dll._dll.xlClosePort(self._port)
                except Exception:
                    pass
                self._port = XLportHandle(XL_INVALID_PORTHANDLE)
        finally:
            try:
                self._dll._dll.xlCloseDriver()
            except Exception:
                pass
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
            raise DeviceOpenError("Vector device is closed")
        if is_fd and not self._is_fd:
            raise DeviceSendError("Vector channel is not initialized in CAN-FD mode")
        if (not is_fd) and len(data) > 8:
            raise DeviceSendError(f"Classical CAN payload length must be <= 8, got {len(data)}")
        if is_fd and len(data) > 64:
            raise DeviceSendError(f"CAN-FD payload length must be <= 64, got {len(data)}")
        msg_id = int(can_id) & 0x1FFFFFFF
        if msg_id > 0x7FF:
            msg_id |= XL_CAN_EXT_MSG_ID
        if is_fd:
            dlc = CANFD_LEN_TO_DLC.get(len(data))
            if dlc is None:
                raise DeviceSendError(f"Invalid CAN-FD payload length for DLC conversion: {len(data)}")
            tx_event = XLcanTxEvent()
            tx_event.tag = XL_CAN_EV_TAG_TX_MSG
            tx_event.transId = 0xFFFF
            tx_event.tagData.canMsg.canId = msg_id
            tx_event.tagData.canMsg.msgFlags = XL_CAN_TXMSG_FLAG_EDL
            tx_event.tagData.canMsg.dlc = dlc
            for i, b in enumerate(data):
                tx_event.tagData.canMsg.data[i] = b
            sent_count = ctypes.c_uint(0)
            status = int(self._dll._dll.xlCanTransmitEx(self._port, self._mask, 1, byref(sent_count), byref(tx_event)))
            self._dll.check(status, "xlCanTransmitEx")
            if int(sent_count.value) <= 0:
                raise DeviceSendError("Vector XL transmit did not send any CAN-FD frame")
        else:
            event = XLevent()
            event.tag = XL_TRANSMIT_MSG
            event.tagData.msg.id = msg_id
            event.tagData.msg.dlc = len(data)
            for i, b in enumerate(data):
                event.tagData.msg.data[i] = b
            count = ctypes.c_uint(1)
            status = int(self._dll._dll.xlCanTransmit(self._port, self._mask, byref(count), byref(event)))
            self._dll.check(status, "xlCanTransmit")
        if self._log_frames:
            logger.info("TX %s", _format_can_frame(can_id, data, is_fd))

    def rxfn(self) -> Optional[RawCanMsg]:
        if self._closed:
            raise DeviceOpenError("Vector device is closed")
        if self._buf:
            return self._buf.popleft()
        for _ in range(self._poll_batch_size):
            item = self._read_one()
            if item is None:
                break
            if item is False:
                continue
            if len(self._buf) == self._buf.maxlen:
                self._dropped_frames += 1
            self._buf.append(item)
        if self._buf:
            return self._buf.popleft()
        return None

    def _read_one(self) -> Optional[Union[RawCanMsg, bool]]:
        if self._is_fd:
            event = XLcanRxEvent()
            event.size = ctypes.sizeof(XLcanRxEvent)
            status = int(self._dll._dll.xlCanReceive(self._port, byref(event)))
            if status == XL_ERR_QUEUE_IS_EMPTY:
                return None
            self._dll.check(status, "xlCanReceive")
            if int(event.tag) != XL_CAN_EV_TAG_RX_OK:
                return False
            msg = event.tagData.canRxOkMsg
            flags = int(msg.msgFlags)
            if flags & (XL_CAN_RXMSG_FLAG_RTR | XL_CAN_RXMSG_FLAG_EF):
                return False
            dlc = int(msg.dlc)
            payload_len = CANFD_DLC_TO_LEN[dlc] if 0 <= dlc < len(CANFD_DLC_TO_LEN) else 0
            payload = bytes(msg.data[:payload_len])
            if not payload:
                return False
            can_id = int(msg.canId) & 0x1FFFFFFF
            if self._log_frames:
                logger.info("RX %s", _format_can_frame(can_id, payload, True))
            return RawCanMsg(id=can_id, data=payload, isfd=bool(flags & XL_CAN_RXMSG_FLAG_EDL))
        count = ctypes.c_uint(1)
        event = XLevent()
        status = int(self._dll._dll.xlReceive(self._port, byref(count), byref(event)))
        if status == XL_ERR_QUEUE_IS_EMPTY:
            return None
        self._dll.check(status, "xlReceive")
        if int(event.tag) != XL_RECEIVE_MSG:
            return False
        flags = int(event.tagData.msg.flags)
        if flags & (XL_CAN_MSG_FLAG_REMOTE_FRAME | XL_CAN_MSG_FLAG_ERROR_FRAME):
            return False
        payload_len = max(0, min(8, int(event.tagData.msg.dlc)))
        payload = bytes(event.tagData.msg.data[:payload_len])
        if not payload:
            return False
        can_id = int(event.tagData.msg.id) & 0x1FFFFFFF
        if self._log_frames:
            logger.info("RX %s", _format_can_frame(can_id, payload, False))
        return RawCanMsg(id=can_id, data=payload, isfd=False)


__all__ = ["Vector"]
