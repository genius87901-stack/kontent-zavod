# -*- coding: utf-8 -*-
"""
Контрольный лист роликов — аналог ALL_PHOTOS.jpg для видео.

Делает то же, что import_photos.py делает с фото: нумерует банк роликов, пишет
карту номеров (vidx.json) и собирает лист с превью, чтобы Валерия выбирала
номером, а не именем файла.

С каждого ролика берётся кадр из СЕРЕДИНЫ, а не с первой секунды: первый кадр
часто смазан или снят до того, как что-то началось.

Под каждым превью: номер, длительность, разрешение и пометки —
TRIM (длиннее 14 сек, придётся резать) и CROP (не 9:16, придётся кадрировать).

    python video_sheet.py
"""
import json, os, subprocess, glob, threading
import concurrent.futures as cf

from PIL import Image, ImageDraw
import cv2
import imageio_ffmpeg

import engine4 as e

HERE = os.path.dirname(os.path.abspath(__file__))
VDIR = os.path.join(HERE, "video")
SHEET = os.path.join(HERE, "ALL_VIDEO.jpg")
VIDX = os.path.join(HERE, "vidx.json")

#: video/ — junction прямо на Google Drive: probe()+grab_middle() читают
#: облако, не локальный диск. Без кэша каждый запуск заново перечитывал бы
#: ВЕСЬ банк (100+ роликов) ради 1-2 новых — 16.08.2026 это и вешало импорт
#: на час+. Кэш хранит уже посчитанное превью+метаданные по (mtime, size).
CACHE_DIR = os.path.join(HERE, ".cache_video_thumbs")
CACHE_META = os.path.join(CACHE_DIR, "meta.json")

#: Один зависший на Drive ролик не должен вешать весь проход (см.
#: feedback_restart_gdrive_on_hang) — жёсткий потолок на файл. Замерено
#: 16.08.2026: типичный внутренний таймаут cv2/ffmpeg на этом Диске — 30-45
#: сек, поэтому порог выше него (иначе он же ложно режет рабочие файлы).
FILE_TIMEOUT_SEC = 60

#: Каждый новый ролик — это ожидание сети (Drive), не CPU: несколько файлов
#: параллельно ждут одновременно вместо очереди друг за другом. Замерено:
#: серийно 24 ролика за 23 мин; при том же 1 файле = ~30-45с задержки сети
#: параллелизм даёт кратный выигрыш, а не деление на N (сеть — не диск).
MAX_WORKERS = 6

MAX_SEC = 14.0          # предел Reels по решению Валерии (12-14 сек)
COLS = 8
THUMB_W, THUMB_H = 260, 462     # 9:16 превью
PAD, LABEL_H = 14, 58


def probe(path):
    """Длительность/размер через cv2 (ffprobe в комплекте imageio-ffmpeg нет)."""
    c = cv2.VideoCapture(path)
    fps = c.get(cv2.CAP_PROP_FPS) or 0
    n = c.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    w = int(c.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))
    c.release()
    return (n / fps if fps else 0.0), w, h


def grab_middle(path, dur):
    """Кадр из середины ролика -> PIL.Image."""
    c = cv2.VideoCapture(path)
    c.set(cv2.CAP_PROP_POS_MSEC, max(0.0, dur / 2.0) * 1000.0)
    ok, frame = c.read()
    if not ok:                       # перемотка не удалась - берём первый кадр
        c.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = c.read()
    c.release()
    if not ok:
        return None
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _cache_key(path):
    st = os.stat(path)
    return "%d|%d" % (int(st.st_mtime), st.st_size)


def _load_cache():
    if not os.path.exists(CACHE_META):
        return {}
    try:
        with open(CACHE_META, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_META, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1)


def _probe_and_grab(path):
    dur, w, h = probe(path)
    img = grab_middle(path, dur)
    return dur, w, h, img


