# -*- coding: utf-8 -*-
"""Забрать материалы клиента с Google Диска В ЗАВОД (BOX-53, патч 1.3.8).

ЗАЧЕМ. До этой команды между Диском и заводом не было ничего. Структуру папок
«КонтентЗавод» мы создавали (`drive_layout`), карту «русская папка → каталог
движка» объявляли (`drive_layout.pipeline_map`) — а кода, который бы этой картой
воспользовался, не существовало: `pipeline_map` не вызывалась ниоткуда.
Клиент клал съёмку в «1 Сырьё», доктор отвечал «приём материалов готов», папки
завода при этом оставались пустыми. Первая же клиентка на этом встала и спросила
дословно: «а зачем мы туда на Google Диск всё скидывали?».

ЧТО ДЕЛАЕТ.
    1. читает файлы из «1 Сырьё» (ВКЛЮЧАЯ саму папку, не только подпапки)
       или из любой указанной папки — `--from`;
    2. определяет тип по расширению (набор берётся из контракта, поле `accepts`);
    3. копирует в рабочие каталоги завода: photos/ (через банк), talking/,
       video/, music/;
    4. печатает по-русски, что взял, и молчит про то, чего не трогал.

ЧЕГО НЕ ДЕЛАЕТ — НАМЕРЕННО.
    * НЕ переносит и НЕ переименовывает файлы клиента на Диске. Читаем и
      копируем к себе. Клиент, открывший Диск с телефона, должен увидеть ровно
      то, что положил, — иначе первый же вопрос будет «куда делось моё видео».
      Поэтому раскладка живёт ВНУТРИ завода, а не в чужой папке.
    * НЕ требует от клиента раскладывать съёмку по подпапкам. Разложил —
      уважаем его раскладку; свалил в кучу — разбираем сами.
    * НЕ ходит в сеть. Работает с уже синхронизированной папкой Диска.

ВИДЕО — ЕДИНСТВЕННОЕ, ЧТО ПО РАСШИРЕНИЮ НЕ РАЗЛИЧАЕТСЯ. `.mp4` бывает и
говорящей головой (→ Reels), и кадром для вставки. Правило простое:
    * лежит в «Говорящие видео» → talking/;
    * лежит в «Видео для вставок» → video/;
    * лежит где угодно ещё → talking/, потому что на старте у клиента почти
      всегда именно говорящая съёмка, а вставки появляются позже.
Решение печатается в отчёте вслух, и его можно перебить: `--video-as вставки`.

ЖУРНАЛ ПРИНЯТОГО — `inbox/pulled.json`, sha1 исходников. Нужен потому, что
дедупликация в `import_photos.py` от повторного захода НЕ спасает: там sha1
ИСХОДНИКА сверяется с sha1 файла В БАНКЕ, а банк лежит перекодированным
(`Image.save(quality=95)`), и байты не совпадают. Замер 11.08.2026 на живом
банке: own_100/101/102 после перекодирования дают другой sha1 — то есть без
своего журнала каждый повторный `pull` заводил бы те же кадры заново.

Коды выхода: 0 — взял (или брать нечего), 1 — не с чего начать (не указана
папка Диска, нет папки-источника), 2 — взял не всё (часть не удалась).

Запуск:
    PYTHONPATH=modules PY -m intake.pull
    PYTHONPATH=modules PY -m intake.pull --from "<любая папка на Диске>"
    PYTHONPATH=modules PY -m intake.pull --dry-run
    PYTHONPATH=modules PY -m intake.pull --video-as вставки
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

LEDGER_REL = Path("inbox") / "pulled.json"

# Служебное, что лежит в папках Диска и материалом не является.
SKIP_NAMES = {
    "ЧТО СЮДА КЛАСТЬ.txt",
    "КАК ЭТО РАБОТАЕТ.txt",
    "desktop.ini",
    "Thumbs.db",
    ".keep",
}

# Русские слова для --video-as: клиент и оператор пишут по-русски.
VIDEO_AS = {
    "говорящие": "talking",
    "говорящее": "talking",
    "talking": "talking",
    "вставки": "video",
    "вставок": "video",
    "video": "video",
}

# Как называть добычу в отчёте.
KIND_TITLE = {
    "photos": ("фото", "фото", "фото"),
    "talking": ("говорящее видео", "говорящих видео", "говорящих видео"),
    "video": ("видео для вставок", "видео для вставок", "видео для вставок"),
    "music": ("трек", "трека", "треков"),
    "longform": ("длинное видео", "длинных видео", "длинных видео"),
}


def _import_layout():
    """drive_layout лежит рядом; поддерживаем и -m intake.pull, и прямой запуск."""
    try:
        from intake import drive_layout
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from intake import drive_layout
    return drive_layout


def _import_doctor():
    """Корень установки и чтение конфига уже написаны в doctor — не дублируем.

    Оба файла лежат в одном модуле и всегда едут клиенту вместе, так что
    импорт безопасен: коробки, где есть pull.py и нет doctor.py, не бывает.
    """
    try:
        from intake import doctor
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from intake import doctor
    return doctor


# --------------------------------------------------------------------------
# типы файлов — из контракта, а не из головы
# --------------------------------------------------------------------------
def ext_sets(contract):
    """{'photos': {...}, 'music': {...}, 'notes': {...}, 'video': {...}} — расширения.

    Берём поле `accepts` из контракта: там уже перечислено, что куда кладут.
    Видео объединяем из двух папок — по расширению они не различаются.
    """
    by_id = {f["id"]: set(f.get("accepts") or []) for f in contract["folders"]}
    return {
        "photos": by_id.get("raw.photos", set()),
        "music": by_id.get("raw.music", set()),
        "notes": by_id.get("raw.notes", set()) - by_id.get("raw.music", set()),
        # Все четыре видео-папки контракта по расширению не различаются —
        # различает их только то, В КАКОЙ папке лежит файл (см. route).
        "video": (by_id.get("raw.talking", set()) | by_id.get("raw.video", set())
                  | by_id.get("raw.longform", set()) | by_id.get("raw.cuts", set())),
    }


def kind_of(path, sets):
    """Тип файла по расширению: photos / music / notes / video / None."""
    ext = Path(path).suffix.lower()
    for kind in ("photos", "video", "music", "notes"):
        if ext in sets[kind]:
            return kind
    return None


def route(path, kind, folders, video_default):
    """Куда положить: имя каталога завода или None (не забираем).

    `folders` — {id папки контракта: абсолютный путь}. Подпапку клиента
    уважаем: она сильнее умолчания, но слабее расширения (видео, брошенное
    в «Фото», всё равно останется видео).
    """
    if kind == "notes":
        return None            # тексты читаются прямо с Диска, копировать нечего
    if kind != "video":
        return kind
    p = Path(path).resolve()
    # «Нарезка из эфиров» — это куски, которые завод сам нарезал из длинного
    # видео; для конвейера они обычные говорящие ролики, поэтому идут в talking.
    for fid, dest in (("raw.talking", "talking"), ("raw.video", "video"),
                      ("raw.longform", "longform"), ("raw.cuts", "talking")):
        base = folders.get(fid)
        if base and _inside(p, Path(base)):
            return dest
    return video_default


def in_video_subfolder(path, folders):
    """Клиент сам положил ролик в «Говорящие видео» или «Видео для вставок»?

    Если да — раскладку решил он, и в отчёте о ней говорить не надо. Если нет —
    решение приняли мы по умолчанию, и это надо сказать вслух.
    """
    p = Path(path).resolve()
    return any(folders.get(fid) and _inside(p, Path(folders[fid]))
               for fid in ("raw.talking", "raw.video", "raw.longform",
                           "raw.cuts"))


def _inside(path, base):
    """Лежит ли `path` внутри `base`. ОБА пути разрешаем полностью.

    Иначе ломается на маке: временные папки живут в `/var/...`, а это симлинк
    на `/private/var/...`. Разрешённый путь файла с неразрешённым путём папки
    не совпадал никогда — и раскладка клиента по подпапкам молча игнорировалась.
    """
    try:
        Path(path).resolve().relative_to(Path(base).resolve())
        return True
    except (ValueError, OSError):
        return False


# --------------------------------------------------------------------------
# журнал принятого
# --------------------------------------------------------------------------
def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def longform_key(path):
    """Опознавательный ключ ДЛИННОГО видео — без чтения файла целиком.

    Для остального сырья ключ журнала — sha1 содержимого, и это правильно:
    файлы мелкие, а имя ненадёжно. Но запись эфира — это гигабайты, и sha1
    считался бы ПРИ КАЖДОМ заходе по всем длинным видео сразу (журнал
    проверяется по хешу, значит хеш нужен всегда). На двух подкастах по 3 ГБ
    это минуты чтения диска на ровном месте.

    Берём имя + размер + время изменения: для файла, который клиент один раз
    положил на Диск и больше не трогает, этого достаточно, а перезалитую
    версию отличает и размер, и время."""
    st = Path(path).stat()
    return "longform:%s:%d:%d" % (Path(path).name, st.st_size, int(st.st_mtime))


def load_ledger(root):
    p = Path(root) / LEDGER_REL
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}          # нет журнала — значит ещё ничего не забирали


def save_ledger(root, ledger):
    p = Path(root) / LEDGER_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n",
                 encoding="utf-8")


# --------------------------------------------------------------------------
# обход источника
# --------------------------------------------------------------------------
def excluded_dirs(contract, factory_root):
    """Папки «КонтентЗавода», которые сырьём НЕ являются.

    Нужны на случай `--from` прямо на «КонтентЗавод»: черновики из «2 На
    утверждение» и служебное из «НЕ_ТРОГАТЬ» затягивать обратно в завод нельзя.
    """
    out = []
    for f in contract["folders"]:
        if f["id"] == "raw" or f["id"].startswith("raw."):
            continue
        out.append(Path(factory_root).joinpath(*f["path"].split("/")))
    return out


def scan(src, sets, excluded=()):
    """[(путь, тип)] по всему дереву источника. Служебное и скрытое — мимо."""
    found = []
    src = Path(src)
    excluded = [Path(e).resolve() for e in excluded]
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        if p.name in SKIP_NAMES or p.name.startswith(".") or p.suffix == ".tmp":
            continue
        if "__pycache__" in p.parts:
            continue
        rp = p.resolve()
        if any(_inside(rp, e) for e in excluded):
            continue
        found.append((p, kind_of(p, sets)))
    return found


# --------------------------------------------------------------------------
# перенос
# --------------------------------------------------------------------------
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


def _import_photos(root, staging):
    """Завести подготовленные кадры в банк — существующим import_photos.py.

    Отдельной функцией, чтобы (а) не дублировать нумерацию и сборку
    контрольного листа, (б) тест мог подменить вызов и не тащить PIL.
    Возвращает (rc, вывод).
    """
    script = Path(root) / "import_photos.py"
    if not script.exists():
        return 1, "в коробке нет import_photos.py — банк фото не заведён"
    try:
        r = subprocess.run(
            [sys.executable, str(script), "--src", str(staging)],
            cwd=str(root), capture_output=True, text=True,
        )
    except OSError as exc:
        return 1, str(exc)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def pull(root, src, contract, folders, video_default="talking", dry_run=False):
    """Забрать всё из `src` в завод. Возвращает отчёт-словарь."""
    sets = ext_sets(contract)
    ledger = load_ledger(root)
    report = {
        "root": str(root), "src": str(src), "dry_run": bool(dry_run),
        "taken": {}, "skipped_known": 0, "notes_left": 0, "unknown": [],
        "photo_numbers": [], "video_by_default": 0, "errors": [],
    }

    to_move = []          # (путь, каталог назначения) — всё, кроме фото
    to_bank = []          # фото: сначала в подготовительную папку, потом в банк
    for path, kind in scan(src, sets, excluded_dirs(contract, folders.get("_factory", src))):
        if kind is None:
            report["unknown"].append(path.name)
            continue
        dest = route(path, kind, folders, video_default)
        if dest is None:
            report["notes_left"] += 1
            continue
        try:
            h = longform_key(path) if dest == "longform" else sha1(path)
        except OSError as exc:
            report["errors"].append("не смог прочитать «%s»: %s" % (path.name, exc))
            continue
        if h in ledger:
            report["skipped_known"] += 1
            continue
        if kind == "video" and not in_video_subfolder(path, folders):
            # решение приняли мы, а не клиент — но только по НОВЫМ файлам:
            # иначе фраза «взял как говорящее» звучала бы и тогда, когда в
            # этом заходе вообще ничего не взято (всё уже было в журнале).
            report["video_by_default"] += 1
        (to_bank if dest == "photos" else to_move).append((path, dest, h))

    # 1) видео и музыка — простое копирование в каталоги движка
    for path, dest, h in to_move:
        dest_dir = Path(root) / dest
        report["taken"].setdefault(dest, []).append(path.name)
        if dry_run:
            continue
        if dest == "longform":
            # ДЛИННОЕ ВИДЕО НЕ КОПИРУЕМ. Запись эфира весит гигабайты, а нужна
            # ровно один раз — чтобы нарезать из неё куски (talk_longcut). Вторая
            # копия на диске клиента не даёт ничего, кроме занятого места, поэтому
            # в журнал кладём ПУТЬ, а работаем по файлу на Диске.
            ledger[h] = {"name": path.name, "dest": dest, "path": str(path),
                         "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
            continue
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(path), str(_unique(dest_dir, path.name)))
        except OSError as exc:
            report["errors"].append("не смог положить «%s»: %s" % (path.name, exc))
            report["taken"][dest].remove(path.name)
            continue
        ledger[h] = {"name": path.name, "dest": dest,
                     "at": datetime.now().strftime("%Y-%m-%d %H:%M")}

    # 2) фото — через банк: нумерация own_N, idx.json и контрольный лист
    if to_bank:
        report["taken"]["photos"] = [p.name for p, _, _ in to_bank]
        if not dry_run:
            # Коробка везёт photos/ пустой папкой-приёмником (build_box.py,
            # .keep), но полагаться на этот файл нельзя: import_photos.py сам
            # каталог не создаёт и падает FileNotFoundError на первом же кадре
            # первого клиента (найдено боем 11.08.2026). Дешевле подстраховаться
            # здесь, чем чинить условие в чужом файле — он не наш в этом патче.
            (Path(root) / "photos").mkdir(parents=True, exist_ok=True)
            staging = tempfile.mkdtemp(prefix="zavod_photo_")
            try:
                for path, _, _ in to_bank:
                    # расширение приводим к нижнему регистру: import_photos ищет
                    # «*.jpg», а на маке glob регистрозависим — снимок IMG_1.JPG
                    # с зеркалки иначе просто не нашёлся бы.
                    safe = _unique(Path(staging), path.stem + path.suffix.lower())
                    shutil.copy2(str(path), str(safe))
                rc, out = _import_photos(root, staging)
                if rc == 0:
                    for line in out.splitlines():
                        head = line.strip().split(None, 1)[0] if line.strip() else ""
                        if head.isdigit() and "<-" in line:
                            report["photo_numbers"].append(int(head))
                    for path, _, h in to_bank:
                        ledger[h] = {"name": path.name, "dest": "photos",
                                     "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
                else:
                    tail = (out.strip().splitlines() or ["без объяснения"])[-1]
                    report["errors"].append("банк фото не принял кадры: %s" % tail)
                    report["taken"].pop("photos", None)
            except OSError as exc:
                report["errors"].append("подготовка фото не удалась: %s" % exc)
                report["taken"].pop("photos", None)
            finally:
                shutil.rmtree(staging, ignore_errors=True)

    if not dry_run and (to_move or to_bank):
        try:
            save_ledger(root, ledger)
        except OSError as exc:
            report["errors"].append(
                "не смог записать журнал принятого: %s. Материал взят, но при "
                "следующем заходе он приедет ещё раз." % exc)

    return report


# --------------------------------------------------------------------------
# отчёт человеку
# --------------------------------------------------------------------------
def _plural(n, one, few, many):
    tail = abs(n) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def render(report, video_default):
    """Человеческий отчёт. Ни одного технического слова без нужды."""
    lines = []
    if report["dry_run"]:
        lines.append("Пробный запуск — ничего не копировалось.")

    total = sum(len(v) for v in report["taken"].values())
    if total == 0:
        lines.append("Нового материала не нашлось.")
        if report["skipped_known"]:
            word = _plural(report["skipped_known"], "файл", "файла", "файлов")
            lines.append("Всё, что лежит в папке (%d %s), я уже забирал раньше."
                         % (report["skipped_known"], word))
        else:
            lines.append("Положите съёмку в «1 Сырьё» на Google Диске и позовите "
                         "меня снова — раскладывать по подпапкам не нужно.")
    else:
        parts = []
        for kind in ("photos", "talking", "video", "music", "longform"):
            n = len(report["taken"].get(kind, []))
            if n:
                parts.append("%d %s" % (n, _plural(n, *KIND_TITLE[kind])))
        lines.append("Взял в работу: " + ", ".join(parts) + ".")
        n_long = len(report["taken"].get("longform", []))
        if n_long:
            lines.append("  длинные видео оставил на Диске — копировать эфир в "
                         "завод незачем. Скажите «нарежь рилсы из моего эфира», "
                         "и я вытащу из него куски.")
        nums = report["photo_numbers"]
        if nums:
            lines.append("  фото заведены в банк под номерами %d–%d — их уже можно "
                         "ставить в карусели." % (min(nums), max(nums)))
        if report["taken"].get("talking"):
            lines.append("  говорящее видео лежит в папке talking — из него собираются Reels.")
        if report["taken"].get("video"):
            lines.append("  видео для вставок лежит в папке video.")
        if report["taken"].get("music"):
            lines.append("  музыка лежит в папке music.")
        if report["skipped_known"]:
            word = _plural(report["skipped_known"], "файл", "файла", "файлов")
            lines.append("Пропустил %d %s — их я забрал в прошлый раз."
                         % (report["skipped_known"], word))

    if report.get("video_by_default"):
        if video_default == "talking":
            lines.append("Видео, которое лежало не в подпапке, я взял как ГОВОРЯЩЕЕ. "
                         "Если это кадры для вставок — скажите, перезаберу: "
                         "--video-as вставки")
        else:
            lines.append("Видео, которое лежало не в подпапке, я взял как КАДРЫ ДЛЯ "
                         "ВСТАВОК.")

    if report["notes_left"]:
        word = _plural(report["notes_left"], "файл", "файла", "файлов")
        lines.append("Тексты и заметки (%d %s) оставил на Диске — их я читаю прямо "
                     "оттуда, копировать не нужно." % (report["notes_left"], word))

    if report["unknown"]:
        shown = ", ".join(report["unknown"][:3])
        tail = " и ещё %d" % (len(report["unknown"]) - 3) if len(report["unknown"]) > 3 else ""
        lines.append("Не понял, что это за файлы, и не тронул: %s%s." % (shown, tail))

    lines.append("Ваши файлы на Google Диске остались на своих местах — я их только копирую.")
    for err in report["errors"]:
        lines.append("  ОШИБКА: %s" % err)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def resolve_source(root, contract, layout, explicit):
    """(папка-источник, карта папок, ошибка-текстом|None)."""
    doctor = _import_doctor()
    cfg, cfg_err = doctor.read_config(Path(root))
    if cfg_err:
        return None, {}, cfg_err

    gdrive = (cfg.get("gdrive_root") or "").strip()
    folders = {}
    if gdrive:
        base = doctor.resolve_gdrive(Path(root), gdrive)
        if base.exists():
            folders = {k: str(v) for k, v in layout.folder_paths(base, contract).items()}
            folders["_factory"] = str(layout.factory_root(base, contract))

    if explicit:
        src = Path(explicit).expanduser()
        if not src.exists():
            return None, folders, ("папки «%s» на этом компьютере нет — проверьте "
                                   "путь или дождитесь синхронизации Google Диска" % src)
        return src, folders, None

    if not gdrive:
        return None, folders, (
            "не указана папка Google Диска, а без неё я не знаю, откуда брать "
            "материалы. Либо укажите её при настройке завода, либо покажите папку "
            "прямо сейчас: --from \"<путь к папке со съёмкой>\"")
    raw = folders.get("raw")
    if not raw or not Path(raw).exists():
        # BOX-72: не просим человека выполнять команду — доводим структуру сами.
        # ensure_structure идемпотентен: только создаёт, ничего не удаляет.
        base = doctor.resolve_gdrive(Path(root), gdrive)
        if base.exists():
            try:
                layout.ensure_structure(base)
            except OSError:
                pass
            folders = {k: str(v) for k, v in layout.folder_paths(base, contract).items()}
            folders["_factory"] = str(layout.factory_root(base, contract))
            raw = folders.get("raw")
    if not raw or not Path(raw).exists():
        return None, folders, (
            "на Диске нет папки «1 Сырьё». Её создаёт установка: "
            "python -m intake.drive_layout --root \"<папка Google Диска>\"")
    return Path(raw), folders, None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Забрать материалы клиента с Google Диска в завод.")
    ap.add_argument("--from", dest="src", default=None,
                    help="папка со съёмкой (по умолчанию «1 Сырьё» на Диске)")
    ap.add_argument("--video-as", default="говорящие",
                    help="как считать видео вне подпапок: говорящие | вставки")
    ap.add_argument("--dry-run", action="store_true",
                    help="показать, что заберу, ничего не копируя")
    ap.add_argument("--json", action="store_true", help="машинный вывод отчёта")
    args = ap.parse_args(argv)

    video_default = VIDEO_AS.get(str(args.video_as).strip().lower())
    if video_default is None:
        print("Не понял «--video-as %s». Допустимо: говорящие, вставки." % args.video_as)
        return 1

    layout = _import_layout()
    contract = layout.load_contract()
    doctor = _import_doctor()
    root = doctor.find_root(Path(__file__).resolve())

    src, folders, err = resolve_source(root, contract, layout, args.src)
    if err:
        print("Не могу забрать материалы.")
        print("Причина: %s" % err)
        return 1

    report = pull(root, src, contract, folders,
                  video_default=video_default, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report, video_default))
    return 2 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
