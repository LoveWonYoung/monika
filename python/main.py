from devices.exceptions import DeviceError
from devices.hw_device import MyHwDevice
from devices.tp_clients import MyHwDeviceWithTpWorker


if __name__ == '__main__':
    try:
        with MyHwDevice() as hw:
            for _ in range(30):
                hw.txfn(0x482, bytes([0] * 8), True)

            with MyHwDeviceWithTpWorker(hw=hw, req_id=0x5B1, resp_id=0x5B9, func_id=0x7DF, is_fd=True) as dev:
                dev.start_keep_alive()
                req = bytes([0x22, 0xF1, 0x94])
                rsp = dev.uds_request(req)
                print("worker request:", req.hex(" "))
                print("worker response:", rsp.hex(" "))
                dev.stop_keep_alive()
    except DeviceError as exc:
        print(f"device error: {exc}")