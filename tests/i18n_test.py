# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""Проверка локализации (не требует Qt, сети и аккаунта Mi Home).

Запуск:  python -m tests.i18n_test

Что проверяется:
  1. Каждый ``tr("…")``/``trf("…", …)`` в коде имеет перевод в ``locales/ru.json``.
  2. Плейсхолдеры в китайском ключе, русском переводе и именованных аргументах
     вызова ``trf`` совпадают (иначе подстановка упала бы во время работы).
  3. В ``ru.json`` нет «мёртвых» ключей, которых нет в коде.
  4. В русских переводах нет иероглифов (кроме явно разрешённых примеров).
  5. Китайские строковые литералы вне ``tr``/``trf``, логов и докстрингов —
     только из белого списка (логика/данные, не интерфейс).
  6. ``tr``/``trf``/контекст работают в обоих языках и устойчивы к ошибкам.
"""

import ast
import json
import os
import re
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
RU_JSON = APP / "i18n" / "locales" / "ru.json"

IDEOGRAPH = re.compile(r"[一-鿿]")
LOG_ATTRS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}

# Русские переводы, где иероглифы допустимы намеренно:
# пример голосовой команды для Сяо Ай (команды понимает только китайский ассистент),
# названия разделов китайского приложения и название языка.
ALLOWED_CJK_IN_RU = ("打开卧室灯", "智能", "界面语言", "中文")

# Китайские литералы вне tr(): данные/логика, а не текст интерфейса.
# Формат: (файл, подстрока литерала) — причина в комментарии.
ALLOWED_PLAIN = {
    ("app/core/service.py", "未知"),          # сентинел «нет комнаты»; на экране показывается через tr()
    ("app/core/service.py", "一"),        # границы диапазона CJK при разборе названий
    ("app/core/service.py", "鿿"),
    ("app/core/service.py", "一"),
    ("app/core/service.py", "鿿"),
    ("app/ui/main_window.py", "全屋"),        # ключи вкладок/домов (логика); подпись — tr(ключ)
    ("app/ui/main_window.py", "全部"),
    ("app/ui/main_window.py", "场景"),
    ("app/ui/main_window.py", "未知"),
    ("app/ui/room_filter.py", "未知"),
    ("app/ui/room_order_dialog.py", "全屋"),
    ("app/ui/workbench_panel.py", "开关"),    # эвристика по китайскому оригиналу заголовка
    ("app/ui/workbench_panel.py", "参数个数不匹配"),  # текст ошибки от upstream mijiaAPI
}
# Литералы, содержащие китайский комментарий внутри CSS/QSS (не текст интерфейса)
ALLOWED_PLAIN_SUBSTR = (
    ("app/ui/update_dialog.py", "代码块用窗口底色"),
)
SKIP_FILES = {
    "app/diagnostics.py",          # лог падений
    "app/ui/si_theme.py",          # QSS и служебные ошибки
    "app/core/spec_zh.py",         # таблица данных (ключи/значения для tr на экране)
    "app/core/net_guard.py",
    "app/core/_json_store.py",
}
SKIP_PREFIXES = ("app/siui/",)
SIUI_CHECKED = {"app/siui/components/editbox.py", "app/siui/components/widgets/button.py"}


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _fields(text: str) -> list[str]:
    # множество: один и тот же параметр может встречаться в шаблоне несколько раз
    return sorted({name for _, name, _, _ in string.Formatter().parse(text) if name is not None})


def _is_tr_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("tr", "trf"))


def _walk_py():
    for path in sorted(APP.rglob("*.py")):
        yield path, _rel(path)


def _parents(tree: ast.AST) -> dict:
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _in_log_call(node, parents) -> bool:
    n = parents.get(node)
    while n is not None:
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in LOG_ATTRS:
                base = f.value
                name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                if "log" in name.lower():
                    return True
            if isinstance(f, ast.Name) and f.id == "print":
                return True
        n = parents.get(n)
    return False


def _in_tr_call(node, parents) -> bool:
    n = parents.get(node)
    while n is not None:
        if _is_tr_call(n):
            return True
        n = parents.get(n)
    return False


def check_catalog() -> list[str]:
    errors: list[str] = []
    catalog = json.loads(RU_JSON.read_text(encoding="utf-8"))
    used: set[str] = set()

    for path, rel in list(_walk_py()) + [(ROOT / "run.py", "run.py")]:
        if rel == "app/i18n/__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not _is_tr_call(node) or not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue  # tr(<выражение>): перевод данных во время работы
            key = first.value
            ctx = ""
            if node.func.id == "tr" and len(node.args) > 1 \
                    and isinstance(node.args[1], ast.Constant):
                ctx = str(node.args[1].value)
            lookup = f"{ctx}|{key}" if ctx and f"{ctx}|{key}" in catalog else key
            used.add(lookup)
            ru = catalog.get(lookup)
            where = f"{rel}:{node.lineno}"
            if ru is None:
                errors.append(f"{where}: нет перевода для {key!r}")
                continue
            try:
                f_key, f_ru = _fields(key), _fields(ru)
            except ValueError as exc:
                errors.append(f"{where}: ошибка формата ({exc}) в {key!r}")
                continue
            if f_key != f_ru:
                errors.append(f"{where}: плейсхолдеры {f_key} != {f_ru} в переводе {ru[:40]!r}")
            if node.func.id == "trf":
                kwargs = sorted(k.arg for k in node.keywords if k.arg)
                if kwargs != f_key:
                    errors.append(f"{where}: аргументы trf {kwargs} != плейсхолдеры {f_key}")
            elif f_key and "%" not in key:
                errors.append(f"{where}: tr() с плейсхолдерами {f_key} — нужен trf()")

    # Служебные ключи, показываемые через tr(<ключ>) на данных
    manual = {"全屋", "全部", "未知", "场景"}
    spec_src = (APP / "core" / "spec_zh.py").read_text(encoding="utf-8")
    for node in ast.parse(spec_src).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict):
            for v in node.value.values:
                if isinstance(v, ast.Constant):
                    manual.add(v.value)
    for key in sorted(manual):
        if key not in catalog:
            errors.append(f"spec_zh/служебный ключ без перевода: {key!r}")
        used.add(key)

    for key in catalog:
        if key not in used:
            errors.append(f"ru.json: ключ не используется в коде: {key[:50]!r}")
    for key, ru in catalog.items():
        if not ru.strip():
            errors.append(f"ru.json: пустой перевод для {key[:40]!r}")
        if IDEOGRAPH.search(ru) and not any(a in ru for a in ALLOWED_CJK_IN_RU):
            errors.append(f"ru.json: иероглифы в переводе {ru[:60]!r}")
    return errors


def check_no_plain_chinese() -> list[str]:
    """Китайские литералы вне tr()/trf()/логов/докстрингов должны быть в белом списке."""
    errors: list[str] = []
    for path, rel in _walk_py():
        if rel in SKIP_FILES or rel == "app/i18n/__init__.py":
            continue
        if rel.startswith(SKIP_PREFIXES) and rel not in SIUI_CHECKED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            s = node.value
            if not IDEOGRAPH.search(s):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Expr):  # докстринг
                continue
            if _in_log_call(node, parents) or _in_tr_call(node, parents):
                continue
            # части f-строк разбираем по всей f-строке
            top = node
            while isinstance(parents.get(top), (ast.JoinedStr, ast.FormattedValue)):
                top = parents[top]
            if (rel, s) in ALLOWED_PLAIN:
                continue
            if any(rel == r and sub in s for r, sub in ALLOWED_PLAIN_SUBSTR):
                continue
            if isinstance(top, ast.JoinedStr) and any(
                    (rel, v.value) in ALLOWED_PLAIN
                    for v in top.values if isinstance(v, ast.Constant)):
                continue
            errors.append(f"{rel}:{node.lineno}: китайский литерал вне tr(): {s[:50]!r}")
    return errors


def check_imports() -> list[str]:
    """tr/trf, использованные в модуле, должны быть импортированы и не затенены."""
    errors: list[str] = []
    files = [(p, r) for p, r in _walk_py() if r != "app/i18n/__init__.py"]
    files.append((ROOT / "run.py", "run.py"))
    for path, rel in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.i18n":
                imported.update(a.asname or a.name for a in node.names)
        for name in ("tr", "trf"):
            used = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == name]
            if used and name not in imported:
                errors.append(f"{rel}:{used[0].lineno}: {name}() используется без импорта")
            if name in imported:
                for n in ast.walk(tree):
                    shadow = (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                              and n.id == name) or (isinstance(n, ast.arg) and n.arg == name)
                    if shadow:
                        errors.append(f"{rel}:{n.lineno}: имя {name} затеняет функцию перевода")
    return errors


def check_runtime() -> list[str]:
    """Поведение модуля i18n: языки, контекст, устойчивость к ошибкам в шаблоне."""
    errors: list[str] = []
    sys.path.insert(0, str(ROOT))
    from app import i18n

    prev = os.environ.get("MIHOME_LANG")
    try:
        i18n.set_language("zh")
        if i18n.tr("设置") != "设置":
            errors.append("zh: tr() должен возвращать исходную строку")
        if i18n.trf("共 {n} 台", n=3) != "共 3 台":
            errors.append("zh: trf() неверно подставляет параметры")
        i18n.set_language("ru")
        if i18n.tr("设置") != "Настройки":
            errors.append("ru: нет перевода «设置»")
        if i18n.tr("关闭", "close") != "Закрыть" or i18n.tr("关闭") != "Выкл.":
            errors.append("ru: контекст «关闭» не работает")
        if i18n.tr("关闭", "нет-такого-контекста") != "Выкл.":
            errors.append("ru: при неизвестном контексте нужен запасной вариант без контекста")
        if i18n.tr("Устройство-из-облака 客厅灯") != "Устройство-из-облака 客厅灯":
            errors.append("ru: незнакомые строки должны возвращаться как есть")
        if i18n.trf("设备列表无变化（共 {total} 台）", total=5) != "Список устройств не изменился (всего: 5)":
            errors.append("ru: trf() неверно подставляет параметры")
        # перевод с ошибкой в плейсхолдере не должен ронять интерфейс
        i18n._catalog["тест {a}"] = "тест {b}"
        if i18n.trf("тест {a}", a=1) != "тест 1":
            errors.append("trf() должен откатываться на исходный шаблон при ошибке перевода")
        del i18n._catalog["тест {a}"]
        if i18n.set_language("fr") != i18n.DEFAULT_LANGUAGE:
            errors.append("неизвестный язык должен приводиться к языку по умолчанию")
    finally:
        if prev is None:
            os.environ.pop("MIHOME_LANG", None)
        else:
            os.environ["MIHOME_LANG"] = prev
    return errors


def main() -> int:
    failed = 0
    for name, fn in (("каталог перевода", check_catalog),
                     ("китайские литералы вне tr()", check_no_plain_chinese),
                     ("импорт tr/trf во всех модулях", check_imports),
                     ("поведение i18n", check_runtime)):
        errors = fn()
        if errors:
            failed += len(errors)
            print(f"[FAIL] {name}: {len(errors)}")
            for e in errors[:60]:
                print("   ", e)
        else:
            print(f"[ OK ] {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
