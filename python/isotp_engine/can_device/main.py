import logging

from ..hw.errors import DeviceError
from ..utils.log_recorder import init_and_rotate
from .backends.toomoss_canfd import Toomoss
from .worker import CanTpWorker


def main() -> None:
    init_and_rotate(log_name="can_tp_", interval_minutes=10)
    logger = logging.getLogger(__name__)

    try:
        with Toomoss() as hw:
            with CanTpWorker(hw=hw, req_id=0x5B1, resp_id=0x5B9, func_id=0x7DF, is_fd=True) as dev:
                req = bytes([0x22, 0xF1, 0x94])
                rsp = dev.uds_request(req)
                logger.info("CAN request: %s", req.hex(" "))
                logger.info("CAN response: %s", rsp.hex(" "))
    except DeviceError as exc:
        logger.exception("device error: %s", exc)


if __name__ == "__main__":
    main()
