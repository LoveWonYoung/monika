try:
    from lib import isotp_engine_ctypes as iso
    from lib import lintp_engine_ctypes as lin
except ModuleNotFoundError:
    from python.lib import isotp_engine_ctypes as iso
    from python.lib import lintp_engine_ctypes as lin

print(iso.TP_BACKEND, lin.LIN_TP_BACKEND)
