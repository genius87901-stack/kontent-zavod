# -*- coding: utf-8 -*-
"""Замер банка музыки: не попадёт ли короткий ролик в тихое вступление трека.

Стоковые треки часто начинаются с раскачки: первые 10-20 секунд тише остального.
Ролик у нас 12-14 секунд и берёт трек С НАЧАЛА — значит может целиком лечь на
вступление и прозвучать пусто. Глазами это не видно, на слух ловится не всегда,
поэтому меряем: средняя громкость первых N секунд против средней по всему треку.

    python music_check.py            # проверить весь банк
    python music_check.py --sec 14   # под другую длину ролика
"""
import argparse, os, re, subprocess

import imageio_ffmpeg

import music as mu

FF = imageio_ffmpeg.get_ffmpeg_exe()

ap = argparse.ArgumentParser()
ap.add_argument("--sec", type=float, default=14.0, help="длина ролика, сек")
ap.add_argument("--warn", type=float, default=3.0,
                help="на сколько дБ начало может быть тише, прежде чем ругаться")
a = ap.parse_args()


def mean_db(path, dur=None):
    """Средняя громкость куска в дБ (ffmpeg volumedetect)."""
    args = [FF, "-hide_banner"]
    if dur:
        args += ["-t", "%.2f" % dur]
    args += ["-i", path, "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    return float(m.group(1)) if m else None


rows, bad = [], []
for p in mu.bank_tracks():
    head = mean_db(p, a.sec)
    full = mean_db(p)
    if head is None or full is None:
        rows.append((os.path.basename(p), None, None, "не измерился"))
        continue
    d = head - full
    flag = "тихое вступление" if d < -a.warn else "ок"
    if d < -a.warn:
        bad.append(os.path.basename(p))
    rows.append((os.path.basename(p), head, full, flag))

w = max(len(r[0]) for r in rows)
print("%-*s  %8s  %8s  %s" % (w, "трек", "начало", "весь", "вывод"))
for name, head, full, flag in rows:
    if head is None:
        print("%-*s  %8s  %8s  %s" % (w, name, "-", "-", flag))
    else:
        print("%-*s  %7.1f дБ %7.1f дБ  %s" % (w, name, head, full, flag))

print()
if bad:
    print("ТИХОЕ ВСТУПЛЕНИЕ У %d из %d: ролик, взявший такой трек с начала, "
          "прозвучит пусто." % (len(bad), len(rows)))
    for b in bad:
        print("  -", b)
else:
    print("Все %d треков берут с начала ровно — вступлений-пустышек нет." % len(rows))
