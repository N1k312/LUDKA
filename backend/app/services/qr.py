"""QR-код для печати у кассы: ссылка на бота клуба."""

from pathlib import Path

import segno

DEFAULT_SOURCE = "desk"


def start_link(bot_username: str, source: str = DEFAULT_SOURCE, startapp: bool = False) -> str:
    """t.me/{bot}?start=desk, или ?startapp=desk при включённом Main Mini App."""
    param = "startapp" if startapp else "start"
    return f"https://t.me/{bot_username}?{param}={source}"


def save_qr(link: str, path: Path) -> Path:
    """PNG для печати (крупные модули) или SVG — по расширению файла."""
    path.parent.mkdir(parents=True, exist_ok=True)
    qr = segno.make(link, error="m")
    if path.suffix.lower() == ".svg":
        qr.save(path, scale=10, border=4)
    else:
        qr.save(path, scale=20, border=4)
    return path
