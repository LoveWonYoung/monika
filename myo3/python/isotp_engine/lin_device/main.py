import logging

from ..hw.errors import DeviceError
from ..utils.log_recorder import init_and_rotate

from . import ToomossLin
from .worker import LinTpWorker


def main() -> None:
    init_and_rotate(log_name="lin_tp_", interval_minutes=10)
    logger = logging.getLogger(__name__)

    if ToomossLin is None:
        raise RuntimeError("Toomoss LIN backend is unavailable on this platform/runtime")

    try:
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
                logger.info("LIN request: %s", req.hex(" "))
                logger.info("LIN response: %s", rsp.hex(" "))
    except DeviceError as exc:
        logger.exception("device error: %s", exc)


if __name__ == "__main__":
    main()
