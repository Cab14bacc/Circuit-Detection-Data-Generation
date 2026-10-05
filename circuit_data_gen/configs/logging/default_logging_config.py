import logging


class MultilineIndentFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Indent every line after the first one by 2 spaces
        record.msg = str(record.msg).replace("\n", "\n  ")
        return super().format(record)


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "formatter": {
            "()": MultilineIndentFormatter,
            "format": "%(asctime)s [%(levelname)s] %(name)s:\n  %(message)s\n",
        },
        "json": {
            "()": ("pythonjsonlogger.json.JsonFormatter"),
            "format": ("%(asctime)s %(name)s %(levelname)s %(message)s"),
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "formatter",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "debug/app.log",
            "maxBytes": 10 * 1024 * 1024,  # 10 MB per file
            "backupCount": 5,  # keep app.log.1 ... app.log.5
            "formatter": "json",
            "encoding": "utf-8",
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["stdout", "file"],
    },
}
