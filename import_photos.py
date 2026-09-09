# -*- coding: utf-8 -*-
"""
ЗАВОД НОВЫХ ФОТО В БАНК.

По правилу из ПРОЕКТ.md: «Новые фото — прислать один раз, дописываются в банк
новым номером и остаются навсегда». Раньше это делалось руками; операция
повторяется каждый раз, когда Валерия досылает кадры, поэтому — скриптом.

Что делает:
  1. берёт файлы из папки-источника (по умолчанию «фото для слайдов»);
  2. копирует их в photos/ под именем own_<номер>.jpg — номера продолжают
     существующие в idx.json, без пропусков и без перенумерации старых;
  3. дописывает idx.json;
  4. пересобирает ALL_PHOTOS.jpg — контрольный лист, который показывают
     при выборе кадра.

Ничего не удаляет и не переписывает: если файл с таким содержимым уже заведён
(сверка по sha1), он пропускается — повторный запуск не плодит дубли.

ЗАПУСК:
    python import_photos.py                      # из «фото для слайдов»
    python import_photos.py --src "новая папка"  # из другой
    python import_photos.py --dry-run            # только показать, что будет
"""
import argparse
import glob
import hashlib
import json
import os

from PIL import Image, ImageOps

#: HEIC с айфона: читаем + применяем ориентацию. Опенер дают ДВА разных пакета —
#: коробка вендорит `pi-heif` (installer/vendor), ученикам ставится `pillow-heif`
#: (ученикам/requirements.txt). Раньше пробовался только первый, и на машине со
#: вторым импорт молча падал в except: HEIC не открывался вовсе, а фото Валерии
#: приезжают именно в HEIC — партия просто не заводилась в банк.
_heif = None
for _mod in ("pi_heif", "pillow_heif"):
    try:
        _heif = __import__(_mod)
        _heif.register_heif_opener()
        break
    except Exception:
        _heif = None

import engine4 as e

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(HERE, "photos")
IDX = os.path.join(HERE, "idx.json")
SHEET = os.path.join(HERE, "ALL_PHOTOS.jpg")
SRC = os.path.join(HERE, "фото для слайдов")

#: photos/ — junction на Google Drive. Без кэша main() хешировал бы ВЕСЬ
#: банк (сотни файлов) заново на каждом запуске ради проверки дублей на
#: горстке новых — 16.08.2026 это было заметной долей общей медлительности
#: импорта. Ключ — (mtime, size) уже заведённого файла в BANK.
HASH_CACHE = os.path.join(HERE, ".state", "photo_hashes.json")

