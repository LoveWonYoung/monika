"""Backward-compatible launcher for CAN worker demo.

Prefer:
- python -m can_device.main
- python -m lin_device.main
"""

if __name__ == '__main__':
    import runpy

    runpy.run_module('can_device.main', run_name='__main__')
