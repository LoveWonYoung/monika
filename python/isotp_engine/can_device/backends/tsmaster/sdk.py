import ctypes
import platform
from pathlib import Path

from ...hw.windows_dll import load_windows_dll

CANFD_DLC_TO_LEN = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
CANFD_LEN_TO_DLC = {length: dlc for dlc, length in enumerate(CANFD_DLC_TO_LEN)}

TSMASTER_OK = 0


class TLIBCANFD(ctypes.Structure):
    _fields_ = [
        ("FIdxChn", ctypes.c_uint8),
        ("FProperties", ctypes.c_uint8),
        ("FDLC", ctypes.c_uint8),
        ("FFDProperties", ctypes.c_uint8),
        ("FIdentifier", ctypes.c_int32),
        ("FTimeUs", ctypes.c_int64),
        ("FData", ctypes.c_uint8 * 64),
    ]


def payload_len_to_dlc(payload_len: int, is_fd: bool) -> int:
    if is_fd:
        dlc = CANFD_LEN_TO_DLC.get(payload_len)
        if dlc is None:
            raise ValueError(f"Invalid CAN-FD payload length for DLC conversion: {payload_len}")
        return int(dlc)
    if payload_len < 0 or payload_len > 8:
        raise ValueError(f"Classical CAN payload length must be in [0, 8], got {payload_len}")
    return int(payload_len)


def dlc_to_payload_len(dlc: int) -> int:
    if 0 <= dlc < len(CANFD_DLC_TO_LEN):
        return CANFD_DLC_TO_LEN[dlc]
    return 0


class TSMasterDll:
    def __init__(self):
        if platform.system() != "Windows":
            raise RuntimeError("TSMaster backend is only supported on Windows")

        this_dir = Path(__file__).resolve().parent
        project_dir = this_dir.parent.parent.parent
        search_dirs = [Path.cwd() / "bin", project_dir / "bin"]
        base_install = Path(r"C:\Program Files (x86)\TOSUN\TSMaster")
        search_dirs.extend([base_install / "bin", base_install / "bin64"])

        self._dll = load_windows_dll(
            dll_names=["TSMaster.dll"],
            registry_subkeys=[
                r"SOFTWARE\TOSUN\TSMaster",
                r"SOFTWARE\WOW6432Node\TOSUN\TSMaster",
            ],
            registry_value_names=["libTSMaster_x64", "libTSMaster_x86", "InstallDir", "Path", ""],
            search_dirs=search_dirs,
        )
        self._bind()

    def _bind(self) -> None:
        self._dll.initialize_lib_tsmaster.argtypes = [ctypes.c_wchar_p]
        self._dll.initialize_lib_tsmaster.restype = ctypes.c_int32
        self._dll.tsapp_enumerate_hw_devices.argtypes = [ctypes.POINTER(ctypes.c_int32)]
        self._dll.tsapp_enumerate_hw_devices.restype = ctypes.c_int32
        self._dll.tsapp_show_tsmaster_window.argtypes = [ctypes.c_char_p, ctypes.c_int32]
        self._dll.tsapp_show_tsmaster_window.restype = ctypes.c_int32
        self._dll.tsapp_set_can_channel_count.argtypes = [ctypes.c_int32]
        self._dll.tsapp_set_can_channel_count.restype = ctypes.c_int32
        self._dll.tsapp_set_mapping_verbose.argtypes = [ctypes.c_wchar_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_wchar_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
        self._dll.tsapp_set_mapping_verbose.restype = ctypes.c_int32
        self._dll.tsapp_configure_baudrate_canfd.argtypes = [ctypes.c_int32, ctypes.c_float, ctypes.c_float, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
        self._dll.tsapp_configure_baudrate_canfd.restype = ctypes.c_int32
        self._dll.tsapp_connect.argtypes = []
        self._dll.tsapp_connect.restype = ctypes.c_int32
        self._dll.tsapp_disconnect.argtypes = []
        self._dll.tsapp_disconnect.restype = ctypes.c_int32
        self._dll.tsfifo_enable_receive_fifo.argtypes = []
        self._dll.tsfifo_enable_receive_fifo.restype = ctypes.c_int32
        self._dll.tsfifo_receive_canfd_msgs.argtypes = [ctypes.POINTER(TLIBCANFD), ctypes.POINTER(ctypes.c_int32), ctypes.c_int32, ctypes.c_int32]
        self._dll.tsfifo_receive_canfd_msgs.restype = ctypes.c_int32
        self._dll.tsapp_transmit_canfd_async.argtypes = [ctypes.POINTER(TLIBCANFD)]
        self._dll.tsapp_transmit_canfd_async.restype = ctypes.c_int32

    def initialize(self, app_name: str) -> int:
        return int(self._dll.initialize_lib_tsmaster(app_name))

    def enumerate_devices(self, out_count: ctypes.c_int32) -> int:
        return int(self._dll.tsapp_enumerate_hw_devices(ctypes.byref(out_count)))

    def show_window(self, tab_name: str, show: bool) -> int:
        return int(self._dll.tsapp_show_tsmaster_window(tab_name.encode("ascii", errors="ignore"), int(bool(show))))

    def set_can_channel_count(self, count: int) -> int:
        return int(self._dll.tsapp_set_can_channel_count(int(count)))

    def set_mapping_verbose(self, app_name: str, app_channel_type: int, app_channel_index: int, hw_name: str, hw_type: int, hw_index: int, hw_channel: int, reserved: int, enable_mapping: bool) -> int:
        return int(self._dll.tsapp_set_mapping_verbose(app_name, int(app_channel_type), int(app_channel_index), hw_name, int(hw_type), int(hw_index), int(hw_channel), int(reserved), int(bool(enable_mapping))))

    def configure_baudrate_canfd(self, channel: int, nominal_kbps: float, data_kbps: float, controller_mode: int = 1, reserved: int = 0, enable_termination: bool = True) -> int:
        return int(self._dll.tsapp_configure_baudrate_canfd(int(channel), float(nominal_kbps), float(data_kbps), int(controller_mode), int(reserved), int(bool(enable_termination))))

    def connect(self) -> int:
        return int(self._dll.tsapp_connect())

    def disconnect(self) -> int:
        return int(self._dll.tsapp_disconnect())

    def enable_receive_fifo(self) -> int:
        return int(self._dll.tsfifo_enable_receive_fifo())

    def receive_canfd_msgs(self, frames_ptr, out_count_ptr, channel: int, include_tx: bool) -> int:
        return int(self._dll.tsfifo_receive_canfd_msgs(frames_ptr, out_count_ptr, int(channel), int(bool(include_tx))))

    def transmit_canfd_async(self, msg_ptr) -> int:
        return int(self._dll.tsapp_transmit_canfd_async(msg_ptr))
