import ctypes
import logging
import platform
from collections import deque
from ctypes import POINTER, byref
from pathlib import Path
from typing import Deque, Optional, Union

from ...common.types import RawCanMsg
from ...hw.errors import DeviceInitError, DeviceOpenError, DeviceSendError
from ...hw.windows_dll import load_windows_dll
from ..interface import CanDeviceInterface


logger = logging.getLogger(__name__)

CANFD_DLC_TO_LEN = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
CANFD_LEN_TO_DLC = {length: dlc for dlc, length in enumerate(CANFD_DLC_TO_LEN)}

XLuint64 = ctypes.c_int64
XLaccess = XLuint64
XLstatus = ctypes.c_short
XLportHandle = ctypes.c_long

XL_SUCCESS = 0
XL_ERR_QUEUE_IS_EMPTY = 10
XL_INVALID_PORTHANDLE = -1

XL_BUS_TYPE_CAN = 1
XL_INTERFACE_VERSION = 3
XL_INTERFACE_VERSION_V4 = 4

XL_RECEIVE_MSG = 1
XL_TRANSMIT_MSG = 10

XL_CAN_EV_TAG_RX_OK = 1024
XL_CAN_EV_TAG_TX_OK = 1028
XL_CAN_EV_TAG_TX_MSG = 1088

XL_CAN_EXT_MSG_ID = 0x80000000

XL_CAN_MSG_FLAG_ERROR_FRAME = 0x01
XL_CAN_MSG_FLAG_REMOTE_FRAME = 0x10

XL_CAN_RXMSG_FLAG_EDL = 0x01
XL_CAN_RXMSG_FLAG_BRS = 0x02
XL_CAN_RXMSG_FLAG_RTR = 0x10
XL_CAN_RXMSG_FLAG_EF = 0x200

XL_CAN_TXMSG_FLAG_EDL = 0x01
XL_CAN_TXMSG_FLAG_BRS = 0x02
XL_CAN_TXMSG_FLAG_RTR = 0x10


class XLcanMsg(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_ulong),
        ("flags", ctypes.c_ushort),
        ("dlc", ctypes.c_ushort),
        ("res1", XLuint64),
        ("data", ctypes.c_ubyte * 8),
        ("res2", XLuint64),
    ]


class XLtagData(ctypes.Union):
    _fields_ = [("msg", XLcanMsg)]


class XLevent(ctypes.Structure):
    _fields_ = [
        ("tag", ctypes.c_ubyte),
        ("chanIndex", ctypes.c_ubyte),
        ("transId", ctypes.c_ushort),
        ("portHandle", ctypes.c_ushort),
        ("flags", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte),
        ("timeStamp", XLuint64),
        ("tagData", XLtagData),
    ]


class XLcanRxMsg(ctypes.Structure):
    _fields_ = [
        ("canId", ctypes.c_uint),
        ("msgFlags", ctypes.c_uint),
        ("crc", ctypes.c_uint),
        ("reserved1", ctypes.c_ubyte * 12),
        ("totalBitCnt", ctypes.c_ushort),
        ("dlc", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte * 5),
        ("data", ctypes.c_ubyte * 64),
    ]


class XLrxTagData(ctypes.Union):
    _fields_ = [("canRxOkMsg", XLcanRxMsg), ("canTxOkMsg", XLcanRxMsg)]


class XLcanRxEvent(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_int),
        ("tag", ctypes.c_ushort),
        ("chanIndex", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte),
        ("userHandle", ctypes.c_int),
        ("flagsChip", ctypes.c_ushort),
        ("reserved0", ctypes.c_ushort),
        ("reserved1", XLuint64),
        ("timeStamp", XLuint64),
        ("tagData", XLrxTagData),
    ]


class XLcanTxMsg(ctypes.Structure):
    _fields_ = [
        ("canId", ctypes.c_uint),
        ("msgFlags", ctypes.c_uint),
        ("dlc", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte * 7),
        ("data", ctypes.c_ubyte * 64),
    ]


