"""Readable terminal logs without replacing print or configuring library loggers."""
import logging
import os
import re
import sys
from threading import RLock

_lock = RLock()
_handler = None


class EdgeFormatter(logging.Formatter):
    def format(self, record):
        name = record.name.rsplit('.', 1)[-1]
        component = 'Cloud' if record.name.startswith('cloud_edge.') else name.replace('_', ' ').title()
        message = record.getMessage()
        # Keep one physical terminal line per event, even for malformed input.
        message = re.sub(r'[\x00-\x1f\x7f]', lambda m: repr(m.group())[1:-1], message)
        timestamp = self.formatTime(record, '%Y-%m-%d %H:%M:%S')
        line = f'[{timestamp}] [{component}] {message}'
        if getattr(sys.stderr, 'isatty', lambda: False)() and 'NO_COLOR' not in os.environ:
            if record.levelno >= logging.ERROR:
                color = '\033[31m'  # Red
            elif record.levelno >= logging.WARNING:
                color = '\033[33m'  # Yellow
            elif record.levelno >= logging.INFO:
                color = '\033[32m'  # Green
            else:
                color = '\033[36m'  # Cyan
            return color + line + '\033[0m'
        return line


class TerminalHandler(logging.StreamHandler):
    def emit(self, record):
        # Resolve stderr at emit time so redirected CLI/test output still works.
        self.stream = sys.stderr
        super().emit(record)


def get_logger(component):
    global _handler
    with _lock:
        if _handler is None:
            # Windows consoles need virtual-terminal mode for ANSI colors.
            if os.name == 'nt' and getattr(sys.stderr, 'isatty', lambda: False)():
                try:
                    import ctypes
                    import msvcrt
                    handle = ctypes.c_void_p(msvcrt.get_osfhandle(sys.stderr.fileno()))
                    mode = ctypes.c_ulong()
                    kernel = ctypes.windll.kernel32
                    if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                        kernel.SetConsoleMode(handle, mode.value | 0x0004)
                except (AttributeError, OSError, ValueError, ImportError):
                    pass
            _handler = TerminalHandler()
            _handler.setFormatter(EdgeFormatter())
        level = getattr(logging, os.environ.get('EDGE_LOG_LEVEL', 'INFO').upper(), logging.INFO)
        if not isinstance(level, int):
            level = logging.INFO
        for name in ('edge', 'cloud_edge'):
            parent = logging.getLogger(name)
            if _handler not in parent.handlers:
                parent.addHandler(_handler)
            parent.setLevel(level)
            parent.propagate = False
    return logging.getLogger('edge.' + component.lower())
