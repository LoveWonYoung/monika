import platform
from ctypes import POINTER, Structure, byref, c_char, c_char_p, c_int, c_ubyte, c_uint, c_ulonglong, c_ushort, create_string_buffer
from pathlib import Path

from ...hw.windows_dll import load_windows_dll

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
    _fields_ = [("ID", c_uint), ("MSGTYPE", c_ubyte), ("LEN", c_ubyte), ("DATA", c_ubyte * 8)]


class TPCANTimestamp(Structure):
    _fields_ = [("millis", c_uint), ("millis_overflow", c_ushort), ("micros", c_ushort)]


class TPCANMsgFD(Structure):
    _fields_ = [("ID", c_uint), ("MSGTYPE", c_ubyte), ("DLC", c_ubyte), ("DATA", c_ubyte * 64)]


class PCANBasicDLL:
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
