import ctypes
import logging
import platform
from collections import deque
from pathlib import Path
from typing import Deque, Optional

from ...common.types import RawCanMsg
from ...hw.errors import DeviceInitError, DeviceNotFoundError, DeviceOpenError, DeviceSendError
from ...hw.windows_dll import load_windows_dll
from ..interface import CanDeviceInterface


logger = logging.getLogger(__name__)

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


def _payload_len_to_dlc(payload_len: int, is_fd: bool) -> int:
    if is_fd:
        dlc = CANFD_LEN_TO_DLC.get(payload_len)
        if dlc is None:
            raise ValueError(f"Invalid CAN-FD payload length for DLC conversion: {payload_len}")
        return int(dlc)
    if payload_len < 0 or payload_len > 8:
        raise ValueError(f"Classical CAN payload length must be in [0, 8], got {payload_len}")
    return int(payload_len)


def _dlc_to_payload_len(dlc: int) -> int:
    if 0 <= dlc < len(CANFD_DLC_TO_LEN):
        return CANFD_DLC_TO_LEN[dlc]
    return 0


def _format_can_frame(can_id: int, data: bytes, is_fd: bool) -> str:
    frame_type = "CAN-FD" if is_fd else "CAN"
    dlc = CANFD_LEN_TO_DLC[len(data)] if is_fd else len(data)
    return f"ID=0x{can_id:X} Type={frame_type} DLC={dlc} Data=[{data.hex(' ')}]"


class _TSMasterDll:
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
            registry_value_names=[
                "libTSMaster_x64",
                "libTSMaster_x86",
                "InstallDir",
                "Path",
                "",
            ],
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

        self._dll.tsapp_set_mapping_verbose.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_wchar_p,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
        ]
        self._dll.tsapp_set_mapping_verbose.restype = ctypes.c_int32

        self._dll.tsapp_configure_baudrate_canfd.argtypes = [
            ctypes.c_int32,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
        ]
        self._dll.tsapp_configure_baudrate_canfd.restype = ctypes.c_int32

        self._dll.tsapp_connect.argtypes = []
        self._dll.tsapp_connect.restype = ctypes.c_int32

        self._dll.tsapp_disconnect.argtypes = []
        self._dll.tsapp_disconnect.restype = ctypes.c_int32

        self._dll.tsfifo_enable_receive_fifo.argtypes = []
        self._dll.tsfifo_enable_receive_fifo.restype = ctypes.c_int32

        self._dll.tsfifo_receive_canfd_msgs.argtypes = [
            ctypes.POINTER(TLIBCANFD),
            ctypes.POINTER(ctypes.c_int32),
            ctypes.c_int32,
            ctypes.c_int32,
        ]
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

    def set_mapping_verbose(
        self,
        app_name: str,
        app_channel_type: int,
        app_channel_index: int,
        hw_name: str,
        hw_type: int,
        hw_index: int,
        hw_channel: int,
        reserved: int,
        enable_mapping: bool,
    ) -> int:
        return int(
            self._dll.tsapp_set_mapping_verbose(
                app_name,
                int(app_channel_type),
                int(app_channel_index),
                hw_name,
                int(hw_type),
                int(hw_index),
                int(hw_channel),
                int(reserved),
                int(bool(enable_mapping)),
            )
        )

    def configure_baudrate_canfd(
        self,
        channel: int,
        nominal_kbps: float,
        data_kbps: float,
        controller_mode: int = 1,
        reserved: int = 0,
        enable_termination: bool = True,
    ) -> int:
        return int(
            self._dll.tsapp_configure_baudrate_canfd(
                int(channel),
                float(nominal_kbps),
                float(data_kbps),
                int(controller_mode),
                int(reserved),
                int(bool(enable_termination)),
            )
        )

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


