# -*- coding: utf-8 -*-
"""
install_music — отдельный шаг установки: наполнение банка музыки «Контент-завода».

ЗАЧЕМ ОТДЕЛЬНЫМ ШАГОМ. Банк музыки (свои треки под Reels, ~49 файлов) — это МЕДИА,
а не код. Он не входит в базовый клиентский .zip (в архиве на его месте — пустая
папка-приёмник `music/.keep`, как и у photos/video/stock). Так базовый архив
остаётся лёгким. Музыка доустанавливается ЭТИМ шагом — аддитивно и идемпотентно,
и НИКОГДА не тянется из сети молча: скрипт только КОПИРУЕТ файлы из локального
источника, который вы указали явно.

ОТКУДА БЕРЁТСЯ МУЗЫКА (источник по приоритету):
  1. --src PATH            — путь к папке с треками, переданный явно;
  2. переменная окружения  FACTORY_MUSIC_SRC;
  3. ключ music_src        в config/factory.config.json (несекретный конфиг);
  4. если <корень>/music уже наполнен — шаг ничего не делает (идемпотентность).

  • На DEV-машине источник — локальная папка/симлинк с банком (у владельца music/
    указывает на его Google Drive). Владелец разово прогоняет шаг и кладёт треки.
  • У КЛИЕНТА источника нет: базовый архив приезжает с пустым music/. Тогда шаг
    НЕ падает и НЕ лезет в интернет — он печатает понятную инструкцию: положить
    свои лицензионные треки в папку music/ (или указать music_src) и повторить.
    Завод работает и без банка: движок берёт музыку автоматически через Openverse
    (см. music.py) — банк лишь даёт свои проверенные треки.

Идемпотентность: повторный запуск не дублирует уже лежащие файлы (сверка по имени)
и безопасен. Только stdlib. Кроссплатформенно. Без абсолютных путей.

Запуск (из корня установки):
    python install/install_music.py                 # наполнить из найденного источника
    python install/install_music.py --src PATH       # из конкретной папки
    python install/install_music.py --status         # что с банком сейчас (по-русски)
    python install/install_music.py --json           # машинная сводка для мастера

Код возврата: 0 — успех/нечего делать/нет источника (не ошибка); 2 — плохой --src.
"""
from __future__ import annotations

import argparse
import json
import os
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


# Расширения аудио — ровно те, что банк music.py считает треками (music.AUDIO_EXT).
AUDIO_EXT = (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac")
BANK_DIRNAME = "music"
CONFIG_KEY = "music_src"


def find_root(explicit: str | None = None) -> Path:
    """Корень установки — тот же контракт поиска, что и в остальных install-скриптах."""
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "install" else Path.cwd()


def _audio_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in AUDIO_EXT)


def _config_music_src(root: Path) -> str | None:
    cfg = root / "config" / "factory.config.json"
    if not cfg.is_file():
        return None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    val = data.get(CONFIG_KEY)
    return str(val).strip() or None if val else None


def resolve_source(root: Path, explicit: str | None) -> tuple[Path | None, str]:
    """Найти источник музыки. Возвращает (путь-или-None, откуда-взяли)."""
    if explicit:
        return Path(explicit).expanduser(), "аргумент --src"
    env = os.environ.get("FACTORY_MUSIC_SRC", "").strip()
    if env:
        return Path(env).expanduser(), "переменная FACTORY_MUSIC_SRC"
    cfg = _config_music_src(root)
    if cfg:
        return Path(cfg).expanduser(), "config/factory.config.json (music_src)"
    return None, "источник не задан"


def status(root: Path) -> dict:
    bank = root / BANK_DIRNAME
    tracks = _audio_files(bank)
    src, origin = resolve_source(root, None)
    src_tracks = _audio_files(src) if src and src.is_dir() else []
    return {
        "bank_dir": str(bank),
        "bank_present": bank.is_dir(),
        "bank_tracks": len(tracks),
        "source": str(src) if src else None,
        "source_origin": origin,
        "source_exists": bool(src and src.is_dir()),
        "source_tracks": len(src_tracks),
    }


