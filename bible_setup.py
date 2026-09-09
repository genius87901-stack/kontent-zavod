# -*- coding: utf-8 -*-
"""
bible_setup — подключение КНИГИ СМЫСЛОВ клиента к заводу (одним шагом).

ЗАЧЕМ. Мастер установки говорит клиенту: «загрузи книгу смыслов в папку
«5 Смыслы и настройки» на Google Диске». А код читает её СОВСЕМ НЕ ОТТУДА —
из корня установки, по имени файла (modules/script_writer/bible.py,
modules/content_plan/caption_bridge.py). Копирования между этими адресами не
было, поэтому клиент, честно выполнивший инструкцию, получал завод в урезанном
режиме: общие заготовки вместо своих формулировок, чужое кодовое слово — и НИ
ОДНОГО сообщения об ошибке. Молчаливый провал, который клиент замечал уже по
пустому контенту.

ЧТО ДЕЛАЕТ. Находит книгу там, куда клиент её реально положил (Диск, корень
завода, любой указанный путь), кладёт копию в корень установки и записывает её
имя в конфиг — после этого её видят ОБА читателя (сценарист и подписи).

ЗАПУСК:
    python bible_setup.py --status            # что подключено сейчас
    python bible_setup.py --find              # где лежат кандидаты (ничего не меняет)
    python bible_setup.py --install           # подключить найденное (самое свежее)
    python bible_setup.py --install "<путь>"  # подключить конкретный файл

Коды возврата: 0 — успех; 1 — книга не найдена / не подключилась; 2 — плохой ввод.
Только stdlib. Без input(). Секретов не печатает и в сеть не ходит.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# Консоль русской Windows — cp1251, и ЛЮБОЙ значок вне неё (→ ✓ ✗ • ⚠) роняет
# скрипт UnicodeEncodeError прямо в лицо клиенту. Переводим свой вывод в utf-8
# с заменой непечатаемого: ни один текст мастера больше не может убить установку.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # старый питон или подменённый stdout — не критично
    pass


# Ключ конфига с именем файла книги. НЕ заводим второй: этот уже спрашивает
# установщик (modules/script_writer/module.manifest.json) и уже читает сценарист.
# Второй ключ разошёлся бы с первым при первой же правке.
BIBLE_KEY = "script_bible_file"

# Как называется файл, если своего имени нет (совместимо с дефолтом читателей).
FALLBACK_NAME = "CONTENT_BIBLE.md"

# Папка книги смыслов на Google Диске — из единого контракта структуры
# (modules/intake/drive_structure.json, folder id «meanings»). Хардкод здесь
# был бы вторым источником правды: контракт читаем, а это — запасной вариант
# на случай, если файла контракта рядом нет.
MEANINGS_FALLBACK = "5 Смыслы и настройки"
DRIVE_ROOT_FALLBACK = "КонтентЗавод"

# Признаки того, что перед нами действительно книга смыслов, а не случайный .md.
# Достаточно ОДНОГО совпадения: книги клиентов расходятся по оформлению, и
# придирчивая проверка отвергала бы живые файлы.
_MARKERS = (
    re.compile(r"100\s+ТЕМ", re.I),
    re.compile(r"50\s+ХУКОВ", re.I),
    re.compile(r"CONTENT\s*BIBLE", re.I),
    re.compile(r"КНИГА\s+СМЫСЛОВ", re.I),
    re.compile(r"ХУКИ\s+ДЛЯ\s+REELS", re.I),
)


# ── корень установки ─────────────────────────────────────────────────────────

def install_root() -> Path:
    """Корень завода. Берём общий ридер установщика — он знает про .factory_root
    и не путается, когда завод лежит внутри другой папки с модулями."""
    here = Path(__file__).resolve().parent
    try:
        sys.path.insert(0, str(here / "install"))
        import factory_config                              # noqa: WPS433
        return Path(factory_config.install_root())
    except Exception:
        return here


def _config_path(root: Path) -> Path:
    return root / "config" / "factory.config.json"


def _load_config(root: Path) -> dict:
    path = _config_path(root)
    try:
        with path.open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_config_key(root: Path, key: str, value: str) -> None:
    """Записать ОДИН несекретный ключ, сохранив остальные. Конфиг клиента —
    не наша собственность: перезаписываем его целиком только после чтения."""
    path = _config_path(root)
    data = _load_config(root)
    data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ── где искать книгу ─────────────────────────────────────────────────────────

def _meanings_dirname(root: Path) -> str:
    """Имя папки смыслов из контракта структуры Диска (единственный источник)."""
    contract = root / "modules" / "intake" / "drive_structure.json"
    try:
        with contract.open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
        for folder in data.get("folders", []):
            if folder.get("id") == "meanings":
                return str(folder.get("path") or MEANINGS_FALLBACK)
    except (OSError, ValueError, TypeError):
        pass
    return MEANINGS_FALLBACK


def _drive_rootname(root: Path) -> str:
    contract = root / "modules" / "intake" / "drive_structure.json"
    try:
        with contract.open(encoding="utf-8-sig") as fh:
            return str(json.load(fh).get("root") or DRIVE_ROOT_FALLBACK)
    except (OSError, ValueError, TypeError):
        return DRIVE_ROOT_FALLBACK


def looks_like_bible(path: Path) -> bool:
    """Похоже ли содержимое на книгу смыслов. Читаем начало файла: книги бывают
    на сотни килобайт, а маркеры стоят в заголовках."""
    try:
        head = path.read_text(encoding="utf-8-sig", errors="replace")[:20000]
    except OSError:
        return False
    return any(rx.search(head) for rx in _MARKERS)


def search_dirs(root: Path) -> list[Path]:
    """Где ищем книгу, в порядке доверия: папка смыслов на Диске → корень завода."""
    dirs = []
    gdrive = (_load_config(root).get("gdrive_root") or "").strip()
    if gdrive:
        base = Path(gdrive)
        dirs.append(base / _drive_rootname(root) / _meanings_dirname(root))
        # Клиент мог создать структуру прямо в выбранной папке, без корневой.
        dirs.append(base / _meanings_dirname(root))
    dirs.append(root)
    return [d for d in dirs if d.is_dir()]


def candidate_score(root: Path, path: Path) -> int:
    """Насколько файл похож на книгу смыслов ИМЕННО как источник для завода.

    Одних маркеров в тексте мало: слова «книга смыслов» встречаются и в рабочих
    заметках. Поэтому считаем то, ради чего книга нужна, — сколько хуков и тем
    из неё реально достанут парсеры модулей. Файл с именем CONTENT_BIBLE* идёт
    вперёд: так его назвали мы сами, это сильный сигнал.
    """
    score = 0
    if path.stem.upper().startswith("CONTENT_BIBLE"):
        score += 1000
    info = parsed_summary(root, path)
    score += info["hooks"] * 10 + info["themes"]
    return score


def find_candidates(root: Path) -> list[tuple[Path, int]]:
    """Кандидаты с оценкой, лучший первым. Дубликаты по одному и тому же
    физическому файлу схлопываем (Диск и корень могут указывать в одно место)."""
    found: dict[str, Path] = {}
    for d in search_dirs(root):
        try:
            entries = sorted(d.glob("*.md"))
        except OSError:
            continue
        for p in entries:
            if not p.is_file() or not looks_like_bible(p):
                continue
            try:
                key = str(p.resolve()).lower()
            except OSError:
                key = str(p).lower()
            found.setdefault(key, p)
    scored = [(p, candidate_score(root, p)) for p in found.values()]
    # Оценка, потом свежесть: два одинаково пригодных файла — берём новый.
    scored.sort(key=lambda it: (it[1], it[0].stat().st_mtime if it[0].exists() else 0),
                reverse=True)
    return scored


# ── чтение того, что реально распарсится ─────────────────────────────────────

#: сколько заголовков показываем в самодиагностике и по сколько символов —
#: экран клиента, а не лог: длинный список читать никто не станет.
_MAX_SHOWN_HEADERS = 15
_HEADER_CHARS = 60


def parsed_summary(root: Path, path: Path) -> dict:
    """Сколько завод РЕАЛЬНО достанет из этой книги. Считаем теми же
    парсерами, что и модули, — иначе отчёт врал бы клиенту в лицо.

    Ключ `read_ok` — ГЛАВНЫЙ. До 10.08.2026 его не было: при нечитаемом файле
    (нет доступа, битая кодировка, парсер не импортировался) функция молча
    возвращала те же самые нули, что и при «файл прочитан, но разделы не
    опознаны». На экране обе беды выглядели одинаково — «хуков в банке: 0» —
    а лечатся они противоположным. Неотличимость «сломано» от «пусто» этому
    проекту уже стоила двух недель, поэтому состояния разведены явно:
        read_ok=False → счётчики НИЧЕГО не значат, смотри `error`;
        read_ok=True и нули → книга прочитана, разделы не распознались,
                              смотри `headers` — что парсер в ней видит.
    """
    out = {"hooks": 0, "themes": 0, "rubrics": 0, "code_word": None,
           "read_ok": False, "error": "", "headers": []}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, ValueError) as exc:                  # ValueError ⊃ UnicodeDecodeError
        out["error"] = "файл не читается (%s)" % type(exc).__name__
        return out
    sys.path.insert(0, str(root))
    try:
        from modules.script_writer import bible as sw_bible   # noqa: WPS433
        data = sw_bible.load_bible(text=text)
        themes = data.get("themes") or {}
        out["hooks"] = len(data.get("hooks") or [])
        out["themes"] = sum(len(v) for v in themes.values())
        out["rubrics"] = len(themes)
        out["code_word"] = data.get("code_word")
        out["read_ok"] = True
        # Заголовки — тем же кодом, что и парсер секций: свой разбор здесь стал
        # бы вторым источником правды и разошёлся бы с ним при первой правке.
        headers = getattr(sw_bible, "section_headers", None)
        if headers is not None:
            out["headers"] = list(headers(text=text))
    except Exception as exc:                              # noqa: BLE001
        out["error"] = "разбор книги не сработал (%s)" % type(exc).__name__
    return out


def _diagnose(info: dict) -> str:
    """Хвост отчёта: почему счётчики такие. Пусто — когда всё в порядке.

    Печатаем ТОЛЬКО строки-заголовки: содержимое книги клиента на экран не
    выводим ни при каких условиях.
    """
    if not info.get("read_ok"):
        return ("\nВНИМАНИЕ: книгу НЕ УДАЛОСЬ ПРОЧИТАТЬ — %s.\n"
                "Нули выше не про содержимое книги: до содержимого дело не "
                "дошло. Проверь, что файл на месте, открывается и сохранён "
                "в UTF-8." % (info.get("error") or "причина неизвестна"))
    if info.get("hooks") or info.get("themes"):
        return ""
    out = ("\nВНИМАНИЕ: книга прочиталась, но разделы «50 ХУКОВ» и «100 ТЕМ» в "
           "ней не распознались — завод соберёт тексты, но на общих заготовках.")
    heads = info.get("headers") or []
    if not heads:
        return out + ("\nЗаголовков в файле не видно вовсе: разделы не выделены "
                      "ни решётками «#», ни жирным, ни заглавными буквами.")
    out += "\nЗаголовки, которые я в этой книге вижу (всего %d):\n" % len(heads)
    out += "\n".join("  - %s" % (h[:_HEADER_CHARS] +
                                 ("…" if len(h) > _HEADER_CHARS else ""))
                     for h in heads[:_MAX_SHOWN_HEADERS])
    if len(heads) > _MAX_SHOWN_HEADERS:
        out += "\n  … и ещё %d" % (len(heads) - _MAX_SHOWN_HEADERS)
    return out + ("\nСреди них нужен заголовок со словом «хук» и заголовок со "
                  "словом «темы» вместе с «постов» или «рубрик» — назови "
                  "разделы так, и всё прочитается.")


def current_bible(root: Path):
    """Книга, подключённая СЕЙЧАС: (путь, есть_ли_файл). Имя — из конфига."""
    name = (_load_config(root).get(BIBLE_KEY) or "").strip()
    if not name:
        # Дефолт читателей: любой CONTENT_BIBLE*.md в корне.
        matches = sorted(root.glob("CONTENT_BIBLE*.md"))
        if matches:
            return matches[0], True
        return None, False
    path = root / name
    return path, path.is_file()


# ── действия ─────────────────────────────────────────────────────────────────

def _target_name(root: Path, src: Path) -> str:
    """Имя файла книги в корне завода. Держим префикс CONTENT_BIBLE — по нему
    книгу находят и читатели с дефолтом, и глаз человека в папке."""
    stem = src.stem.strip()
    if stem.upper().startswith("CONTENT_BIBLE"):
        return src.name
    client = (_load_config(root).get("client_name") or "").strip()
    slug = re.sub(r"[^\w\-]+", "_", client or stem, flags=re.UNICODE).strip("_")
    return ("CONTENT_BIBLE_%s.md" % slug) if slug else FALLBACK_NAME


def do_install(root: Path, explicit: str | None, as_json: bool) -> int:
    if explicit:
        src = Path(explicit).expanduser()
        if not src.is_file():
            _out(as_json, {"ok": False, "reason": "нет файла"},
                 "Не нашёл файл: %s\nПроверь путь и повтори." % src)
            return 1
        if not looks_like_bible(src):
            # Не отказ: книги бывают в вольном оформлении. Но честно предупредим.
            print("ВНИМАНИЕ:В файле не нашлись привычные разделы книги смыслов "
                  "(«100 ТЕМ», «50 ХУКОВ»). Подключаю как есть — проверь потом "
                  "командой --status, сколько тем и хуков прочиталось.")
    else:
        cands = find_candidates(root)
        if not cands:
            _out(as_json, {"ok": False, "reason": "не найдена"},
                 "Книгу смыслов не нашёл.\n"
                 "Искал здесь:\n" + "\n".join("  - %s" % d for d in search_dirs(root)) +
                 "\nПоложи файл в папку «%s» на Google Диске или укажи путь: "
                 "bible_setup.py --install \"<путь к файлу>\"" % _meanings_dirname(root))
            return 1
        src, best = cands[0]
        if best <= 0:
            # Похожие по словам файлы есть, но ни из одного завод НИЧЕГО не
            # достанет. Молча подключить такой — вернуть ровно ту поломку, ради
            # которой скрипт и написан. Просим указать файл явно.
            _out(as_json, {"ok": False, "reason": "кандидаты пустые",
                           "candidates": [str(p) for p, _ in cands]},
                 "Нашёл похожие файлы, но ни в одном нет ни хуков, ни тем — "
                 "подключать такое нельзя, завод остался бы на общих заготовках.\n"
                 "Кандидаты:\n" + "\n".join("  - %s" % p for p, _ in cands) +
                 "\nУкажи книгу явно: bible_setup.py --install \"<путь к файлу>\"")
            return 1

    target = root / _target_name(root, src)
    try:
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
    except OSError as exc:
        _out(as_json, {"ok": False, "reason": str(exc)},
             "Не смог скопировать книгу в папку завода: %s" % exc)
        return 1

    _save_config_key(root, BIBLE_KEY, target.name)
    info = parsed_summary(root, target)
    payload = {"ok": True, "source": str(src), "installed": target.name, **info}
    _out(as_json, payload,
         "Книга смыслов подключена: %s\n"
         "  взято отсюда: %s\n"
         "  кодовое слово: %s\n"
         "  хуков в банке: %d\n"
         "  тем по рубрикам: %d (рубрик: %d)%s"
         % (target.name, src, info["code_word"] or "—",
            info["hooks"], info["themes"], info["rubrics"], _diagnose(info)))
    return 0


def do_status(root: Path, as_json: bool) -> int:
    path, exists = current_bible(root)
    if not exists:
        _out(as_json, {"ok": False, "connected": None},
             "Книга смыслов НЕ подключена — завод пишет на общих заготовках.\n"
             "Подключить: bible_setup.py --install")
        return 1
    info = parsed_summary(root, path)
    _out(as_json, {"ok": True, "connected": path.name, **info},
         "Книга смыслов подключена: %s\n"
         "  кодовое слово: %s\n  хуков в банке: %d\n  тем по рубрикам: %d (рубрик: %d)%s"
         % (path.name, info["code_word"] or "—", info["hooks"],
            info["themes"], info["rubrics"], _diagnose(info)))
    return 0


def do_find(root: Path, as_json: bool) -> int:
    cands = find_candidates(root)
    payload = {"ok": bool(cands), "dirs": [str(d) for d in search_dirs(root)],
               "candidates": [{"path": str(p), "score": s} for p, s in cands]}
    if not cands:
        _out(as_json, payload,
             "Кандидатов не нашёл. Искал здесь:\n" +
             "\n".join("  - %s" % d for d in search_dirs(root)))
        return 1
    _out(as_json, payload,
         "Нашёл книги смыслов (лучшая первой):\n" +
         "\n".join("  %d) %s%s" % (i, p, "" if s > 0 else "   (пусто, не годится)")
                   for i, (p, s) in enumerate(cands, 1)))
    return 0


def _out(as_json: bool, payload: dict, human: str) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(human)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Подключение книги смыслов клиента к заводу.")
    ap.add_argument("--root", help="корень установки (по умолчанию — авто)")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true", help="что подключено сейчас")
    g.add_argument("--find", action="store_true", help="где лежат кандидаты")
    g.add_argument("--install", nargs="?", const="", metavar="ПУТЬ",
                   help="подключить книгу (без пути — найти самому)")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve() if args.root else install_root()
    if not root.is_dir():
        print("Нет такой папки: %s" % root, file=sys.stderr)
        return 2

    if args.status:
        return do_status(root, args.json)
    if args.find:
        return do_find(root, args.json)
    return do_install(root, args.install or None, args.json)


if __name__ == "__main__":
    sys.exit(main())
