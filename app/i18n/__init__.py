# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""Локализация интерфейса (ru / zh).

Принцип работы
--------------
Исходные строки программы написаны по-китайски, и именно китайский текст
служит ключом перевода (как msgid в gettext). Поэтому:

* ``tr("设置")`` — вернёт русский вариант при выбранном русском языке и
  исходную строку при китайском (``tr("关闭", "close")`` — вариант с
  контекстом, см. docstring ``tr``);
* ``trf("共 {total} 台设备", total=n)`` — то же для шаблонов с параметрами;
* строки, которых нет в каталоге (названия устройств, комнат, сцен и прочие
  данные из облака Xiaomi), возвращаются без изменений.

Русский каталог лежит в отдельном файле ``app/i18n/locales/ru.json``
(плоский словарь «китайская строка → русская строка»). Для обновления
перевода достаточно отредактировать этот файл — код трогать не нужно.

Выбор языка
-----------
Приоритет: переменная окружения ``MIHOME_LANG`` → поле ``language`` в
``settings.json`` → язык по умолчанию (русский). Язык фиксируется при
запуске процесса, поэтому смена языка в настройках требует перезапуска
приложения (так же, как смена масштаба интерфейса).

Модуль намеренно не зависит от Qt и от остального кода приложения.
"""

import json
import logging
import os
from pathlib import Path

_logger = logging.getLogger(__name__)

LANG_RU = "ru"
LANG_ZH = "zh"
DEFAULT_LANGUAGE = LANG_RU

# (код, название на самом языке): названия не переводятся, чтобы пользователь
# мог найти свой язык, даже если интерфейс открыт на незнакомом ему языке
LANGUAGES: tuple[tuple[str, str], ...] = (
    (LANG_RU, "Русский"),
    (LANG_ZH, "中文"),
)

_ENV_LANG = "MIHOME_LANG"
_ENV_LOG_MISSING = "MIHOME_I18N_LOG_MISSING"

_current: str | None = None
_catalog: dict[str, str] = {}
_missing_logged: set[str] = set()


def _normalize(code: object) -> str | None:
    """Приводит значение к поддерживаемому коду языка либо None."""
    if not isinstance(code, str):
        return None
    value = code.strip().lower().replace("_", "-")
    if value.startswith("ru"):
        return LANG_RU
    if value.startswith("zh") or value.startswith("cn"):
        return LANG_ZH
    return None


def _language_from_settings() -> str | None:
    """Читает язык из settings.json напрямую, не импортируя settings_store."""
    try:
        from app.core import _json_store

        raw = _json_store.read_json(_json_store.data_file("settings.json"), {})
    except Exception:
        return None
    return _normalize(raw.get("language")) if isinstance(raw, dict) else None


def _locale_dir() -> Path:
    """Каталог с файлами локализации (источник и сборка Nuitka)."""
    try:
        from app import resource_path

        return resource_path("app/i18n/locales")
    except Exception:
        return Path(__file__).resolve().parent / "locales"


def _load_catalog(lang: str) -> dict[str, str]:
    if lang == LANG_ZH:
        return {}
    path = _locale_dir() / f"{lang}.json"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        _logger.warning("Не удалось загрузить перевод %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def get_language() -> str:
    """Текущий язык интерфейса; при первом обращении определяется и загружается."""
    global _current, _catalog
    if _current is None:
        lang = (_normalize(os.environ.get(_ENV_LANG))
                or _language_from_settings()
                or DEFAULT_LANGUAGE)
        _current = lang
        _catalog = _load_catalog(lang)
    return _current


def set_language(lang: str) -> str:
    """Переключает язык в памяти процесса и возвращает применённый код.

    Уже построенные виджеты не перерисовываются: полноценное применение
    языка — после перезапуска приложения. Сохранение выбора в настройки
    выполняет ``settings_store.set_language``.
    """
    global _current, _catalog
    code = _normalize(lang) or DEFAULT_LANGUAGE
    _current = code
    _catalog = _load_catalog(code)
    _missing_logged.clear()
    return code


def tr(text: str, context: str = "") -> str:
    """Перевод строки; неизвестные строки возвращаются как есть.

    ``context`` нужен, когда один и тот же китайский текст в разных местах
    требует разных русских слов (например «关闭»: «Закрыть» у кнопки и
    «Выключить» у действия). Ключ в ru.json тогда записывается как
    ``"контекст|китайский текст"``; если такого ключа нет, берётся обычный.
    """
    if _current is None:
        get_language()
    if not _catalog:
        return text
    if context:
        result = _catalog.get(f"{context}|{text}")
        if result is not None:
            return result
    result = _catalog.get(text)
    if result is not None:
        return result
    if os.environ.get(_ENV_LOG_MISSING) and text not in _missing_logged:
        _missing_logged.add(text)
        _logger.info("i18n: нет перевода для %r", text)
    return text


def trf(template: str, **kwargs) -> str:
    """Перевод шаблона и подстановка параметров через ``str.format``.

    Ошибка в переводе (неверное имя параметра, лишняя фигурная скобка) не
    должна ронять интерфейс: тогда используется исходный шаблон.
    """
    text = tr(template)
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        _logger.warning("i18n: ошибка в шаблоне перевода %r", text)
        return template.format(**kwargs)


def available_languages() -> tuple[tuple[str, str], ...]:
    return LANGUAGES
