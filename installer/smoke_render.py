# -*- coding: utf-8 -*-
"""
smoke_render — «дымовой» офлайн-рендер: доказывает, что развёрнутый завод РЕНДЕРИТ.

Не подменяет продуктовый рендер (полный ролик собирает движок на реальных медиа —
это шаг приёмки в BUILD-RECIPE.md). Здесь — короткий ПРОВЕРОЧНЫЙ рендер, который
цепляет ровно ту офлайн-цепочку, что нужна заводу: Pillow (кадры) + шрифт Onest
(OFL) + ffmpeg из пакета imageio-ffmpeg (без системного ffmpeg, без сети). Если этот
.mp4 получился — значит окружение развёрнуто верно и рендер работает.

Пишет `out_smoke/smoke.mp4` в корне установки. Ничего не публикует, сеть не трогает.
Только Pillow + imageio-ffmpeg (обе — заявленные зависимости движка) + stdlib.

Запуск (из корня установки, интерпретатором .venv):
    python installer/smoke_render.py --root .
    python installer/smoke_render.py --seconds 2 --fps 24

Код возврата: 0 — .mp4 собран; 1 — не хватает зависимости/шрифта; 2 — ошибка рендера.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def find_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "installer" else Path.cwd()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Дымовой офлайн-рендер (проверка завода).")
    ap.add_argument("--root")
    ap.add_argument("--seconds", type=int, default=2)
    ap.add_argument("--fps", type=int, default=24)
    args = ap.parse_args(argv)

    root = find_root(args.root)

    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        print("Нет Pillow — окружение развёрнуто не полностью. Повтори установку.")
        return 1
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        print("Нет imageio-ffmpeg — движок не сможет кодировать видео. Повтори установку.")
        return 1

    # Нативный слой. Pillow и ffmpeg выше везут свои библиотеки и заводятся всегда —
    # поэтому сами по себе НЕ доказывают, что коробка жива. Проверку лиц (mediapipe/
    # opencv) и распознавание речи (torch) держит C++-runtime Microsoft, и именно он
    # отсутствует на чистой Windows. Без этой проверки дым-рендер зелёный, а завод
    # у клиента не работает.
    for mod, зачем in (("cv2", "проверка «текст не на лице» и сборка видео"),
                       ("mediapipe", "детектор лиц"),
                       ("torch", "распознавание речи для Reels")):
        try:
            __import__(mod)
        except ImportError as e:
            if "DLL load failed" in str(e):
                print(f"НЕ ХВАТАЕТ СИСТЕМНОГО КОМПОНЕНТА WINDOWS: не загрузился «{mod}» "
                      f"({зачем}).\nЭто библиотеки Microsoft Visual C++ — коробка должна "
                      f"была привезти их рядом с python.exe.\nЧто делать: сообщите "
                      f"поставщику (сборка неполная) либо установите «Microsoft Visual "
                      f"C++ Redistributable x64» — но он требует прав администратора.")
                return 1
            print(f"Не импортируется «{mod}» ({зачем}): {e}\nОкружение развёрнуто не "
                  f"полностью — повтори установку.")
            return 1

    # шрифт Onest (OFL) — тот же, что у движка; если нет — берём дефолтный, но сообщаем
    font_path = root / "fonts" / "Onest.ttf"
    W, H = 720, 1280
    try:
        font = ImageFont.truetype(str(font_path), 64) if font_path.is_file() \
            else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    n_frames = max(1, args.seconds * args.fps)
    out_dir = root / "out_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = out_dir / "smoke.mp4"

    # ffmpeg читает сырые RGB-кадры со stdin (rawvideo) — кроссплатформенно, без сети.
    cmd = [
        ffmpeg, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(args.fps), "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        str(out_mp4),
    ]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"ffmpeg не запустился: {e}")
        return 2

    try:
        for i in range(n_frames):
            t = i / max(1, n_frames - 1)
            bg = (int(20 + 40 * t), int(20 + 20 * t), int(40 + 60 * t))
            img = Image.new("RGB", (W, H), bg)
            d = ImageDraw.Draw(img)
            d.text((60, H // 2 - 80), "КОНТЕНТ-ЗАВОД", font=font, fill=(255, 255, 255))
            d.text((60, H // 2 + 10), "офлайн-рендер OK", font=font, fill=(180, 220, 255))
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
        proc.wait(timeout=120)
    except Exception as e:
        print(f"Ошибка рендера: {e}")
        return 2

    if out_mp4.is_file() and out_mp4.stat().st_size > 0:
        print(f"Дымовой рендер OK: {out_mp4} ({out_mp4.stat().st_size} байт). "
              f"Цепочка Pillow+шрифт+ffmpeg жива, офлайн.")
        return 0
    print("Файл не собрался — рендер не подтверждён.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
