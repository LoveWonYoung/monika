import ctypes
import platform
from ctypes import POINTER
from pathlib import Path

from ....hw.errors import DeviceOpenError
from ....hw.windows_dll import load_windows_dll

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
    _fields_ = [("id", ctypes.c_ulong), ("flags", ctypes.c_ushort), ("dlc", ctypes.c_ushort), ("res1", XLuint64), ("data", ctypes.c_ubyte * 8), ("res2", XLuint64)]


class XLtagData(ctypes.Union):
    _fields_ = [("msg", XLcanMsg)]


class XLevent(ctypes.Structure):
    _fields_ = [("tag", ctypes.c_ubyte), ("chanIndex", ctypes.c_ubyte), ("transId", ctypes.c_ushort), ("portHandle", ctypes.c_ushort), ("flags", ctypes.c_ubyte), ("reserved", ctypes.c_ubyte), ("timeStamp", XLuint64), ("tagData", XLtagData)]


class XLcanRxMsg(ctypes.Structure):
    _fields_ = [("canId", ctypes.c_uint), ("msgFlags", ctypes.c_uint), ("crc", ctypes.c_uint), ("reserved1", ctypes.c_ubyte * 12), ("totalBitCnt", ctypes.c_ushort), ("dlc", ctypes.c_ubyte), ("reserved", ctypes.c_ubyte * 5), ("data", ctypes.c_ubyte * 64)]


class XLrxTagData(ctypes.Union):
    _fields_ = [("canRxOkMsg", XLcanRxMsg), ("canTxOkMsg", XLcanRxMsg)]


class XLcanRxEvent(ctypes.Structure):
    _fields_ = [("size", ctypes.c_int), ("tag", ctypes.c_ushort), ("chanIndex", ctypes.c_ubyte), ("reserved", ctypes.c_ubyte), ("userHandle", ctypes.c_int), ("flagsChip", ctypes.c_ushort), ("reserved0", ctypes.c_ushort), ("reserved1", XLuint64), ("timeStamp", XLuint64), ("tagData", XLrxTagData)]


class XLcanTxMsg(ctypes.Structure):
    _fields_ = [("canId", ctypes.c_uint), ("msgFlags", ctypes.c_uint), ("dlc", ctypes.c_ubyte), ("reserved", ctypes.c_ubyte * 7), ("data", ctypes.c_ubyte * 64)]


class XLtxTagData(ctypes.Union):
    _fields_ = [("canMsg", XLcanTxMsg)]


class XLcanTxEvent(ctypes.Structure):
    _fields_ = [("tag", ctypes.c_ushort), ("transId", ctypes.c_ushort), ("chanIndex", ctypes.c_ubyte), ("reserved", ctypes.c_ubyte * 3), ("tagData", XLtxTagData)]


class XLcanFdConf(ctypes.Structure):
    _fields_ = [("arbitrationBitRate", ctypes.c_uint), ("sjwAbr", ctypes.c_uint), ("tseg1Abr", ctypes.c_uint), ("tseg2Abr", ctypes.c_uint), ("dataBitRate", ctypes.c_uint), ("sjwDbr", ctypes.c_uint), ("tseg1Dbr", ctypes.c_uint), ("tseg2Dbr", ctypes.c_uint), ("reserved", ctypes.c_ubyte), ("options", ctypes.c_ubyte), ("reserved1", ctypes.c_ubyte * 2), ("reserved2", ctypes.c_ubyte)]


class VectorXLDll:
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
        self._dll.xlOpenPort.argtypes = [POINTER(XLportHandle), ctypes.c_char_p, XLaccess, POINTER(XLaccess), ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
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
        self._dll.xlCanTransmitEx.argtypes = [XLportHandle, XLaccess, ctypes.c_uint, POINTER(ctypes.c_uint), POINTER(XLcanTxEvent)]
        self._dll.xlCanTransmitEx.restype = XLstatus
        self._dll.xlGetApplConfig.argtypes = [ctypes.c_char_p, ctypes.c_uint, POINTER(ctypes.c_uint), POINTER(ctypes.c_uint), POINTER(ctypes.c_uint), ctypes.c_uint]
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