def fit_thumb(img):
    """Вписать кадр в превью 9:16 по центру, не растягивая (letterbox)."""
    canvas = Image.new("RGB", (THUMB_W, THUMB_H), (24, 24, 26))
    r = min(THUMB_W / img.width, THUMB_H / img.height)
    im = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))),
                    Image.LANCZOS)
    canvas.paste(im, ((THUMB_W - im.width) // 2, (THUMB_H - im.height) // 2))
    return canvas


def main():
    files = [f for f in sorted(glob.glob(os.path.join(VDIR, "*")))
             if os.path.splitext(f)[1].lower() in (".mov", ".mp4")]
    if not files:
        raise SystemExit("В video\\ нет роликов")

    cache = _load_cache()
    results = {}          # i -> (name, thumb, dur, w, h)
    to_fetch = []          # (i, f, name, key) — не в кэше, нужен Drive

    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        key = _cache_key(f)
        entry = cache.get(name)
        thumb_path = os.path.join(CACHE_DIR, name + ".jpg")
        if entry and entry.get("key") == key and os.path.exists(thumb_path):
            thumb = Image.open(thumb_path).convert("RGB")
            results[i] = (name, thumb, entry["dur"], entry["w"], entry["h"])
        else:
            to_fetch.append((i, f, name, key))

    print(f"  в кэше: {len(results)}  |  забрать с Drive: {len(to_fetch)}"
          + (f" (параллельно по {MAX_WORKERS})" if to_fetch else ""))

    if to_fetch:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_lock = threading.Lock()
        with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            fut_map = {pool.submit(_probe_and_grab, f): (i, f, name, key)
                       for i, f, name, key in to_fetch}
            for fut in cf.as_completed(fut_map):
                i, f, name, key = fut_map[fut]
                try:
                    dur, w, h, img = fut.result(timeout=FILE_TIMEOUT_SEC)
                except cf.TimeoutError:
                    print(f"  ПРОПУСК (не отвечает Drive >{FILE_TIMEOUT_SEC}s): {name}")
                    continue
                except Exception as ex:
                    print(f"  ПРОПУСК (ошибка чтения): {name} — {ex}")
                    continue
                if img is None:
                    print("  ПРОПУСК (кадр не читается):", name)
                    continue
                thumb = fit_thumb(img)
                thumb.save(os.path.join(CACHE_DIR, name + ".jpg"), quality=90)
                with cache_lock:
                    cache[name] = {"key": key, "dur": dur, "w": w, "h": h}
                    # Сохранять СРАЗУ после каждого файла, не в конце цикла:
                    # каждый файл здесь может стоить 30-45 сек Drive-ожидания,
                    # и при схеме «запись один раз в конце» обрыв/убийство
                    # процесса ронял бы ВСЮ уже оплаченную медленную работу —
                    # тот же класс бага, что был у idx.json в import_photos.py.
                    _save_cache(cache)
                results[i] = (name, thumb, dur, w, h)
                print(f"  {i:2d}  {name:22s} {dur:5.1f}s  {w}x{h}  (новое)")

    idx, cards = {}, []
    for i in sorted(results):
        name, thumb, dur, w, h = results[i]
        idx[str(i)] = name
        cards.append((i, thumb, dur, w, h))

    _save_cache(cache)
    cache_hits = len(files) - len(to_fetch)
    fresh_ok = len(results) - cache_hits
    print(f"\n  из {len(files)} роликов: {fresh_ok}/{len(to_fetch)} забрано с Drive успешно, "
          f"{cache_hits} взято из кэша, {len(files) - len(results)} пропущено")

    with open(VIDX, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1)

    rows = (len(cards) + COLS - 1) // COLS
    W = COLS * THUMB_W + (COLS + 1) * PAD
    H = rows * (THUMB_H + LABEL_H) + (rows + 1) * PAD
    sheet = Image.new("RGB", (W, H), (245, 244, 242))
    d = ImageDraw.Draw(sheet)

    f_num = e.font("Black", 30)
    f_meta = e.font("SemiBold", 17)

    for k, (num, thumb, dur, w, h) in enumerate(cards):
        cx, cy = k % COLS, k // COLS
        x = PAD + cx * (THUMB_W + PAD)
        y = PAD + cy * (THUMB_H + LABEL_H + PAD)
        sheet.paste(thumb, (x, y))

        # номер — плашкой в углу превью, чтобы читался на любом кадре
        d.rectangle([x, y, x + 62, y + 42], fill=e.ACCENTS["lime"])
        d.text((x + 12, y + 6), str(num), font=f_num, fill=e.INK)

        ty = y + THUMB_H + 6
        d.text((x + 2, ty), f"{dur:.1f} сек", font=f_meta, fill=e.INK)

        marks = []
        if dur > MAX_SEC:
            marks.append("TRIM")
        if abs((h / w if w else 0) - 16 / 9) > 0.02:
            marks.append("CROP")
        d.text((x + 2, ty + 20), f"{w}x{h}" + ("  " + " ".join(marks) if marks else ""),
               font=f_meta, fill=(190, 60, 40) if marks else (120, 118, 115))

    sheet.save(SHEET, quality=92)
    print("\nOK ->", SHEET, f"({len(cards)} роликов)")
    print("OK ->", VIDX)


if __name__ == "__main__":
    main()
