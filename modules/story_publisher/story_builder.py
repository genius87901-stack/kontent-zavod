# -*- coding: utf-8 -*-
"""Движок сторис: собрать кадр 9:16 (1080×1920) из смыслов/материала.

Кадр = ФОН (фото-материал или синтетический тёмный градиент) + ХУК + короткий
ТЕКСТ + КОДОВОЕ СЛОВО/CTA. Текст и грейд не рисуем сами — ПЕРЕИСПОЛЬЗУЕМ движок
слайдов проекта engine_photo как ВЫЗОВ (его функцию cta: хук + плашка кодового
слова + подпись поверх приведённого к единому тону кадра). engine_photo и
engine4 НЕ редактируются — только вызываются.

Если движок слайдов недоступен в этой установке (например, вынут из архива),
включается автономный запасной рендер на голом PIL — чтобы сборка сторис не
падала, а всегда отдавала валидный PNG 1080×1920.

Ничего не публикует. Возвращает путь к готовому PNG и его размеры.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .config import install_root
from .paths import module_folder, OUTPUT

# Каноничный размер сторис.
STORY_W, STORY_H = 1080, 1920

# Акцент по умолчанию: на тёмном фото lime читается (контраст 14+), см.
# engine_photo и DESIGN_RULES. Здесь только имя — значение берёт engine4.ACCENTS.
DEFAULT_ACCENT = "lime"


# ── доступ к движку слайдов проекта (engine_photo / engine4) ────────────────

def _engine_roots():
    """Каталоги-кандидаты, где может лежать engine_photo.py. Корень установки и
    два уровня вверх (на случай иной раскладки короба). Без абсолютных путей."""
    root = install_root()
    seen, out = set(), []
    for cand in (root, *list(root.parents)[:2]):
        s = str(cand)
        if s not in seen:
            seen.add(s)
            out.append(cand)
    return out


def load_engine():
    """Подгрузить engine_photo + engine4 как ВЫЗЫВАЕМЫЙ движок или вернуть None.

    Ничего в этих модулях не меняем — только импортируем. Импорт тяжёлый
    (numpy/cv2/PIL/mediapipe) и делается лениво, по требованию сборки."""
    for cand in _engine_roots():
        if (cand / "engine_photo.py").exists() and (cand / "engine4.py").exists():
            s = str(cand)
            if s not in sys.path:
                sys.path.insert(0, s)
            try:
                import engine4 as e          # noqa: WPS433
                import engine_photo as ph     # noqa: WPS433
            except Exception:                 # движок есть, но не поднялся
                return None
            return e, ph
    return None


# ── фон ─────────────────────────────────────────────────────────────────────

def _synth_background(out_path, seed_accent=None):
    """Синтетический тёмный тёплый фон 1080×1920, когда своего фото-материала нет.

    Вертикальный градиент + мягкая виньетка. Дальше его всё равно приводит к
    единому тону grade() движка, так что достаточно нейтральной тёмной основы."""
    import numpy as np
    from PIL import Image

    h, w = STORY_H, STORY_W
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    xx = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]

    # тёплая тёмная база, чуть светлее к центру по вертикали
    base = 0.06 + 0.10 * (1.0 - np.abs(yy - 0.42) * 1.6).clip(0, 1)
    r = base * 1.15
    g = base * 1.02
    b = base * 0.85
    vign = (1.0 - 0.35 * (xx ** 2)).clip(0, 1)
    rgb = np.stack([r * vign, g * vign, b * vign], axis=-1)
    arr = (np.clip(rgb, 0, 1) * 255).astype("uint8")
    img = Image.fromarray(np.broadcast_to(arr, (h, w, 3)).copy())
    img.save(out_path, quality=92)
    return out_path


def _prepare_background(background, scratch_dir):
    """Вернуть путь к фон-картинке: либо переданный материал, либо синтетический.
    scratch_dir — куда положить синтетический фон (создаётся при нужде)."""
    if background:
        p = Path(background)
        if not p.is_absolute():
            p = install_root() / p
        if p.exists() and p.is_file():
            return p, False
    scratch_dir.mkdir(parents=True, exist_ok=True)
    synth = scratch_dir / "_story_bg.jpg"
    _synth_background(synth)
    return synth, True


# ── запасной рендер (без движка проекта) ────────────────────────────────────

def _fallback_render(hook, text, code, accent_name, bg_path, out_path):
    """Автономный рендер на голом PIL: фон + хук + текст + плашка кодового слова.
    Включается, только если engine_photo недоступен — чтобы сборка не падала."""
    from PIL import Image, ImageDraw, ImageFont

    ACC = {
        "lime": (204, 255, 0), "mint": (94, 214, 184), "orange": (240, 138, 74),
        "red": (228, 16, 43), "sky": (108, 168, 255), "terracotta": (201, 113, 78),
    }.get(accent_name, (204, 255, 0))

    img = Image.open(bg_path).convert("RGB").resize((STORY_W, STORY_H))
    # затемняем, чтобы белый текст читался
    img = Image.blend(img, Image.new("RGB", img.size, (10, 8, 7)), 0.45)
    d = ImageDraw.Draw(img)

    def _font(size):
        for cand in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                     "/System/Library/Fonts/Helvetica.ttc"):
            try:
                return ImageFont.truetype(cand, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _wrap(txt, fnt, maxw):
        words, line, out = txt.split(), "", []
        for wd in words:
            t = (line + " " + wd).strip()
            if d.textlength(t, font=fnt) <= maxw:
                line = t
            else:
                if line:
                    out.append(line)
                line = wd
        if line:
            out.append(line)
        return out

    M = 80
    maxw = STORY_W - 2 * M
    y = int(STORY_H * 0.30)
    d.rounded_rectangle([M, y, M + 96, y + 10], 5, fill=ACC)
    y += 50
    fh = _font(92)
    for ln in _wrap(hook, fh, maxw):
        d.text((M, y), ln, font=fh, fill=(255, 255, 255))
        y += 108
    y += 24
    fb = _font(46)
    for ln in _wrap(text, fb, maxw):
        d.text((M, y), ln, font=fb, fill=(224, 220, 214))
        y += 62
    if code:
        y += 40
        fc = _font(72)
        cw = d.textlength(code, font=fc)
        d.rounded_rectangle([M, y, M + cw + 88, y + 108], 18, fill=ACC)
        d.text((M + 44, y + 16), code, font=fc, fill=(20, 18, 16))
    img.save(out_path)
    return out_path


# ── публичная сборка ────────────────────────────────────────────────────────

def build_story(hook, text, code=None, accent=DEFAULT_ACCENT, background=None,
                out=None, index=1, total=1):
    """Собрать один кадр сторис 1080×1920 и сохранить его в PNG.

    hook       — крупный заголовок (хук).
    text       — короткая подпись под хуком (можно с разметкой *акцент* engine4).
    code       — кодовое слово/CTA (плашка акцентом). None/"" — без плашки.
    accent     — имя акцента (см. engine4.ACCENTS); по умолчанию lime.
    background — путь к фото-материалу; None → синтетический тёмный фон.
    out        — путь для PNG; None → output/stories/story.png в корне установки.
    index/total— счётчик кадра (для набора сторис); по умолчанию 1/1.

    Возвращает dict: path, width, height, engine ("engine_photo" | "fallback"),
    background ("material" | "synthetic"), code (bool).
    """
    if not hook:
        raise ValueError("build_story: пустой хук — сторис без заголовка не собираем")

    out_path = Path(out) if out else (module_folder(OUTPUT) / "story.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scratch = out_path.parent
    bg_path, synthetic = _prepare_background(background, scratch)

    loaded = load_engine()
    if loaded is not None:
        e, ph = loaded
        engine_used = _render_with_engine(e, ph, hook, text, code, accent,
                                          bg_path, out_path, index, total)
        if engine_used:
            return _result(out_path, "engine_photo", synthetic, code)

    # запасной путь — движок недоступен/не отработал
    _fallback_render(hook, text, code, accent, bg_path, out_path)
    return _result(out_path, "fallback", synthetic, code)


def _render_with_engine(e, ph, hook, text, code, accent, bg_path, out_path,
                        index, total):
    """Вызвать engine_photo.cta под сторис-формат 9:16. Ничего в движке не меняем:
    временно переводим его в формат 1920 (как это делает build_anton_stories),
    регистрируем фон-материал и возвращаем состояние обратно."""
    acc = e.ACCENTS.get(accent, e.ACCENTS.get(DEFAULT_ACCENT))
    key = "__story_bg__"

    saved_eH, saved_phH = e.H, ph.H
    saved_photo = e.PHOTOS.get(key, None)
    saved_counter = getattr(ph, "counter", None)
    try:
        e.H = STORY_H
        ph.H = STORY_H
        e.PHOTOS[key] = str(bg_path)
        # СЧЁТЧИК СЛАЙДОВ ГАСИМ. ph.cta рисует в углу «1/1» — это счётчик
        # КАРУСЕЛИ, и у неё он на месте: человек листает и видит, сколько
        # осталось. У сторис листать нечего, и «1/1» в углу читается как
        # недоделка (замер 28.08.2026: он выходил на каждом собранном кадре).
        # Движок не правим — по контракту его только зовут; на время вызова
        # подменяем одну функцию пустышкой и возвращаем обратно в finally,
        # тем же приёмом, что и высоту кадра выше.
        if saved_counter is not None:
            ph.counter = lambda *_a, **_kw: None
        # cta: хук(title) + кодовое слово(code, плашка) + короткий текст(sub)
        # поверх кадра, приведённого к единому тону. code="" → без плашки.
        ph.cta(hook, code or "", text, key, acc, str(out_path), index, total)
        return True
    except Exception:
        return False
    finally:
        e.H, ph.H = saved_eH, saved_phH
        if saved_counter is not None:
            ph.counter = saved_counter
        if saved_photo is None:
            e.PHOTOS.pop(key, None)
        else:
            e.PHOTOS[key] = saved_photo


def _result(out_path, engine, synthetic, code):
    from PIL import Image

    with Image.open(out_path) as im:
        w, h = im.size
    return {
        "path": str(out_path),
        "width": w,
        "height": h,
        "engine": engine,
        "background": "synthetic" if synthetic else "material",
        "code": bool(code),
    }


# ── готовый кадр → холст сторис ─────────────────────────────────────────────
# ЗАЧЕМ. Слайд карусели — 1080×1350 (4:5, `engine4.W/H`), сторис — 1080×1920
# (9:16). Это РАЗНЫЕ кадры, и выложить слайд историей «как есть» нельзя: площадка
# либо обрежет его по бокам-верху-низу (а в каруселях завода текст занимает почти
# весь кадр — срежется именно он), либо добавит свои поля неизвестного цвета.
# Поэтому слайд кладётся на холст 9:16 ЦЕЛИКОМ, а пустоту сверху и снизу
# закрывает размытая увеличенная копия самого слайда — приём стандартный, цвет
# всегда попадает в кадр, и ничего дорисовывать не нужно.
#
# Движки НЕ трогаем: engine4/engine_photo сюда не импортируются вовсе, работаем
# с уже готовым PNG на голом PIL.

#: Насколько тёмный слой поверх фона-заливки. Фон обязан отойти на второй план,
#: иначе размытая копия спорит с самим слайдом за внимание.
_BG_DARKEN = 0.55

#: Радиус размытия фона в долях ширины кадра. Замер на слайдах завода: 0.04
#: (≈43 px при 1080) убирает читаемость текста в фоне, но оставляет цвет.
_BG_BLUR = 0.04

#: Допуск соотношения, при котором кадр считается уже сторисным и просто
#: приводится к 1080×1920 без подложки.
_RATIO_EPS = 0.01


def story_from_frame(src, out=None, darken=_BG_DARKEN):
    """Переложить ГОТОВЫЙ кадр (слайд карусели, обложку, любой PNG/JPG) на холст
    сторис 1080×1920, ничего не обрезая.

    src    — путь к исходному кадру.
    out    — куда сохранить PNG; None → рядом с исходником, имя + `_story`.
    darken — насколько притушить фон-подложку (0 — не тушить, 1 — в чёрное).

    Кадр, у которого соотношение уже 9:16, просто масштабируется до 1080×1920.
    В остальных случаях: подложка = тот же кадр, растянутый ПО ЗАПОЛНЕНИЮ холста,
    размытый и притушенный; поверх — исходный кадр целиком, вписанный по ширине
    и отцентрованный.

    Возвращает dict: path, width, height, mode ("fit" | "scaled"), source.
    """
    from PIL import Image, ImageFilter

    src_path = Path(src)
    if not src_path.is_absolute():
        src_path = install_root() / src_path
    if not src_path.exists():
        raise ValueError("story_from_frame: кадра нет: %s" % src_path)

    out_path = Path(out) if out else src_path.with_name(src_path.stem + "_story.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src_path) as im:
        frame = im.convert("RGB")

        # Уже сторисное соотношение — подложка не нужна.
        if abs(frame.width / frame.height - STORY_W / STORY_H) <= _RATIO_EPS:
            frame.resize((STORY_W, STORY_H), Image.LANCZOS).save(out_path)
            return {"path": str(out_path), "width": STORY_W, "height": STORY_H,
                    "mode": "scaled", "source": str(src_path)}

        # Подложка: тот же кадр ПО ЗАПОЛНЕНИЮ (cover), размытый и притушенный.
        scale = max(STORY_W / frame.width, STORY_H / frame.height)
        bw, bh = max(1, int(frame.width * scale)), max(1, int(frame.height * scale))
        bg = frame.resize((bw, bh), Image.LANCZOS).crop((
            (bw - STORY_W) // 2, (bh - STORY_H) // 2,
            (bw - STORY_W) // 2 + STORY_W, (bh - STORY_H) // 2 + STORY_H))
        bg = bg.filter(ImageFilter.GaussianBlur(radius=STORY_W * _BG_BLUR))
        # ПРИТУШИВАНИЕ — ПО ЯРКОСТИ, А НЕ ВСЛЕПУЮ. Глухое затемнение на 0.55
        # красило подложку светлого слайда в СЕРЫЙ, и поля читались грязной
        # полосой поверх белого кадра (поймано глазами на боевом слайде c10/02).
        # Тёмному фото притушивание нужно — оно отодвигает его на второй план;
        # светлому плоскому слайду оно только вредит: его поля должны просто
        # продолжать сам кадр. Поэтому сила падает с ростом яркости подложки и
        # на белом обращается в ноль.
        if darken:
            import statistics
            grey = bg.convert("L")
            mean = statistics.fmean(grey.getdata()) if grey.size[0] else 0.0
            force = darken * (1.0 - mean / 255.0)
            if force > 0.01:
                bg = Image.blend(bg, Image.new("RGB", bg.size, (10, 8, 7)), force)

        # Сам кадр: вписан по ширине, отцентрован. Если по высоте не влезает
        # (кадр длиннее 9:16) — вписываем по высоте, чтобы не обрезать.
        fit = min(STORY_W / frame.width, STORY_H / frame.height)
        fw, fh = max(1, int(frame.width * fit)), max(1, int(frame.height * fit))
        bg.paste(frame.resize((fw, fh), Image.LANCZOS),
                 ((STORY_W - fw) // 2, (STORY_H - fh) // 2))
        bg.save(out_path)

    return {"path": str(out_path), "width": STORY_W, "height": STORY_H,
            "mode": "fit", "source": str(src_path)}


def stories_from_frames(sources, out_dir=None):
    """Переложить НАБОР готовых кадров (слайды карусели) в набор сторис 9:16.

    Порядок сохраняется: слайды показываются историями подряд, и порядок — это
    и есть «карусель» в сторис (листаемой карусели в историях у площадок нет,
    серия кадров подряд — её штатный эквивалент).

    Возвращает список dict-ов story_from_frame, по одному на кадр.
    """
    srcs = [sources] if isinstance(sources, (str, Path)) else list(sources)
    if not srcs:
        raise ValueError("stories_from_frames: не передано ни одного кадра")
    folder = Path(out_dir) if out_dir else None
    if folder:
        folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, s in enumerate(srcs, 1):
        dst = (folder / ("story_%02d.png" % i)) if folder else None
        out.append(story_from_frame(s, dst))
    return out
