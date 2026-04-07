import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOG_LOCK = threading.Lock()
_CURRENT_HANDLER: Optional[logging.Handler] = None
_ROTATE_THREAD: Optional[threading.Thread] = None
_ROTATE_STOP_EVENT = threading.Event()


def now_string() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


def make_dir(base_dir: str = ".") -> Path:
    dir_name = datetime.now().strftime("%Y_%m_%d")
    full_path = Path(base_dir) / dir_name
    full_path.mkdir(parents=True, exist_ok=True)
    return full_path


def _set_log_output(file_path: Path) -> None:
    global _CURRENT_HANDLER
    handler = logging.FileHandler(file_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(fmt="%(asctime)s.%(msecs)03d %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    with _LOG_LOCK:
        old_handler = _CURRENT_HANDLER
        _CURRENT_HANDLER = handler
        root.addHandler(handler)
        if old_handler is not None:
            root.removeHandler(old_handler)
            old_handler.close()


def recorder_as_name_init(name: str, base_dir: str = ".") -> Path:
    log_dir = make_dir(base_dir)
    log_path = log_dir / f"{name}.log"
    _set_log_output(log_path)
    return log_path


def init(log_name: str, base_dir: str = ".") -> Path:
    log_path = recorder_as_name_init(f"{log_name}{now_string()}", base_dir=base_dir)
    logging.getLogger(__name__).info("log initialized: %s", log_path)
    return log_path


def _rotate_loop(log_name: str, base_dir: str, interval_seconds: float) -> None:
    while not _ROTATE_STOP_EVENT.wait(interval_seconds):
        try:
            log_path = recorder_as_name_init(f"{log_name}{now_string()}", base_dir=base_dir)
            logging.getLogger(__name__).info("log rotated: %s", log_path)
        except Exception:
            logging.getLogger(__name__).exception("log rotation failed")


def init_and_rotate(log_name: str, base_dir: str = ".", interval_minutes: int = 10) -> Path:
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be > 0")
    log_path = init(log_name=log_name, base_dir=base_dir)
    global _ROTATE_THREAD
    with _LOG_LOCK:
        if _ROTATE_THREAD is not None and _ROTATE_THREAD.is_alive():
            return log_path
        _ROTATE_STOP_EVENT.clear()
        _ROTATE_THREAD = threading.Thread(target=_rotate_loop, args=(log_name, base_dir, interval_minutes * 60), name="log-rotate", daemon=True)
        _ROTATE_THREAD.start()
    return log_path
