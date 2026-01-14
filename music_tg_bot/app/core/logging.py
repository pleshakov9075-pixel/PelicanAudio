from __future__ import annotations

import logging
import sys
from typing import Optional


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format=(
            "%(asctime)s | %(levelname)s | %(name)s | "
            "request_id=%(request_id)s | %(message)s"
        ),
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    root_logger = logging.getLogger()
    request_id_filter = RequestIdFilter()
    for handler in root_logger.handlers:
        handler.addFilter(request_id_filter)


class RequestIdFilter(logging.Filter):
    def __init__(self, request_id: Optional[str] = None) -> None:
        super().__init__()
        self.request_id = request_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = self.request_id or "-"
        return True


class LoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return msg, {"extra": {"request_id": self.extra.get("request_id", "-")}}
