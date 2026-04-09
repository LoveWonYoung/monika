import logging
import platform
from collections import deque
from ctypes import POINTER, byref, c_char, c_char_p, c_int, c_ubyte, c_uint, c_ulonglong, c_ushort, create_string_buffer
from ctypes import Structure
from pathlib import Path
from typing import Deque, Optional, Union

from ...common.types import RawCanMsg
from ...hw.errors import DeviceInitError, DeviceOpenError, DeviceSendError
from ...hw.windows_dll import load_windows_dll
from ..interface import CanDeviceInterface


logger = logging.getLogger(__name__)

CANFD_DLC_TO_LEN = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
CANFD_LEN_TO_DLC = {length: dlc for dlc, length in enumerate(CANFD_DLC_TO_LEN)}

TPCANHandle = c_ushort
TPCANBaudrate = c_ushort
TPCANStatus = c_int

PCAN_ERROR_OK = 0x00000
PCAN_ERROR_QRCVEMPTY = 0x00020
PCAN_ERROR_BUSLIGHT = 0x00004
PCAN_ERROR_BUSHEAVY = 0x00008
PCAN_ERROR_BUSPASSIVE = 0x40000
PCAN_ERROR_BUSOFF = 0x00010

PCAN_MESSAGE_STANDARD = 0x00
PCAN_MESSAGE_RTR = 0x01
PCAN_MESSAGE_EXTENDED = 0x02
PCAN_MESSAGE_FD = 0x04
PCAN_MESSAGE_BRS = 0x08
PCAN_MESSAGE_ERRFRAME = 0x40

PCAN_USBBUS1 = 0x51
PCAN_CHANNEL_NAMES = {
    "PCAN_USBBUS1": 0x51,
    "PCAN_USBBUS2": 0x52,
    "PCAN_USBBUS3": 0x53,
    "PCAN_USBBUS4": 0x54,
    "PCAN_USBBUS5": 0x55,
    "PCAN_USBBUS6": 0x56,
    "PCAN_USBBUS7": 0x57,
    "PCAN_USBBUS8": 0x58,
    "PCAN_USBBUS9": 0x509,
    "PCAN_USBBUS10": 0x50A,
    "PCAN_USBBUS11": 0x50B,
    "PCAN_USBBUS12": 0x50C,
    "PCAN_USBBUS13": 0x50D,
    "PCAN_USBBUS14": 0x50E,
    "PCAN_USBBUS15": 0x50F,
    "PCAN_USBBUS16": 0x510,
}
PCAN_BITRATES = {
    1000000: 0x0014,
    800000: 0x0016,
    500000: 0x001C,
    250000: 0x011C,
    125000: 0x031C,
    100000: 0x432F,
    95000: 0xC34E,
    83000: 0x852B,
    50000: 0x472F,
    47000: 0x1414,
    33000: 0x8B2F,
    20000: 0x532F,
    10000: 0x672F,
    5000: 0x7F7F,
}


class TPCANMsg(Structure):
    _fields_ = [
        ("ID", c_uint),
        ("MSGTYPE", c_ubyte),
        ("LEN", c_ubyte),
        ("DATA", c_ubyte * 8),
    ]


class TPCANTimestamp(Structure):
    _fields_ = [
        ("millis", c_uint),
        ("millis_overflow", c_ushort),
        ("micros", c_ushort),
    ]


class TPCANMsgFD(Structure):
    _fields_ = [
        ("ID", c_uint),
        ("MSGTYPE", c_ubyte),
        ("DLC", c_ubyte),
        ("DATA", c_ubyte * 64),
    ]


def _format_can_frame(can_id: int, data: bytes, is_fd: bool) -> str:
    frame_type = "CAN-FD" if is_fd else "CAN"
    dlc = CANFD_LEN_TO_DLC[len(data)] if is_fd else len(data)
    return f"ID=0x{can_id:X} Type={frame_type} DLC={dlc} Data=[{data.hex(' ')}]"


