import logging

from log_recorder import init_and_rotate
from devices.exceptions import DeviceError
from devices.toomoss.toomoss_canfd import Toomoss
from devices.tp_clients import TpWorker

if __name__ == '__main__':
    init_and_rotate(log_name="tp_engine_", interval_minutes=10)
    logger = logging.getLogger(__name__)
    try:
        with Toomoss() as hw:
            for _ in range(30):
                hw.txfn(0x482, bytes([0] * 8), True)

            with TpWorker(hw=hw, req_id=0x5B1, resp_id=0x5B9, func_id=0x7DF, is_fd=True) as dev:
                dev.start_keep_alive()
                req = bytes([0x22, 0xf1, 0x94])
                rsp = dev.uds_request(req)
                logger.info("worker request: %s", req.hex(" "))
                logger.info("worker response: %s", rsp.hex(" "))
                dev.uds_request(bytes([0x22, 0xc0, 0x40]))
                dev.stop_keep_alive()
    except DeviceError as exc:
        logger.exception("device error: %s", exc)
