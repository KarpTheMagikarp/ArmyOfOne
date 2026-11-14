"""
sets up file logging so a crash or unexpected error leaves something
behind to actually look at, instead of just whatever scrolled past in
a terminal window that's already closed by the time someone reports a
problem. rotates at 1mb with 2 backups kept, so this never grows
unbounded across however many times someone runs the tool.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "ffxiii_trilogy_patcher"


def setup_logging(log_path: Path, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)

    # guard against double setup adding duplicate handlers if this ever
    # gets called more than once in the same process
    if logger.handlers:
        return logger

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # backslashreplace because windows filenames can contain unpaired
        # surrogates, which utf-8 cannot encode. without it the exception
        # fires inside emit(), and a logging call that can throw is a logging
        # call every caller has to wrap.
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2,
                                      encoding="utf-8", errors="backslashreplace")
    except OSError:
        # a locked or redirected LOCALAPPDATA is not worth refusing to start
        # over. the app keeps working, it just has nothing to write down.
        logger.addHandler(logging.NullHandler())
        return logger

    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    """
    gets the logger without setting it up, for modules that want to
    log but shouldn't be the ones deciding where the log file lives.
    if setup_logging() was never called, this just has no handlers and
    silently does nothing, which is fine for tests and for any code
    path that runs before logging is configured.
    """
    return logging.getLogger(LOGGER_NAME)
