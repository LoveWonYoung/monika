"""
USB2XXX device API bindings.
"""

import platform
from ctypes import *
from pathlib import Path

from .windows_dll import load_windows_dll


_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _THIS_DIR.parent.parent
_CWD_DIR = Path.cwd()


class DEVICE_INFO(Structure):
    _fields_ = [
        ("FirmwareName", c_char * 32),
        ("BuildDate", c_char * 32),
        ("HardwareVersion", c_uint),
        ("FirmwareVersion", c_uint),
        ("SerialNumber", c_uint * 3),
        ("Functions", c_uint),
    ]


class HARDWARE_INFO(Structure):
    _fields_ = [
        ("McuModel", c_char * 16),
        ("ProductModel", c_char * 16),
        ("Version", c_uint),
        ("CANChannelNum", c_char),
        ("PWMChannelNum", c_char),
        ("HaveCANFD", c_char),
        ("DIChannelNum", c_char),
        ("DOChannelNum", c_char),
        ("HaveIsolation", c_char),
        ("ExPowerSupply", c_char),
        ("IsOEM", c_char),
        ("EECapacity", c_char),
        ("SPIFlashCapacity", c_char),
        ("TFCardSupport", c_char),
        ("ProductionDate", c_char * 12),
        ("USBControl", c_char),
        ("SerialControl", c_char),
        ("EthControl", c_char),
        ("VbatChannel", c_char),
    ]


POWER_LEVEL_1V8 = 1
POWER_LEVEL_2V5 = 2
POWER_LEVEL_3V3 = 3


def _load_windows_dll_from_bin_only(dll_name: str, search_dirs):
    for search_dir in search_dirs:
        candidate = search_dir / dll_name
        if candidate.is_file():
            return windll.LoadLibrary(str(candidate))
    raise RuntimeError(
        f"Failed to load {dll_name} from bin directories: {[str(search_dir) for search_dir in search_dirs]}"
    )


if platform.system() == "Windows":
    search_dirs = [_CWD_DIR / "bin", _PROJECT_DIR / "bin"]
    registry_subkeys = [
        r"SOFTWARE\TOOMOSS\USB2XXX",
        r"SOFTWARE\WOW6432Node\TOOMOSS\USB2XXX",
        r"SOFTWARE\USB2XXX",
        r"SOFTWARE\WOW6432Node\USB2XXX",
    ]
    registry_value_names = ["InstallDir", "Path", ""]

    if "64bit" in platform.architecture()[0]:
        # Runtime is 64-bit: load directly from local bin directories only.
        USB2XXXDep = _load_windows_dll_from_bin_only("libusb-1.0.dll", search_dirs)
        USB2XXXLib = _load_windows_dll_from_bin_only("USB2XXX.dll", search_dirs)
    else:
        dep_search_dirs = search_dirs + [_PROJECT_DIR / "libs" / "windows" / "x86_32"]
        lib_search_dirs = search_dirs + [_PROJECT_DIR / "libs" / "windows" / "x86_32"]

        USB2XXXDep = load_windows_dll(
            dll_names=["libusb-1.0.dll"],
            registry_subkeys=registry_subkeys,
            registry_value_names=registry_value_names,
            search_dirs=dep_search_dirs,
        )
        USB2XXXLib = load_windows_dll(
            dll_names=["USB2XXX.dll"],
            registry_subkeys=registry_subkeys,
            registry_value_names=registry_value_names,
            search_dirs=lib_search_dirs,
        )
elif platform.system() == "Darwin":
    dep_path = _PROJECT_DIR / "libs" / "mac_os" / "libusb-1.0.0.dylib"
    lib_path = _PROJECT_DIR / "libs" / "mac_os" / "libUSB2XXX.dylib"
    cdll.LoadLibrary(str(dep_path))
    USB2XXXLib = cdll.LoadLibrary(str(lib_path))
else:
    raise RuntimeError(f"Unsupported platform for USB2XXX: {platform.system()}")


def USB_ScanDevice(pDevHandle):
    return USB2XXXLib.USB_ScanDevice(pDevHandle)


def USB_OpenDevice(DevHandle):
    return USB2XXXLib.USB_OpenDevice(DevHandle)


def USB_ResetDevice(DevHandle):
    return USB2XXXLib.USB_ResetDevice(DevHandle)


def USB_RetryConnect(DevHandle):
    return USB2XXXLib.USB_RetryConnect(DevHandle)


def USB_WaitResume(DevHandle, TimeOutMs):
    return USB2XXXLib.USB_WaitResume(DevHandle, TimeOutMs)


def DEV_GetDeviceInfo(DevHandle, pDevInfo, pFunctionStr):
    return USB2XXXLib.DEV_GetDeviceInfo(DevHandle, pDevInfo, pFunctionStr)


def DEV_GetHardwareInfo(DevHandle, pHardwareInfo):
    return USB2XXXLib.DEV_GetHardwareInfo(DevHandle, pHardwareInfo)


def USB_CloseDevice(DevHandle):
    return USB2XXXLib.USB_CloseDevice(DevHandle)


def DEV_EraseUserData(DevHandle):
    return USB2XXXLib.DEV_EraseUserData(DevHandle)


def DEV_WriteUserData(DevHandle, OffsetAddr, pWriteData, DataLen):
    return USB2XXXLib.DEV_WriteUserData(DevHandle, OffsetAddr, pWriteData, DataLen)


def DEV_ReadUserData(DevHandle, OffsetAddr, pReadData, DataLen):
    return USB2XXXLib.DEV_ReadUserData(DevHandle, OffsetAddr, pReadData, DataLen)


def DEV_SetPowerLevel(DevHandle, PowerLevel):
    return USB2XXXLib.DEV_SetPowerLevel(DevHandle, PowerLevel)


def DEV_GetTimestamp(DevHandle, BusType, pTimestamp):
    return USB2XXXLib.DEV_GetTimestamp(DevHandle, BusType, pTimestamp)


def DEV_ResetTimestamp(DevHandle):
    return USB2XXXLib.DEV_ResetTimestamp(DevHandle)


def DEV_GetDllBuildTime(pDateTime):
    return USB2XXXLib.DEV_GetDllBuildTime(pDateTime)
