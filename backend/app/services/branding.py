"""Оформление клуба: палитра, надпись в центре колеса, префикс промокодов, тексты, картинки.

Хранится в clubs.branding (JSONB). Пустое поле — оформление по умолчанию, как было раньше.
Картинки (логотип, баннер) лежат на сервере в MEDIA_DIR/<club_id>/ и раздаются по /media/...
Ссылки на файлы Telegram не используются: в них есть токен бота.
"""

import hashlib
import re
from pathlib import Path

from app.config import get_settings
from app.models import Club

DEFAULT_PALETTE = "classic"
DEFAULT_HUB_LABEL = "GG"
DEFAULT_CODE_PREFIX = "GG"
HUB_LABEL_MAX = 4
TEXT_MAX = 300
IMAGE_MAX_BYTES = 5 * 1024 * 1024
IMAGE_KINDS = ("logo", "banner")
TEXT_KINDS = {
    "welcome": "Приветствие",
    "win": "Выигрыш",
    "lose": "Пустой сектор",
}

# accent — кнопки и выделения; wheel — сектора по кругу; empty — пустой сектор; rim — обод колеса.
PALETTES: dict[str, dict] = {
    "classic": {"title": "Классика", "accent": "#7c5cff", "empty": "#5b6475", "rim": "#232733",
                "wheel": ["#7c5cff", "#ff5c8a", "#ffb03b", "#2ec4b6", "#4d8cff", "#ff7a45", "#a35cff", "#22c55e"]},
    "neon": {"title": "Неон", "accent": "#00d4ff", "empty": "#3a3f55", "rim": "#0d0f1a",
             "wheel": ["#00d4ff", "#ff2bd6", "#7cff4f", "#ffe600", "#7a5cff", "#ff6b2b"]},
    "sunset": {"title": "Закат", "accent": "#ff6b35", "empty": "#6d597a", "rim": "#2b1b2e",
               "wheel": ["#ff6b35", "#f7c548", "#e84855", "#f49d37", "#c2185b", "#ff9f80"]},
    "ocean": {"title": "Океан", "accent": "#1e88e5", "empty": "#546e7a", "rim": "#0f2233",
              "wheel": ["#1e88e5", "#00acc1", "#3949ab", "#26a69a", "#4fc3f7", "#5c6bc0"]},
    "forest": {"title": "Лес", "accent": "#2e7d32", "empty": "#6d6d5f", "rim": "#1b2a1c",
               "wheel": ["#2e7d32", "#66bb6a", "#00796b", "#c0ca33", "#8d6e63", "#43a047"]},
    "gold": {"title": "Золото", "accent": "#c9a227", "empty": "#4a4a4a", "rim": "#111111",
             "wheel": ["#c9a227", "#2b2b2b", "#e0b84c", "#3d3d3d", "#a8841a", "#575757"]},
}

PREFIX_RE = re.compile(r"^[A-Z]{2,4}$")


class BrandingError(ValueError):
    """Понятная причина отказа для ответа владельцу."""


def data(club: Club) -> dict:
    return dict(club.branding or {})


def code_prefix(club: Club) -> str:
    return data(club).get("code_prefix") or DEFAULT_CODE_PREFIX


def text(club: Club, kind: str) -> str | None:
    return (data(club).get("texts") or {}).get(kind)


def _save(club: Club, updated: dict) -> None:
    # Новый словарь, чтобы SQLAlchemy увидел изменение JSONB.
    club.branding = {k: v for k, v in updated.items() if v not in (None, "", {})} or None


def set_palette(club: Club, key: str) -> None:
    if key not in PALETTES:
        raise BrandingError("Такой палитры нет")
    _save(club, {**data(club), "palette": key})


def set_hub_label(club: Club, label: str) -> None:
    label = label.strip()
    if not 1 <= len(label) <= HUB_LABEL_MAX:
        raise BrandingError(f"Надпись в центре колеса — от 1 до {HUB_LABEL_MAX} символов")
    _save(club, {**data(club), "hub_label": label})


def set_code_prefix(club: Club, prefix: str) -> None:
    prefix = prefix.strip().upper()
    if not PREFIX_RE.match(prefix):
        raise BrandingError("Префикс — от 2 до 4 латинских букв, например CA")
    _save(club, {**data(club), "code_prefix": prefix})


def set_text(club: Club, kind: str, value: str) -> None:
    if kind not in TEXT_KINDS:
        raise BrandingError("Неизвестный текст")
    value = value.strip()
    if not 1 <= len(value) <= TEXT_MAX:
        raise BrandingError(f"Текст — от 1 до {TEXT_MAX} символов")
    texts = {**(data(club).get("texts") or {}), kind: value}
    _save(club, {**data(club), "texts": texts})


def media_dir() -> Path:
    return Path(get_settings().media_dir)


def image_path(club: Club, kind: str) -> Path | None:
    name = data(club).get(kind)
    return media_dir() / str(club.id) / name if name else None


def image_url(club: Club, kind: str) -> str | None:
    name = data(club).get(kind)
    return f"/media/{club.id}/{name}" if name else None


def _image_extension(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    raise BrandingError("Нужна картинка JPG, PNG или WebP")


def set_image(club: Club, kind: str, content: bytes) -> Path:
    """Сохраняет логотип или баннер; прежний файл того же вида удаляется."""
    if kind not in IMAGE_KINDS:
        raise BrandingError("Неизвестная картинка")
    if len(content) > IMAGE_MAX_BYTES:
        raise BrandingError("Картинка больше 5 МБ")
    ext = _image_extension(content)
    folder = media_dir() / str(club.id)
    folder.mkdir(parents=True, exist_ok=True)
    old = image_path(club, kind)
    name = f"{kind}-{hashlib.sha256(content).hexdigest()[:12]}.{ext}"
    (folder / name).write_bytes(content)
    if old is not None and old.name != name:
        old.unlink(missing_ok=True)
    _save(club, {**data(club), kind: name})
    return folder / name


def reset(club: Club) -> None:
    """Сбрасывает оформление, кроме префикса кодов: иначе ручной ввод старых кодов без префикса сломается."""
    for kind in IMAGE_KINDS:
        path = image_path(club, kind)
        if path is not None:
            path.unlink(missing_ok=True)
    prefix = data(club).get("code_prefix")
    _save(club, {"code_prefix": prefix})


def resolved(club: Club) -> dict:
    """Оформление для Mini App со значениями по умолчанию."""
    b = data(club)
    palette_key = b.get("palette") if b.get("palette") in PALETTES else DEFAULT_PALETTE
    palette = PALETTES[palette_key]
    texts = b.get("texts") or {}
    return {
        "palette": palette_key,
        "accent": palette["accent"],
        "wheel": palette["wheel"],
        "empty": palette["empty"],
        "rim": palette["rim"],
        "hub_label": b.get("hub_label") or DEFAULT_HUB_LABEL,
        "code_prefix": b.get("code_prefix") or DEFAULT_CODE_PREFIX,
        "logo_url": image_url(club, "logo"),
        "texts": {"win": texts.get("win"), "lose": texts.get("lose")},
        "customized": bool(b),
    }