class TSMaster(CanDeviceInterface):
    """TSMaster CAN/CAN-FD backend."""

    def __init__(
        self,
        channel: int = 0,
        is_fd: bool = True,
        app_name: str = "TSMaster_Python_Demo",
        hw_name: str = "TC1016",
        can_channel_count: int = 4,
        can_bitrate_kbps: float = 500.0,
        data_bitrate_kbps: float = 2000.0,
        mapping_app_channel_type: int = 0,
        mapping_app_channel_index: int = 0,
        mapping_hw_type: int = 3,
        mapping_hw_index: int = 11,
        mapping_hw_channel: int = 0,
        mapping_reserved: int = 0,
        enable_mapping: bool = True,
        show_window: bool = False,
        show_window_tab: str = "Hardware",
        enable_receive_fifo: bool = True,
        include_tx_echo: bool = False,
        rx_buffer_size: int = 1024,
        poll_batch_size: int = 128,
        log_frames: bool = True,
    ):
        self._channel = int(channel)
        self._is_fd = bool(is_fd)
        self._app_name = str(app_name)
        self._hw_name = str(hw_name)
        self._can_channel_count = int(can_channel_count)
        self._can_bitrate_kbps = float(can_bitrate_kbps)
        self._data_bitrate_kbps = float(data_bitrate_kbps)

        self._mapping_app_channel_type = int(mapping_app_channel_type)
        self._mapping_app_channel_index = int(mapping_app_channel_index)
        self._mapping_hw_type = int(mapping_hw_type)
        self._mapping_hw_index = int(mapping_hw_index)
        self._mapping_hw_channel = int(mapping_hw_channel)
        self._mapping_reserved = int(mapping_reserved)
        self._enable_mapping = bool(enable_mapping)

        self._show_window = bool(show_window)
        self._show_window_tab = str(show_window_tab)
        self._enable_receive_fifo = bool(enable_receive_fifo)
        self._include_tx_echo = bool(include_tx_echo)
        self._log_frames = bool(log_frames)

        self._poll_batch_size = max(1, int(poll_batch_size))
        self._buf: Deque[RawCanMsg] = deque(maxlen=max(1, int(rx_buffer_size)))
        self._dropped_frames = 0
        self._connected = False
        self._closed = False

        self._dll = _TSMasterDll()
        self._open()

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    def _open(self) -> None:
        try:
            self._check(self._dll.initialize(self._app_name), "initialize_lib_tsmaster", DeviceInitError)

            found = ctypes.c_int32(0)
            self._check(self._dll.enumerate_devices(found), "tsapp_enumerate_hw_devices", DeviceInitError)
            if int(found.value) <= 0:
                raise DeviceNotFoundError("No TSMaster hardware devices found")

            if self._show_window:
                rc = self._dll.show_window(self._show_window_tab, True)
                if rc != TSMASTER_OK:
                    logger.warning("tsapp_show_tsmaster_window failed: rc=%s", rc)

            if self._can_channel_count > 0:
                self._check(
                    self._dll.set_can_channel_count(self._can_channel_count),
                    "tsapp_set_can_channel_count",
                    DeviceInitError,
                )

            if self._enable_mapping:
                self._check(
                    self._dll.set_mapping_verbose(
                        app_name=self._app_name,
                        app_channel_type=self._mapping_app_channel_type,
                        app_channel_index=self._mapping_app_channel_index,
                        hw_name=self._hw_name,
                        hw_type=self._mapping_hw_type,
                        hw_index=self._mapping_hw_index,
                        hw_channel=self._mapping_hw_channel,
                        reserved=self._mapping_reserved,
                        enable_mapping=True,
                    ),
                    "tsapp_set_mapping_verbose",
                    DeviceInitError,
                )

            self._check(
                self._dll.configure_baudrate_canfd(
                    channel=self._channel,
                    nominal_kbps=self._can_bitrate_kbps,
                    data_kbps=self._data_bitrate_kbps,
                    controller_mode=1,
                    reserved=0,
                    enable_termination=True,
                ),
                "tsapp_configure_baudrate_canfd",
                DeviceInitError,
            )

            self._check(self._dll.connect(), "tsapp_connect", DeviceOpenError)
            self._connected = True

            if self._enable_receive_fifo:
                self._check(self._dll.enable_receive_fifo(), "tsfifo_enable_receive_fifo", DeviceOpenError)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(rc: int, operation: str, err_type):
        if int(rc) == TSMASTER_OK and operation != "tsfifo_enable_receive_fifo":
            return
        if int(rc) == 1 and operation == "tsfifo_enable_receive_fifo":
            return
        raise err_type(f"{operation} failed with rc={int(rc)}")

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._connected:
                rc = self._dll.disconnect()
                if rc != TSMASTER_OK:
                    logger.warning("tsapp_disconnect failed: rc=%s", rc)
        finally:
            self._connected = False
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
        if self._closed or not self._connected:
            raise DeviceOpenError("TSMaster device is closed")
        if len(data) == 0:
            raise DeviceSendError("data length is 0")
        if is_fd and not self._is_fd:
            raise DeviceSendError("TSMaster channel is not initialized in CAN-FD mode")
        if (not is_fd) and len(data) > 8:
            raise DeviceSendError(f"Classical CAN payload length must be <= 8, got {len(data)}")
        if is_fd and len(data) > 64:
            raise DeviceSendError(f"CAN-FD payload length must be <= 64, got {len(data)}")

        try:
            dlc = _payload_len_to_dlc(len(data), is_fd)
        except ValueError as exc:
            raise DeviceSendError(str(exc)) from exc

        msg = TLIBCANFD()
        msg.FIdxChn = self._channel
        msg.FIdentifier = int(can_id) & 0x1FFFFFFF
        msg.FProperties = 1  # TX
        msg.FDLC = dlc
        msg.FFDProperties = 1 if is_fd else 0
        for i, b in enumerate(data):
            msg.FData[i] = b

        rc = self._dll.transmit_canfd_async(ctypes.byref(msg))
        if rc != TSMASTER_OK:
            raise DeviceSendError(f"tsapp_transmit_canfd_async failed with rc={rc}")
        if self._log_frames:
            logger.info("TX %s", _format_can_frame(can_id, data, is_fd))

    # Compatibility helpers for codebases that use the Go-style naming.
    def write(self, can_id: int, is_fd: bool, data: bytes) -> None:
        self.txfn(can_id=can_id, data=data, is_fd=is_fd)

    def read(self) -> Optional[RawCanMsg]:
        return self.rxfn()

    def is_fd_mode(self) -> bool:
        return self._is_fd

    def rxfn(self) -> Optional[RawCanMsg]:
        if self._closed or not self._connected:
            raise DeviceOpenError("TSMaster device is closed")

        if self._buf:
            return self._buf.popleft()

        self._poll_once()
        if self._buf:
            return self._buf.popleft()
        return None

    def _poll_once(self) -> None:
        frames = (TLIBCANFD * self._poll_batch_size)()
        size = ctypes.c_int32(self._poll_batch_size)
        rc = self._dll.receive_canfd_msgs(
            ctypes.byref(frames[0]),
            ctypes.byref(size),
            channel=self._channel,
            include_tx=True,
        )
        if rc != TSMASTER_OK:
            logger.debug("tsfifo_receive_canfd_msgs failed: rc=%s", rc)
            return

        received = max(0, min(self._poll_batch_size, int(size.value)))
        for idx in range(received):
            frame = frames[idx]
            is_tx_echo = bool(int(frame.FProperties) & 0x01)
            if is_tx_echo and not self._include_tx_echo:
                continue

            payload_len = _dlc_to_payload_len(int(frame.FDLC))
            if payload_len <= 0:
                self._dropped_frames += 1
                continue

            payload = bytes(frame.FData[:payload_len])
            if not payload:
                self._dropped_frames += 1
                continue

            can_id = int(frame.FIdentifier) & 0x1FFFFFFF
            is_fd = bool(int(frame.FFDProperties) & 0x01)
            if self._log_frames:
                logger.info("%s %s", "TXE" if is_tx_echo else "RX", _format_can_frame(can_id, payload, is_fd))

            if len(self._buf) == self._buf.maxlen:
                self._dropped_frames += 1
            self._buf.append(RawCanMsg(id=can_id, data=payload, isfd=is_fd))


__all__ = ["TSMaster"]