#: Клетки контрольного листа. Лежат ЛОКАЛЬНО (.state), а не в банке: банк —
#: junction в Google Drive, и читать его целиком ради каждого листа слишком
#: дорого (замер в `_cell`).
CELLS = os.path.join(HERE, ".state", "sheet_cells")


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_hash_cache():
    if not os.path.exists(HASH_CACHE):
        return {}
    try:
        with open(HASH_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_hash_cache(cache):
    os.makedirs(os.path.dirname(HASH_CACHE), exist_ok=True)
    with open(HASH_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def _cached_sha1(path, cache):
    """sha1 уже заведённого файла БАНКА — с кэшем по (mtime, size)."""
    st = os.stat(path)
    fkey = "%d|%d" % (int(st.st_mtime), st.st_size)
    name = os.path.basename(path)
    entry = cache.get(name)
    if entry and entry.get("key") == fkey:
        return entry["sha1"]
    h = sha1(path)
    cache[name] = {"key": fkey, "sha1": h}
    return h


def load_idx():
    """Банк фото. Нет idx.json — значит банк ещё пуст, а не поломка.

    На свежей коробке этого файла НЕТ ни у кого: он заводится первым же импортом.
    Раньше отсутствие файла роняло скрипт трассировкой FileNotFoundError на самой
    первой команде клиента про фото — ровно то, что CLAUDE.md запрещает
    показывать человеку.
    """
    if not os.path.exists(IDX):
        return {}
    with open(IDX, encoding="utf-8") as f:
        return json.load(f)


def _cell(path, name, cell):
    """Клетка контрольного листа — уменьшенный кадр, посчитанный ОДИН раз.

    ЗАМЕР 05.09.2026. `photos/` — junction в Google Drive, чтение оттуда идёт
    0,65 МБ/с, банк весит 877 МБ. Поэтому КАЖДАЯ пересборка листа перечитывала
    его целиком — ~22 минуты, в том числе в прогон, где не завелось ни одного
    кадра: 48 файлов оказались дублями, в банк не ушло ни байта, а лист всё
    равно собирался с нуля. Клетка заведённого кадра не меняется никогда.

    Ключ — имя + размер, БЕЗ mtime: Drive меняет время при пересинхронизации, и
    кэш по mtime обнулялся бы целиком на ровном месте (по этой же причине mtime
    не входит в ключ `media_watch.key_of`). Банк дописывается и не переписывается,
    так что имени с размером достаточно.
    """
    st = os.stat(path)
    cached = os.path.join(CELLS, "%s_%d.jpg" % (os.path.splitext(name)[0], st.st_size))
    if os.path.exists(cached):
        try:
            return Image.open(cached).convert("RGB")
        except Exception:
            # Битая клетка (кэш недописался) — не повод ронять лист, считаем заново.
            try:
                os.remove(cached)
            except OSError:
                pass
    im = Image.open(path)
    # draft: libjpeg отдаёт уменьшённый кадр прямо при распаковке (1/2, 1/4, 1/8).
    # Ради картинки 200x200 не нужно разворачивать 4-мегабайтный кадр целиком.
    try:
        im.draft("RGB", (cell, cell))
    except Exception:
        pass  # не-JPEG (png/webp) draft не умеет — просто читаем как раньше
    im = im.convert("RGB")
    im.thumbnail((cell, cell), Image.LANCZOS)
    try:
        os.makedirs(CELLS, exist_ok=True)
        im.save(cached, quality=88)
    except OSError:
        pass  # кэш — ускорение, а не условие работы
    return im


def build_all_sheet(idx, out=SHEET, cols=7, cell=200):
    """Лист всего банка с номерами — тот самый ALL_PHOTOS.jpg.

    Показывается при каждом выборе фото, поэтому номер должен читаться с ходу:
    он рисуется крупно, поверх кадра, на подложке — иначе на тёмном кадре
    номер тонет, а на светлом теряется.
    """
    nums = sorted(idx, key=int)
    rows = (len(nums) + cols - 1) // cols
    pad = 10
    sheet = Image.new("RGB", (cols * (cell + pad) + pad,
                              rows * (cell + pad) + pad), (238, 236, 233))
    from PIL import ImageDraw
    d = ImageDraw.Draw(sheet)
    fnt = e.font("Black", 30)

    for i, n in enumerate(nums):
        p = os.path.join(BANK, idx[n])
        if not os.path.exists(p):
            continue
        im = _cell(p, idx[n], cell)
        x = pad + (i % cols) * (cell + pad)
        y = pad + (i // cols) * (cell + pad)
        sheet.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
        d.rectangle([x, y, x + 46, y + 40], fill=(255, 255, 255))
        d.text((x + 6, y + 3), n, font=fnt, fill=e.INK)

    sheet.save(out, quality=90)
    return out, len(nums)


def main():
    ap = argparse.ArgumentParser(description="Завести новые фото в банк")
    ap.add_argument("--src", default=SRC, help="папка-источник")
    ap.add_argument("--dry-run", action="store_true", help="показать, но не делать")
    ap.add_argument("--sheet", action="store_true",
                    help="пересобрать ALL_PHOTOS.jpg, даже если новых кадров нет")
    a = ap.parse_args()

    idx = load_idx()
    hash_cache = _load_hash_cache()
    known = {}
    for k, v in idx.items():
        p = os.path.join(BANK, v)
        if not os.path.exists(p):
            continue
        try:
            known[_cached_sha1(p, hash_cache)] = k
        except OSError as ex:
            # Транзиент сразу после рестарта Drive (файл ещё досинхронизируется)
            # не должен ронять весь импорт — просто эта запись выпадает из
            # сверки на дубли в ЭТОМ запуске, следующий её подхватит.
            print(f"  (пропущен при сверке дублей, не читается сейчас: {v} — {ex})")
    _save_hash_cache(hash_cache)
    # Пустой банк: max() по пустому набору тоже падал трассировкой. Первый
    # заведённый кадр получает номер 1 — нумерация дальше идёт без пропусков.
    nxt = max(int(k) for k in idx) + 1 if idx else 1

    # Расширения перебираем в обоих регистрах: на macOS/Linux glob чувствителен
    # к регистру, и без верхнего пропали бы `.HEIC`/`.JPG` прямо с айфона. На
    # Windows файловая система регистр игнорирует, поэтому оба паттерна вернут
    # ОДНИ И ТЕ ЖЕ пути — отсюда dedupe по normcase: без него каждый айфоновский
    # кадр хешировался дважды, а отчёт врал («пропущено дублей: 27» на партии,
    # где дублей не было ни одного).
    # `.webp` добавлен 02.09.2026: партия «Valeria photo 2» пришла с шестью
    # такими файлами, и они молча не заводились — Pillow их читает, а список
    # расширений про них не знал.
    exts = ("jpg", "jpeg", "png", "heic", "webp")
    found = []
    for ext in exts:
        for pat in (ext, ext.upper()):
            found += glob.glob(os.path.join(a.src, "*." + pat))
    files = sorted({os.path.normcase(os.path.abspath(p)): p for p in found}.values())
    if not files:
        raise SystemExit(f"В «{a.src}» нет картинок.")

    added, skipped = [], 0
    for src_path in files:
        h = sha1(src_path)
        if h in known:
            skipped += 1
            continue
        name = f"own_{nxt}.jpg"
        if not a.dry_run:
            dst = os.path.join(BANK, name)
            # ВСЕГДА пере-кодируем в jpg с ПРИМЕНЁННОЙ EXIF-ориентацией:
            # айфон/зеркалка часто пишут кадр «боком» + флаг поворота, а движок
            # флаг не читает — без exif_transpose кадр лёг бы на бок (грабля 26.07).
            # ЦВЕТ. exif_transpose сохраняет info (в т.ч. icc_profile), а
            # to_srgb переводит кадр из Display P3 айфона в sRGB — иначе числа
            # остаются P3, профиль при save теряется, и в ленте кадр выцветает
            # (23.08.2026, см. engine4.to_srgb). Порядок важен: сначала поворот,
            # потом цвет — profileToProfile info уже не несёт.
            import engine4 as _e4
            im = _e4.to_srgb(ImageOps.exif_transpose(Image.open(src_path)))
            im.save(dst, quality=95)
            idx[str(nxt)] = name
        known[h] = str(nxt)
        added.append((nxt, os.path.basename(src_path)))
        nxt += 1

    for n, orig in added:
        print(f"  {n:3d}  <- {orig}")
    print(f"\nзаведено: {len(added)} | пропущено дублей: {skipped}")

    if a.dry_run:
        print("(--dry-run: ничего не записано)")
        return

    # Лист устарел, если его нет или он старше idx.json. Сверять ДО записи
    # idx.json, иначе своя же запись всегда делает лист «старым».
    stale = (not os.path.exists(SHEET) or not os.path.exists(IDX)
             or os.path.getmtime(SHEET) < os.path.getmtime(IDX))

    if added:
        with open(IDX, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=1)
    elif not stale and not a.sheet:
        # Дубли — обычное дело: сторож приносит одну и ту же партию, пока она
        # лежит в папке-источнике. Платить за это перечитыванием всего банка
        # незачем.
        print("Заводить нечего, лист уже свежий — пересборка пропущена "
              "(принудительно: --sheet).")
        return

    out, total = build_all_sheet(idx)
    print(f"idx.json обновлён, в банке теперь {total} кадров")
    print(f"контрольный лист пересобран: {out}")


if __name__ == "__main__":
    main()