class XLtxTagData(ctypes.Union):
    _fields_ = [("canMsg", XLcanTxMsg)]


class XLcanTxEvent(ctypes.Structure):
    _fields_ = [
        ("tag", ctypes.c_ushort),
        ("transId", ctypes.c_ushort),
        ("chanIndex", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte * 3),
        ("tagData", XLtxTagData),
    ]


class XLcanFdConf(ctypes.Structure):
    _fields_ = [
        ("arbitrationBitRate", ctypes.c_uint),
        ("sjwAbr", ctypes.c_uint),
        ("tseg1Abr", ctypes.c_uint),
        ("tseg2Abr", ctypes.c_uint),
        ("dataBitRate", ctypes.c_uint),
        ("sjwDbr", ctypes.c_uint),
        ("tseg1Dbr", ctypes.c_uint),
        ("tseg2Dbr", ctypes.c_uint),
        ("reserved", ctypes.c_ubyte),
        ("options", ctypes.c_ubyte),
        ("reserved1", ctypes.c_ubyte * 2),
        ("reserved2", ctypes.c_ubyte),
    ]


def _format_can_frame(can_id: int, data: bytes, is_fd: bool) -> str:
    frame_type = "CAN-FD" if is_fd else "CAN"
    dlc = CANFD_LEN_TO_DLC[len(data)] if is_fd else len(data)
    return f"ID=0x{can_id:X} Type={frame_type} DLC={dlc} Data=[{data.hex(' ')}]"


