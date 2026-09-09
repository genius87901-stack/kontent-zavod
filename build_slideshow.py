# -*- coding: utf-8 -*-
"""
СЛАЙД-ШОУ ИЗ ГОТОВОЙ КАРУСЕЛИ — под ВК и Telegram.

ЗАЧЕМ. Свайп-карусель есть ТОЛЬКО в Instagram. Telegram раскладывает несколько
картинок альбомом, ВК — сеткой: все слайды видны сразу мелкими превью. Замерено
глазами 22.07 на карусели #8 — слайды 5, 6 и 7 ужались в нижнюю полоску, CTA с
плашкой РОСТ оказался размером с ноготь, а обложка перестала быть обложкой.

РЕШЕНИЕ ВАЛЕРИИ 22.07: на ВК и Telegram гоним те же слайды ВИДЕО — по очереди,
в полный размер. Один пост, один файл. Instagram и Threads не трогаем, там
настоящая карусель работает как надо.

ДЛИТЕЛЬНОСТЬ РАЗНАЯ, И ЭТО НЕ ПРИХОТЬ. Обложке нужно время на хук, CTA — на
решение написать. Внутренние слайды короче: там одна мысль. Значения ниже
подобраны по скорости чтения вслух (~15 знаков в секунду) с запасом.

ЗВУКА НЕТ намеренно: карусели идут без музыки (решение Валерии 19.07), а
беззвучное видео в ленте ВК и Telegram — норма.

РЕЖИМ --vertical (9:16) — ДЛЯ ВК. Замерено 22.07: ВК кладёт вертикальное видео
в Клипы, и это единственный путь карусели в ВК, потому что фото на стену нашим
токеном закрыто наглухо (`photos.getWallUploadServer` отбивает всегда, а
`video.save` работает). Слайд 4:5 в Клипах обрежется по краям — поедет CTA.
Поэтому кадр 1080x1350 вписываем в 1080x1920 на подложке, а не растягиваем.
Цвет подложки берём из угла самого слайда: у белого набора она белая, у
тёмного — тёмная, поля не выглядят чужими. Это дешевле размытия и не спорит с
правилом «один цвет — одна координата».

    python build_slideshow.py out_c8
    python build_slideshow.py out_c8 --vertical          # 1080x1920, под ВК
    python build_slideshow.py out_c8 --sec 3.0 --cover 4.5 --cta 6.0
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import imageio_ffmpeg
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "out_slides")

# Холст Клипов ВК. Слайд карусели — 1080x1350, разница уходит в поля.
VERT_W, VERT_H = 1080, 1920

# Секунды на слайд. Обложка и CTA дольше — см. шапку.
COVER_SEC = 4.0
BODY_SEC = 3.5
CTA_SEC = 5.5
FPS = 30


def slides(d):
    """Все png папки по возрастанию имени: 01.png — обложка, последний — CTA."""
    files = sorted(f for f in os.listdir(d) if f.lower().endswith(".png"))
    if not files:
        sys.exit(f"В {d} нет png.")
    return [os.path.join(d, f) for f in files]


def to_vertical(files, tmpdir):
    """Слайд 4:5 -> кадр 9:16 на подложке цвета самого слайда.

    Цвет берём из угла (2,2): у белого набора там белый, у тёмного — тёмный
    край фотографии. Растягивать кадр нельзя — поедут пропорции лица на
    обложке; обрезать тоже нельзя — уедет CTA. Значит поля.
    """
    out = []
    for i, path in enumerate(files):
        im = Image.open(path).convert("RGB")
        if im.width != VERT_W:
            h = round(im.height * VERT_W / im.width)
            im = im.resize((VERT_W, h), Image.LANCZOS)
        canvas = Image.new("RGB", (VERT_W, VERT_H), im.getpixel((2, 2)))
        canvas.paste(im, (0, (VERT_H - im.height) // 2))
        dst = os.path.join(tmpdir, "%02d.png" % i)
        canvas.save(dst)
        out.append(dst)
    return out


def durations(n, cover, body, cta):
    if n == 1:
        return [cover]
    return [cover] + [body] * (n - 2) + [cta]


def build(src, out, cover, body, cta, vertical=False):
    files = slides(src)
    secs = durations(len(files), cover, body, cta)

    tmpdir = None
    if vertical:
        tmpdir = tempfile.mkdtemp(prefix="slides9x16_")
        files = to_vertical(files, tmpdir)

    # concat demuxer: последний файл повторяется без duration — иначе ffmpeg
    # обрезает хвост и CTA мелькает. Это задокументированное поведение, не баг.
    lst = os.path.join(OUTDIR, "_concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for path, sec in zip(files, secs):
            f.write(f"file '{path.replace(os.sep, '/')}'\n")
            f.write(f"duration {sec}\n")
        f.write(f"file '{files[-1].replace(os.sep, '/')}'\n")

    # ЗАМЕР 22.07: без -t готовый файл вышел 32.5 сек вместо 27 — повторённый
    # последний кадр висит лишние 5.5 сек. Повтор нужен (иначе CTA мелькает один
    # кадр), поэтому длину зажимаем явно суммой duration.
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ff, "-y", "-f", "concat", "-safe", "0", "-i", lst,
        "-t", f"{sum(secs):.3f}",
        "-vf", f"fps={FPS},format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-movflags", "+faststart",
        out,
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        sys.exit("ffmpeg упал:\n" + r.stderr.decode("utf-8", "replace")[-2000:])
    os.remove(lst)
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return files, secs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", help="папка карусели, напр. out_c8")
    ap.add_argument("--sec", type=float, default=BODY_SEC, help="секунд на внутренний слайд")
    ap.add_argument("--cover", type=float, default=COVER_SEC, help="секунд на обложку")
    ap.add_argument("--cta", type=float, default=CTA_SEC, help="секунд на последний слайд")
    ap.add_argument("--out", help="куда писать mp4")
    ap.add_argument("--vertical", action="store_true",
                    help="кадр 1080x1920 под Клипы ВК (по умолчанию 1080x1350)")
    a = ap.parse_args()

    src = a.dir if os.path.isabs(a.dir) else os.path.join(HERE, a.dir)
    if not os.path.isdir(src):
        sys.exit(f"Нет папки {src}")
    os.makedirs(OUTDIR, exist_ok=True)

    base = os.path.basename(src.rstrip("/\\"))
    suffix = "_slideshow_9x16" if a.vertical else "_slideshow"
    out = a.out or os.path.join(OUTDIR, f"{base}{suffix}.mp4")

    files, secs = build(src, out, a.cover, a.sec, a.cta, vertical=a.vertical)

    print(f"Формат  : {'1080x1920 (Клипы ВК)' if a.vertical else '1080x1350'}")
    print(f"Слайдов : {len(files)}")
    for f, s in zip(files, secs):
        print(f"   {os.path.basename(f)}  {s} сек")
    print(f"Длина   : {sum(secs):.1f} сек")
    print(f"Размер  : {os.path.getsize(out) / 1e6:.1f} МБ")
    print("OK ->", out)
    print("\nПОСМОТРИ ГЛАЗАМИ ПЕРЕД ОТПРАВКОЙ. Проверки тут не заменяют взгляд.")


if __name__ == "__main__":
    main()
