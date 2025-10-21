import json
import logging
import logging.handlers
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Optional, Union

from .slack import send_slack_message
import os


_configured: bool = False


def _coerce_level(level: Union[str, int]) -> int:
    if isinstance(level, int):
        return level
    try:
        return getattr(logging, str(level).upper())
    except AttributeError:
        return logging.INFO


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        base = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            base["exception"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)


class SlackLogHandler(logging.Handler):
    def __init__(
        self,
        webhook_url: Optional[str],
        *,
        min_level: Union[str, int] = "ERROR",
        extra_flag_key: str = "notify_slack",
        rate_limit_per_minute: int = 30,
    ) -> None:
        super().__init__(_coerce_level(min_level))
        self.webhook_url = webhook_url
        self.extra_flag_key = extra_flag_key
        self.rate_limit_per_minute = max(1, rate_limit_per_minute)
        self._recent_sends: Deque[float] = deque()

    def _allow_send(self) -> bool:
        now = time.monotonic()
        one_min_ago = now - 60.0
        while self._recent_sends and self._recent_sends[0] < one_min_ago:
            self._recent_sends.popleft()
        if len(self._recent_sends) >= self.rate_limit_per_minute:
            return False
        self._recent_sends.append(now)
        return True

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        try:
            if not self.webhook_url:
                return
            flagged = bool(getattr(record, self.extra_flag_key, False))
            if not flagged and record.levelno < self.level:
                return
            if not self._allow_send():
                return
            msg = self.format(record) if self.formatter else record.getMessage()
            send_slack_message(msg, webhook_url=self.webhook_url)
        except Exception:
            # 절대 예외 전파 금지 (로깅에서 예외가 서비스 흐름을 막지 않도록)
            self.handleError(record)


def setup_logging(
    level: Union[str, int] = "INFO",
    *,
    json_logs: bool = False,
    log_file: Optional[str] = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
    slack_webhook_url: Optional[str] = None,
    slack_min_level: Optional[Union[str, int]] = "ERROR",
    slack_extra_flag_key: str = "notify_slack",
    slack_rate_limit_per_minute: int = 30,
) -> None:
    global _configured

    logger = logging.getLogger("fj_client")
    logger.setLevel(_coerce_level(level))
    logger.propagate = False

    # clear existing handlers to avoid duplicates
    for h in list(logger.handlers):
        logger.removeHandler(h)

    formatter: logging.Formatter
    if json_logs:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 로그 디렉터리 자동 생성
    log_file = log_file or f"./logs/${datetime.now().strftime('%Y-%m-%d %H:%M:%Sq')}.log"
    try:
        directory = os.path.dirname(log_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
    except Exception:
        # 디렉터리 생성 실패 시에도 핸들러 생성 시도(권한 문제 등은 핸들러에서 예외 발생)
        pass
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if slack_webhook_url or slack_min_level:
        slack_handler = SlackLogHandler(
            webhook_url=slack_webhook_url,
            min_level=slack_min_level or "ERROR",
            extra_flag_key=slack_extra_flag_key,
            rate_limit_per_minute=slack_rate_limit_per_minute,
        )
        slack_handler.setFormatter(formatter)
        logger.addHandler(slack_handler)

    _configured = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger("fj_client" if not name else f"fj_client.{name}")


def set_level(level: Union[str, int]) -> None:
    logging.getLogger("fj_client").setLevel(_coerce_level(level))


__all__ = [
    "JsonFormatter",
    "SlackLogHandler",
    "setup_logging",
    "get_logger",
    "set_level",
]