class _VectorXLDll:
    def __init__(self):
        if platform.system() != "Windows":
            raise RuntimeError("Vector backend is only supported on Windows")

        this_dir = Path(__file__).resolve().parent
        project_dir = this_dir.parent.parent.parent
        dll_names = ["vxlapi64.dll", "vxlapi.dll"] if platform.architecture()[0] == "64bit" else ["vxlapi.dll"]
        self._dll = load_windows_dll(
            dll_names=dll_names,
            registry_subkeys=[
                r"SOFTWARE\Vector\XL Driver Library",
                r"SOFTWARE\WOW6432Node\Vector\XL Driver Library",
            ],
            registry_value_names=["InstallDir", "Path", "ApiPath", "BinPath", ""],
            search_dirs=[Path.cwd() / "bin", project_dir / "bin"],
        )
        self._bind()

    def _bind(self) -> None:
        self._dll.xlGetErrorString.argtypes = [XLstatus]
        self._dll.xlGetErrorString.restype = ctypes.c_char_p

        self._dll.xlOpenDriver.argtypes = []
        self._dll.xlOpenDriver.restype = XLstatus

        self._dll.xlCloseDriver.argtypes = []
        self._dll.xlCloseDriver.restype = XLstatus

        self._dll.xlOpenPort.argtypes = [
            POINTER(XLportHandle),
            ctypes.c_char_p,
            XLaccess,
            POINTER(XLaccess),
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        self._dll.xlOpenPort.restype = XLstatus

        self._dll.xlClosePort.argtypes = [XLportHandle]
        self._dll.xlClosePort.restype = XLstatus

        self._dll.xlActivateChannel.argtypes = [XLportHandle, XLaccess, ctypes.c_uint, ctypes.c_uint]
        self._dll.xlActivateChannel.restype = XLstatus

        self._dll.xlDeactivateChannel.argtypes = [XLportHandle, XLaccess]
        self._dll.xlDeactivateChannel.restype = XLstatus

        self._dll.xlCanSetChannelMode.argtypes = [XLportHandle, XLaccess, ctypes.c_int, ctypes.c_int]
        self._dll.xlCanSetChannelMode.restype = XLstatus

        self._dll.xlCanSetChannelBitrate.argtypes = [XLportHandle, XLaccess, ctypes.c_ulong]
        self._dll.xlCanSetChannelBitrate.restype = XLstatus

        self._dll.xlCanFdSetConfiguration.argtypes = [XLportHandle, XLaccess, POINTER(XLcanFdConf)]
        self._dll.xlCanFdSetConfiguration.restype = XLstatus

        self._dll.xlReceive.argtypes = [XLportHandle, POINTER(ctypes.c_uint), POINTER(XLevent)]
        self._dll.xlReceive.restype = XLstatus

        self._dll.xlCanReceive.argtypes = [XLportHandle, POINTER(XLcanRxEvent)]
        self._dll.xlCanReceive.restype = XLstatus

        self._dll.xlCanTransmit.argtypes = [XLportHandle, XLaccess, POINTER(ctypes.c_uint), POINTER(XLevent)]
        self._dll.xlCanTransmit.restype = XLstatus

        self._dll.xlCanTransmitEx.argtypes = [
            XLportHandle,
            XLaccess,
            ctypes.c_uint,
            POINTER(ctypes.c_uint),
            POINTER(XLcanTxEvent),
        ]
        self._dll.xlCanTransmitEx.restype = XLstatus

        self._dll.xlGetApplConfig.argtypes = [
            ctypes.c_char_p,
            ctypes.c_uint,
            POINTER(ctypes.c_uint),
            POINTER(ctypes.c_uint),
            POINTER(ctypes.c_uint),
            ctypes.c_uint,
        ]
        self._dll.xlGetApplConfig.restype = XLstatus

        self._dll.xlGetChannelIndex.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self._dll.xlGetChannelIndex.restype = ctypes.c_int

    def status_text(self, status: int) -> str:
        msg = self._dll.xlGetErrorString(XLstatus(status))
        if not msg:
            return f"XL status {status}"
        return msg.decode("utf-8", errors="replace")

    def check(self, status: int, operation: str) -> None:
        if int(status) == XL_SUCCESS:
            return
        raise DeviceOpenError(f"{operation} failed: {self.status_text(int(status))}")


class Vector(CanDeviceInterface):
    """Vector XL API backend."""

    def __init__(
        self,
        channel: int = 0,
        channel_index: Optional[int] = None,
        app_name: Optional[str] = "CANalyzer",
        bitrate: int = 500000,
        is_fd: bool = False,
        data_bitrate: Optional[int] = None,
        rx_queue_size: int = 2**14,
        rx_buffer_size: int = 1024,
        poll_batch_size: int = 128,
        log_frames: bool = True,
        sjw_abr: int = 2,
        tseg1_abr: int = 6,
        tseg2_abr: int = 3,
        sjw_dbr: int = 2,
        tseg1_dbr: int = 6,
        tseg2_dbr: int = 3,
    ):
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

        self._dll = _VectorXLDll()
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
            status = int(
                self._dll._dll.xlOpenPort(
                    byref(self._port),
                    app_name,
                    self._mask,
                    byref(requested_permission),
                    self._rx_queue_size,
                    interface_version,
                    XL_BUS_TYPE_CAN,
                )
            )
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
                self._dll.check(
                    int(self._dll._dll.xlCanFdSetConfiguration(self._port, config_mask, byref(conf))),
                    "xlCanFdSetConfiguration",
                )
            else:
                self._dll.check(
                    int(self._dll._dll.xlCanSetChannelBitrate(self._port, config_mask, self._bitrate)),
                    "xlCanSetChannelBitrate",
                )

            self._dll.check(int(self._dll._dll.xlCanSetChannelMode(self._port, self._mask, 0, 0)), "xlCanSetChannelMode")
            self._dll.check(
                int(self._dll._dll.xlActivateChannel(self._port, self._mask, XL_BUS_TYPE_CAN, 0)),
                "xlActivateChannel",
            )
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
            status = int(
                self._dll._dll.xlGetApplConfig(
                    self._app_name.encode("ascii", errors="ignore"),
                    ctypes.c_uint(self._channel),
                    byref(hw_type),
                    byref(hw_index),
                    byref(hw_channel),
                    XL_BUS_TYPE_CAN,
                )
            )
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
