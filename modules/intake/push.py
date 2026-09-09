# -*- coding: utf-8 -*-
"""Положить готовое ИЗ ЗАВОДА обратно на Google Диск клиента.

ЗАЧЕМ. `pull` возит материал с Диска в завод, а обратной дороги не было вовсе:
контракт объявляет папки с `owner: factory` («2 На утверждение», «Нарезка из
эфиров»), но кода, который бы туда что-то клал, в проекте не существовало —
`drive_layout` звали только доктор, мастер установки и `pull`. Клиент видел
пустые папки и делал вывод, что завод в них не работает.

Первый потребитель — нарезка длинных видео (`talk_longcut.py`): куски эфира
должны лечь на Диск, чтобы человек посмотрел их с телефона и сказал, из какого
собирать рилс.

ЧЕГО НЕ ДЕЛАЕТ — НАМЕРЕННО.
    * НЕ удаляет и НЕ перезаписывает чужое. Если файл с таким именем уже лежит,
      кладём рядом «имя_2.mp4» — как это делает `pull`. Диск клиента — не наша
      рабочая папка, затирать там нельзя ничего.
    * НЕ ходит в сеть. Пишем в папку, которую синхронизирует сам Google Диск.
    * НЕ трогает то, что клиент положил сам.
    * НЕ публикует. Файл на Диске видит только клиент.

ЖУРНАЛ ОТДАННОГО — `inbox/pushed.json`: что и когда уехало. Нужен, чтобы
повторный запуск не разложил вторую копию тех же кусков.

Коды выхода: 0 — положил (или класть нечего), 1 — не с чего начать (не указана
папка Диска, нечего класть), 2 — положил не всё.

Запуск:
    PYTHONPATH=modules PY -m intake.push --files out_longcut/эфир/*.mp4 --to cuts
    PYTHONPATH=modules PY -m intake.push --dir out_longcut/эфир --to cuts \\
        --subdir "Эфир про выгорание"
    PYTHONPATH=modules PY -m intake.push --dir ... --to cuts --dry-run
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

LEDGER_REL = Path("inbox") / "pushed.json"

#: Куда разрешено класть. Только папки контракта с owner=factory: в клиентские
#: («1 Сырьё/Фото» и прочие) завод писать не должен — там хозяин человек.
#: Ключ — короткое имя для --to, значение — id папки в контракте.
TARGETS = {
    "cuts": "raw.cuts",          # 1 Сырьё/Нарезка из эфиров
    "review": "review",          # 2 На утверждение
    "ready": "ready",            # 3 Готово к публикации
}


def _import_layout():
    """drive_layout лежит рядом; поддерживаем и -m intake.push, и прямой запуск."""
    try:
        from intake import drive_layout
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from intake import drive_layout
    return drive_layout


def _import_doctor():
    """Корень установки и чтение конфига уже написаны в doctor — не дублируем."""
    try:
        from intake import doctor
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from intake import doctor
    return doctor


# --------------------------------------------------------------------------
# журнал отданного
# --------------------------------------------------------------------------
def load_ledger(root):
    p = Path(root) / LEDGER_REL
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ledger(root, ledger):
    p = Path(root) / LEDGER_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n",
                 encoding="utf-8")


def key_of(path, target, subdir):
    """Ключ журнала: куда именно этот файл уже уезжал.

    Содержимое не хешируем: файл только что собрал сам завод, он лежит рядом,
    и вопрос тут другой — «эту штуку уже относили на Диск?». Отвечают на него
    путь назначения и размер."""
    p = Path(path)
    return "%s/%s/%s:%d" % (target, subdir or "", p.name, p.stat().st_size)


# --------------------------------------------------------------------------
# перенос
# --------------------------------------------------------------------------
def _same_file(a, b):
    """Тот же самый файл — по имени и размеру? (содержимое не читаем: гигабайты)"""
    try:
        return Path(a).stat().st_size == Path(b).stat().st_size
    except OSError:
        return False


def _unique(dest_dir, name):
    """Имя, которое не затрёт уже лежащий файл: «ролик.mp4» → «ролик_2.mp4»."""
    target = dest_dir / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while (dest_dir / f"{stem}_{n}{suffix}").exists():
        n += 1
    return dest_dir / f"{stem}_{n}{suffix}"


def target_dir(gdrive_root, target, subdir=None, contract=None):
    """Абсолютный путь папки назначения на Диске (папку не создаёт)."""
    layout = _import_layout()
    contract = contract or layout.load_contract()
    if target not in TARGETS:
        raise ValueError("некуда класть: «%s» (можно: %s)"
                         % (target, ", ".join(sorted(TARGETS))))
    folders = layout.folder_paths(gdrive_root, contract)
    base = folders.get(TARGETS[target])
    if not base:
        raise ValueError("в контракте нет папки «%s»" % TARGETS[target])
    return Path(base) / subdir if subdir else Path(base)


def push(root, files, gdrive_root, target="cuts", subdir=None, contract=None,
         dry_run=False):
    """Положить `files` в папку Диска. Возвращает отчёт-словарь."""
    report = {"target": target, "subdir": subdir or "", "dest": "",
              "put": [], "skipped_known": 0, "errors": []}
    dest = target_dir(gdrive_root, target, subdir, contract)
    report["dest"] = str(dest)
    ledger = load_ledger(root)
    changed = False

    for f in files:
        p = Path(f)
        if not p.is_file():
            report["errors"].append("нет файла «%s»" % p.name)
            continue
        try:
            k = key_of(p, target, subdir)
        except OSError as exc:
            report["errors"].append("не смог прочитать «%s»: %s" % (p.name, exc))
            continue
        if k in ledger:
            report["skipped_known"] += 1
            continue
        if _same_file(p, dest / p.name):
            # ТАКОЙ ЖЕ файл уже лежит на месте, а журнала нет — значит журнал
            # потеряли (переустановка, чистка, другая машина), а работа доехала.
            # Класть рядом «имя_2» в этом случае нельзя: клиент откроет папку и
            # увидит два одинаковых ролика, и это хуже, чем ничего не сделать.
            # Живой случай 28.08.2026: сброшенный журнал развёл 16 кусков в 32.
            report["skipped_known"] += 1
            ledger[k] = {"name": p.name, "target": target,
                         "subdir": subdir or "",
                         "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
            changed = True
            continue
        report["put"].append(p.name)
        if dry_run:
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(p), str(_unique(dest, p.name)))
        except OSError as exc:
            report["errors"].append("не смог положить «%s»: %s" % (p.name, exc))
            report["put"].remove(p.name)
            continue
        ledger[k] = {"name": p.name, "target": target, "subdir": subdir or "",
                     "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
        changed = True

    if changed:
        try:
            save_ledger(root, ledger)
        except OSError as exc:
            report["errors"].append(
                "не смог записать журнал отданного: %s. Файлы на Диске, но при "
                "следующем заходе поедут ещё раз." % exc)
    return report


# --------------------------------------------------------------------------
# отчёт человеку
# --------------------------------------------------------------------------
def render(report):
    lines = []
    n = len(report["put"])
    if n:
        lines.append("Положил на Google Диск: %d файл(ов)." % n)
        lines.append("  папка: %s" % report["dest"])
        lines.append("  Откройте её с телефона — файлы появятся, как только "
                     "Google Диск закончит синхронизацию.")
    else:
        lines.append("Нового класть нечего.")
    if report["skipped_known"]:
        lines.append("  %d файл(ов) уже относил раньше — повторно не кладу."
                     % report["skipped_known"])
    for e in report["errors"]:
        lines.append("  ! %s" % e)
    return "\n".join(lines)


VIDEO_EXT = {".mp4", ".mov", ".m4v"}


def collect_dir(folder, say=None):
    """Что из рабочей папки завода можно отдать клиенту: ПАРЫ «видео + карточка».

    Правило узкое намеренно. Рабочая папка нарезки — это не витрина: рядом с
    готовыми кусками лежат контактные листы, пробные рендеры, транскрипты и
    прочая кухня. Первый же прогон 28.08.2026 увёз клиенту на Диск
    `ПРОБНЫЙ_РИЛС_04_SHEET.jpg` — просто потому, что он лежал в той же папке и
    был картинкой.

    Пара «`01_тема.mp4` + `01_тема.txt`» — это и есть договор `talk_longcut`:
    он кладёт карточку рядом с КАЖДЫМ куском и больше ни с чем. Всё, что в пару
    не сложилось, остаётся дома, и о каждом пропуске говорится вслух — молчаливый
    отсев читается как «увёз всё»."""
    folder = Path(folder)
    files = [p for p in sorted(folder.iterdir()) if p.is_file()]
    cards = {p.stem for p in files if p.suffix.lower() == ".txt"}
    out, skipped = [], []
    for p in files:
        if p.suffix.lower() in VIDEO_EXT and p.stem in cards:
            out.append(p)
            out.append(folder / (p.stem + ".txt"))
        elif p.suffix.lower() in VIDEO_EXT:
            skipped.append("%s — нет карточки рядом" % p.name)
        elif p.suffix.lower() != ".txt":
            skipped.append("%s — рабочий файл завода" % p.name)
    if say and skipped:
        say(skipped)
    return out


def _collect(args):
    """Список файлов из --files и --dir."""
    out = [Path(f) for f in (args.files or [])]
    if args.dir:
        def _say(skipped):
            print("Оставляю дома (%d):" % len(skipped))
            for s in skipped:
                print("  · %s" % s)
        out += collect_dir(args.dir, say=_say)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Положить готовое из завода на Google Диск клиента")
    ap.add_argument("--files", nargs="+", help="конкретные файлы")
    ap.add_argument("--dir", help="папка — возьму из неё видео, картинки и .txt")
    ap.add_argument("--to", default="cuts", choices=sorted(TARGETS),
                    help="в какую папку Диска класть (по умолчанию нарезку)")
    ap.add_argument("--subdir", default=None,
                    help="подпапка внутри неё (например имя эфира)")
    ap.add_argument("--root", default=None, help="корень установки завода")
    ap.add_argument("--gdrive", default=None,
                    help="папка Google Диска (по умолчанию из настроек завода)")
    ap.add_argument("--dry-run", action="store_true",
                    help="показать, что положил бы, ничего не копируя")
    args = ap.parse_args(argv)

    doctor = _import_doctor()
    root = (Path(args.root) if args.root
            else doctor.find_root(Path(__file__).resolve()))
    if args.gdrive:
        gdrive = Path(args.gdrive).expanduser()
    else:
        cfg, cfg_err = doctor.read_config(Path(root))
        if cfg_err:
            print("Не с чего начать: %s" % cfg_err)
            return 1
        value = (cfg.get("gdrive_root") or "").strip()
        if not value:
            print("Не с чего начать: не указана папка Google Диска.\n"
                  "  Задайте её в настройках завода или передайте --gdrive.")
            return 1
        gdrive = doctor.resolve_gdrive(Path(root), value)
    if not Path(gdrive).exists():
        print("Не с чего начать: папки «%s» на этом компьютере нет — проверьте "
              "путь или дождитесь синхронизации Google Диска." % gdrive)
        return 1

    files = _collect(args)
    if not files:
        print("Не с чего начать: не сказано, что класть (--files или --dir).")
        return 1

    report = push(root, files, gdrive, target=args.to, subdir=args.subdir,
                  dry_run=args.dry_run)
    if args.dry_run:
        print("Пробный запуск — ничего не копировалось.")
    print(render(report))
    return 2 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