class _PCANBasicDLL:
    def __init__(self):
        if platform.system() != "Windows":
            raise RuntimeError("PCAN backend is only supported on Windows")

        this_dir = Path(__file__).resolve().parent
        project_dir = this_dir.parent.parent.parent
        dll = load_windows_dll(
            dll_names=["PCANBasic.dll", "PCANBasic64.dll", "PCANBasic"],
            registry_subkeys=[
                r"SOFTWARE\PEAK-System\PCAN-Basic",
                r"SOFTWARE\WOW6432Node\PEAK-System\PCAN-Basic",
            ],
            registry_value_names=["InstallDir", "Path", "Location", ""],
            search_dirs=[Path.cwd() / "bin", project_dir / "bin"],
        )

        self._dll = dll
        self._bind()

    def _bind(self) -> None:
        self._dll.CAN_Initialize.argtypes = [TPCANHandle, TPCANBaudrate, c_ubyte, c_uint, c_ushort]
        self._dll.CAN_Initialize.restype = TPCANStatus

        self._dll.CAN_InitializeFD.argtypes = [TPCANHandle, c_char_p]
        self._dll.CAN_InitializeFD.restype = TPCANStatus

        self._dll.CAN_Uninitialize.argtypes = [TPCANHandle]
        self._dll.CAN_Uninitialize.restype = TPCANStatus

        self._dll.CAN_Read.argtypes = [TPCANHandle, POINTER(TPCANMsg), POINTER(TPCANTimestamp)]
        self._dll.CAN_Read.restype = TPCANStatus

        self._dll.CAN_ReadFD.argtypes = [TPCANHandle, POINTER(TPCANMsgFD), POINTER(c_ulonglong)]
        self._dll.CAN_ReadFD.restype = TPCANStatus

        self._dll.CAN_Write.argtypes = [TPCANHandle, POINTER(TPCANMsg)]
        self._dll.CAN_Write.restype = TPCANStatus

        self._dll.CAN_WriteFD.argtypes = [TPCANHandle, POINTER(TPCANMsgFD)]
        self._dll.CAN_WriteFD.restype = TPCANStatus

        self._dll.CAN_GetErrorText.argtypes = [TPCANStatus, c_ushort, POINTER(c_char)]
        self._dll.CAN_GetErrorText.restype = TPCANStatus

    def initialize(self, channel: int, bitrate_code: int) -> int:
        return int(self._dll.CAN_Initialize(TPCANHandle(channel), TPCANBaudrate(bitrate_code), 0, 0, 0))

    def initialize_fd(self, channel: int, bitrate_fd: bytes) -> int:
        return int(self._dll.CAN_InitializeFD(TPCANHandle(channel), c_char_p(bitrate_fd)))

    def uninitialize(self, channel: int) -> int:
        return int(self._dll.CAN_Uninitialize(TPCANHandle(channel)))

    def read(self, channel: int):
        msg = TPCANMsg()
        ts = TPCANTimestamp()
        status = int(self._dll.CAN_Read(TPCANHandle(channel), byref(msg), byref(ts)))
        return status, msg

    def read_fd(self, channel: int):
        msg = TPCANMsgFD()
        ts = c_ulonglong(0)
        status = int(self._dll.CAN_ReadFD(TPCANHandle(channel), byref(msg), byref(ts)))
        return status, msg

    def write(self, channel: int, msg: TPCANMsg) -> int:
        return int(self._dll.CAN_Write(TPCANHandle(channel), byref(msg)))

    def write_fd(self, channel: int, msg: TPCANMsgFD) -> int:
        return int(self._dll.CAN_WriteFD(TPCANHandle(channel), byref(msg)))

    def error_text(self, status: int) -> str:
        buf = create_string_buffer(256)
        ret = int(self._dll.CAN_GetErrorText(TPCANStatus(status), 0x09, buf))
        if ret == PCAN_ERROR_OK:
            return buf.value.decode("utf-8", errors="replace")
        return f"PCAN status 0x{status:X}"


class Pcan(CanDeviceInterface):
    """PEAK PCAN adapter backend."""

    def __init__(
        self,
        channel: Union[int, str] = "PCAN_USBBUS1",
        bitrate: int = 500000,
        is_fd: bool = False,
        fd_bitrate: bytes = b"f_clock=80000000,nom_brp=10,nom_tseg1=5,nom_tseg2=2,nom_sjw=1,data_brp=4,data_tseg1=7,data_tseg2=2,data_sjw=1",
        rx_buffer_size: int = 1024,
        poll_batch_size: int = 128,
        log_frames: bool = True,
    ):
        self._channel = self._parse_channel(channel)
        self._bitrate = int(bitrate)
        self._is_fd = bool(is_fd)
        self._fd_bitrate = bytes(fd_bitrate)
        self._buf: Deque[RawCanMsg] = deque(maxlen=max(1, int(rx_buffer_size)))
        self._poll_batch_size = max(1, int(poll_batch_size))
        self._log_frames = bool(log_frames)
        self._dropped_frames = 0
        self._closed = False

        self._dll = _PCANBasicDLL()
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
