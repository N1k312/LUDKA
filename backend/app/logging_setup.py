"""Логи без секретов: токены ботов и номера телефонов вырезаются из итоговой строки.

Редактирование применяется к уже отформатированному сообщению, поэтому
закрывает и тексты исключений с трейсбеками, и логи сторонних библиотек
(например, URL запроса к api.telegram.org/bot<токен>/... в ошибке сети).
"""

import logging
import re

# 123456789:AAH... — формат токена бота Telegram.
BOT_TOKEN = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")
# Телефон: 11–15 цифр подряд, с «+» или без (Telegram id короче — до 10 цифр).
PHONE = re.compile(r"(?<!\d)\+?\d{11,15}(?!\d)")

LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "aiogram", "sqlalchemy")


def redact(text: str) -> str:
    text = BOT_TOKEN.sub("<bot-token>", text)
    return PHONE.sub("<phone>", text)


class RedactingFormatter(logging.Formatter):
    def __init__(self, inner: logging.Formatter | None) -> None:
        super().__init__()
        self.inner = inner or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        return redact(self.inner.format(record))


def install_redaction() -> None:
    """Оборачивает форматтеры всех обработчиков логов. Повторный вызов безопасен."""
    for name in LOGGERS:
        for handler in logging.getLogger(name).handlers:
            if not isinstance(handler.formatter, RedactingFormatter):
                handler.setFormatter(RedactingFormatter(handler.formatter))


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    install_redaction()
