class DeviceError(RuntimeError):
    """Base class for hardware device errors."""


class DeviceNotFoundError(DeviceError):
    pass


class DeviceOpenError(DeviceError):
    pass


class DeviceInitError(DeviceError):
    pass


class DeviceSendError(DeviceError):
    pass
