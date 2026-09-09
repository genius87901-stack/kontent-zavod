# -*- coding: utf-8 -*-
"""
ЛИСТ МУЗЫКИ — как ALL_PHOTOS.jpg, только слушать.

Зачем: выбирать трек по названию нельзя, «Upbeat Corporate» ничего не говорит
об ощущении. Скрипт берёт треки по нескольким настроениям, режет из каждого
15 секунд И НЕ С НАЧАЛА (у музыки начало обычно пустое — вступление), и
складывает в music_preview пронумерованными файлами. Валерия слушает и
называет номер, дальше номер идёт в build_*.py.

    python music_preview.py
    python music_preview.py --queries chill lounge minimal --per 3

Карта номеров — music_preview/picks.json: по номеру достаётся точный трек
(id, запрос, позиция в выдаче), чтобы сборка взяла ровно то, что выбрали.
"""
import argparse
import json
import os
import subprocess

import imageio_ffmpeg

import music as mu

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "music_preview")
PICKS = os.path.join(OUT, "picks.json")
FF = imageio_ffmpeg.get_ffmpeg_exe()

# Настроения под нишу: деловое, но не пресное. Каждое слово — ОДНО:
# условия в Openverse идут по И, "cinematic corporate" даёт ноль.
QUERIES = ["corporate", "inspiring", "chill", "lounge", "minimal",
           "cinematic", "electronic", "ambient"]

PREVIEW_SEC = 15
SKIP_SEC = 25          # откуда резать: вступление у треков обычно пустое


def cut(src, dst, start=SKIP_SEC, dur=PREVIEW_SEC):
    subprocess.run([FF, "-y", "-loglevel", "error", "-ss", "%.1f" % start,
                    "-i", src, "-t", "%.1f" % dur,
                    "-af", "afade=t=in:st=0:d=0.4,afade=t=out:st=%.1f:d=0.6"
                    % (dur - 0.6),
                    "-c:a", "libmp3lame", "-b:a", "160k", dst],
                   capture_output=True)


def main():
    ap = argparse.ArgumentParser(description="Нарезать превью музыки для выбора")
    ap.add_argument("--queries", nargs="*", default=QUERIES)
    ap.add_argument("--per", type=int, default=2, help="треков на настроение")
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    picks, n, seen = {}, 0, set()

    for q in a.queries:
        try:
            tracks = mu.search(q, min_sec=45)      # короче 45 сек резать неоткуда
        except Exception as ex:
            print("  %-12s ОШИБКА: %s" % (q, ex))
            continue
        taken = 0
        for i, t in enumerate(tracks):
            if taken >= a.per:
                break
            if t["id"] in seen:
                continue
            seen.add(t["id"])
            n += 1
            try:
                src = mu.download(t)
            except Exception as ex:
                print("  не скачался %r: %s" % (t["title"], ex))
                n -= 1
                continue
            name = "%02d_%s.mp3" % (n, q)
            cut(src, os.path.join(OUT, name))
            picks[str(n)] = dict(query=q, pick=i, id=t["id"],
                                 title=t["title"], author=t["author"],
                                 license="%s %s" % ((t["license"] or "").upper(),
                                                    t["license_version"] or ""),
                                 sec=t["sec"], attribution=mu.attribution(t))
            taken += 1
            print("  %2d  %-12s %-42s %4.0f сек  %s"
                  % (n, q, (t["title"] or "")[:42], t["sec"], t["author"]))

    with open(PICKS, "w", encoding="utf-8") as f:
        json.dump(picks, f, ensure_ascii=False, indent=1)

    print("\nOK -> %s (%d превью по %d сек)" % (OUT, n, PREVIEW_SEC))
    print("OK ->", PICKS)


if __name__ == "__main__":
    main()