def install(root: Path, explicit_src: str | None) -> tuple[int, list[str]]:
    """Наполнить банк из источника. Возвращает (код_возврата, строки_отчёта)."""
    out: list[str] = []
    bank = root / BANK_DIRNAME
    bank.mkdir(parents=True, exist_ok=True)          # приёмник существует всегда

    src, origin = resolve_source(root, explicit_src)
    have = {p.name for p in _audio_files(bank)}

    # Источник не задан вовсе → клиентский сценарий: не ошибка, инструкция.
    if src is None:
        if have:
            out.append(f"Банк музыки уже наполнен: {len(have)} трек(ов) в «{BANK_DIRNAME}/». "
                       f"Источник не задавали — оставляю как есть.")
        else:
            out.append("Банк музыки пуст, источник не задан — это нормально.")
            out.append(f"Чтобы добавить свои треки: положите лицензионные файлы "
                       f"({', '.join(AUDIO_EXT)}) в папку «{BANK_DIRNAME}/» — и всё.")
            out.append("Либо укажите папку с треками: "
                       "переменной FACTORY_MUSIC_SRC, ключом music_src в "
                       "config/factory.config.json или запуском с --src ПУТЬ.")
            out.append("Из интернета ничего не скачиваю. Завод работает и без банка: "
                       "музыку под Reels движок подберёт сам (Openverse).")
        return 0, out

    if explicit_src and not src.is_dir():
        out.append(f"Указанная папка-источник не найдена: {src} — проверьте путь (--src).")
        return 2, out

    if not src.is_dir():
        out.append(f"Источник музыки ({origin}) недоступен: {src}. "
                   f"Пропускаю — банк не тронут. Из сети ничего не тяну.")
        return 0, out

    src_tracks = _audio_files(src)
    if not src_tracks:
        out.append(f"В источнике ({origin}) нет аудиофайлов {AUDIO_EXT}: {src}. "
                   f"Класть нечего.")
        return 0, out

    # Аддитивно: копируем только те, которых ещё нет (сверка по имени файла).
    added = skipped = 0
    for t in src_tracks:
        dst = bank / t.name
        if dst.exists():
            skipped += 1
            continue
        try:
            shutil.copy2(t, dst)
            added += 1
        except OSError as e:
            out.append(f"Не удалось скопировать {t.name}: {e}")
    total = len(_audio_files(bank))
    out.append(f"Банк музыки наполнен из «{origin}» ({src}).")
    out.append(f"Добавлено: {added}; уже было (пропущено): {skipped}; "
               f"всего в банке: {total}.")
    out.append("Повторный запуск безопасен — дубликатов не будет.")
    return 0, out


def status_text(root: Path) -> str:
    st = status(root)
    lines = ["=== ШАГ УСТАНОВКИ: МУЗЫКА (банк треков) ===", ""]
    if st["bank_present"] and st["bank_tracks"]:
        lines.append(f"Банк наполнен: {st['bank_tracks']} трек(ов) в «{BANK_DIRNAME}/».")
    else:
        lines.append(f"Банк пуст (папка «{BANK_DIRNAME}/» без аудио).")
    if st["source"]:
        where = "доступен" if st["source_exists"] else "НЕ найден"
        lines.append(f"Источник ({st['source_origin']}): {st['source']} — {where}"
                     + (f", треков: {st['source_tracks']}" if st["source_exists"] else ""))
    else:
        lines.append("Источник не задан. Музыка не входит в базовый архив — это ожидаемо.")
        lines.append(f"Добавить: положить треки в «{BANK_DIRNAME}/» или задать music_src.")
    lines.append("")
    lines.append("Из сети ничего не качается. Завод работает и без банка "
                 "(движок берёт музыку сам через Openverse).")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Отдельный шаг установки: банк музыки.")
    ap.add_argument("--root", help="Корень установки (по умолчанию — авто-поиск).")
    ap.add_argument("--src", help="Папка-источник с треками (переопределяет прочее).")
    ap.add_argument("--status", action="store_true", help="Русская сводка состояния банка.")
    ap.add_argument("--json", action="store_true", help="Машинная сводка (для мастера).")
    args = ap.parse_args(argv)

    root = find_root(args.root)

    if args.json:
        print(json.dumps(status(root), ensure_ascii=False, indent=2))
        return 0
    if args.status:
        print(status_text(root))
        return 0

    code, report = install(root, args.src)
    for line in report:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
