import logging
from pathlib import Path

from patcher.logging_setup import LOGGER_NAME, get_logger, setup_logging


def teardown_logger():
    """tests keep re-configuring the same named logger, so clear its handlers between tests"""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_setup_logging_writes_to_the_real_file(tmp_path: Path):
    teardown_logger()
    log_path = tmp_path / "patcher.log"

    logger = setup_logging(log_path)
    logger.info("test message")

    assert log_path.exists()
    assert "test message" in log_path.read_text(encoding="utf-8")
    teardown_logger()


def test_setup_logging_creates_parent_directories(tmp_path: Path):
    teardown_logger()
    log_path = tmp_path / "nested" / "patcher.log"

    setup_logging(log_path)

    assert log_path.parent.exists()
    teardown_logger()


def test_setup_logging_does_not_duplicate_handlers_on_repeat_calls(tmp_path: Path):
    teardown_logger()
    log_path = tmp_path / "patcher.log"

    logger1 = setup_logging(log_path)
    handler_count_after_first = len(logger1.handlers)
    logger2 = setup_logging(log_path)

    assert logger1 is logger2
    assert len(logger2.handlers) == handler_count_after_first
    teardown_logger()


def test_get_logger_before_setup_does_not_raise():
    teardown_logger()
    logger = get_logger()
    logger.info("nothing configured yet, should just be a silent no-op")
    teardown_logger()
