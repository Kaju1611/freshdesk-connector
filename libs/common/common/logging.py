"""JSON logging with secret redaction."""
from __future__ import annotations
import json, logging, re, sys, time

_PATTERNS = [
    (re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(basic|bearer)\s+[A-Za-z0-9+/=._-]+"), r"\1\2 [REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(token['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class _JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "ctx", None)
        if extra:
            payload["ctx"] = json.loads(redact(json.dumps(extra, default=str)))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def configure_logging(service: str, level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
