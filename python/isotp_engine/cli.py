from __future__ import annotations

import argparse
import logging
from typing import Callable

from .can_device.backends.toomoss import Toomoss
from .can_device.worker import CanTpWorker
from .hw.errors import DeviceError
from .lin_device.backends.toomoss import ToomossLin
from .lin_device.worker import LinTpWorker
from .utils.log_recorder import init_and_rotate


def _run_demo(log_name: str, runner: Callable[[], tuple[bytes, bytes]], label: str) -> None:
    init_and_rotate(log_name=log_name, interval_minutes=10)
    logger = logging.getLogger(__name__)

    try:
        req, rsp = runner()
        logger.info("%s request: %s", label, req.hex(" "))
        logger.info("%s response: %s", label, rsp.hex(" "))
    except DeviceError as exc:
        logger.exception("device error: %s", exc)


def run_can_demo() -> None:
    def _runner() -> tuple[bytes, bytes]:
        with Toomoss() as hw:
            with CanTpWorker(hw=hw, req_id=0x5B1, resp_id=0x5B9, func_id=0x7DF, is_fd=True) as dev:
                req = bytes([0x22, 0xF1, 0x94])
                rsp = dev.uds_request(req)
                return req, rsp

    _run_demo(log_name="can_tp_", runner=_runner, label="CAN")


def run_lin_demo() -> None:
    def _runner() -> tuple[bytes, bytes]:
        with ToomossLin(channel=0, baudrate=19200, master=True) as hw:
            with LinTpWorker(
                hw=hw,
                req_frame_id=0x3C,
                resp_frame_id=0x3D,
                req_nad=0x10,
                func_nad=0x7F,
                resp_poll_interval_ms=15,
            ) as dev:
                req = bytes([0x22, 0xF1, 0x90])
                rsp = dev.uds_request(req)
                return req, rsp

    _run_demo(log_name="lin_tp_", runner=_runner, label="LIN")


def main() -> None:
    parser = argparse.ArgumentParser(description="ISO-TP / LIN-TP demos")
    parser.add_argument("mode", choices=["can", "lin"], help="demo to run")
    args = parser.parse_args()
    if args.mode == "can":
        run_can_demo()
    else:
        run_lin_demo()


__all__ = ["main", "run_can_demo", "run_lin_demo"]
