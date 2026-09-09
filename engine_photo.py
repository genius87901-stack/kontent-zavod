# -*- coding: utf-8 -*-
"""
ТЁМНЫЙ ФОТО-СТИЛЬ — второй набор слайдов (утверждён после разбора референса).
Белый набор (engine_white) остаётся и никуда не девается: стиль выбирается под тему.

Что держит такую карусель вместе — не фото, а две вещи:

1. ЕДИНЫЙ ГРЕЙД (grade). Кадры приходят из разных мест: банк Валерии, сток.
   Тон у них разный, и без приведения к одному они рассыпаются. grade() сажает
   любой кадр в один тёплый тёмный тон: десатурация, тёплый баланс, затемнение,
   подъём чёрных в тёплое. Это и есть «один организм».

2. ТЕКСТ В СПОКОЙНОЙ ЗОНЕ (pick_zone). В референсе текст лежит в пустоте кадра,
   потому что кадры отбирали руками. На стоке так не будет: заголовок ляжет
   поперёк тарелки. pick_zone считает карту детализации (Sobel) и ищет самую
   спокойную полосу, обходя лица. Логика та же, что у checks.check_cover_face,
   только не «поймать после», а «положить правильно сразу».

АКЦЕНТ: на тёмном фото акцентный текст РАЗРЕШЁН — правило «акцент только заливкой»
(см. ПРОЕКТ.md, «ОДИН ЦВЕТ») касается белого фона, где контраст акцента 1.14–2.4.
На тёмном грейде тот же lime даёт 14+. Значение цвета при этом одно и то же.
"""
import os

import numpy as np
from PIL import Image, ImageDraw

import engine4 as e

W, H, M = e.W, e.H, e.M
font = e.font
WHITE = (255, 255, 255)
BODY = (206, 200, 194)      # мелкий текст на фото
MUTED = (150, 144, 138)

# ── ГРЕЙД: подобран по референсу (тёплый, тёмный, слегка приглушённый) ──
# Первый заход (GAIN 0.60) на светлом кадре давал серость вместо глубины: тон
# уводился, а темноты не появлялось. Референс заметно глубже — отсюда 0.44.
DESAT = 0.34                        # доля увода в серое
WARM = (1.08, 0.99, 0.85)           # тёплый баланс: плюс красного, минус синего
GAIN = 0.44                         # общее затемнение
LIFT = (0.045, 0.032, 0.022)        # подъём чёрных в тёплое, чтобы не было «дыры»

# Текстовый блок занимает эту долю площади кадра.
# Кегль под неё подбирается автоматически — см. _autofit.
# Было 0.50, стало 0.40 (решение Валерии по рендеру #6): на 50% короткий текст
# раздувался во весь кадр и слайды звучали вразнобой.
TEXT_AREA = 0.40
COL = 0.86                          # ширина колонки текста от ширины кадра

# ── КЕГЛЬ МЕЛКОГО ТЕКСТА (описаний под заголовком) ──
# Знобы, чтобы под сторис-формат (1080x1920) сделать описания читаемо крупнее,
# не трогая прошлые карусели: дефолты = прежнее поведение байт-в-байт.
# BODY_*   — тело в statement/cover (_autofit): bs = clamp(hs*RATIO, MIN, MAX)
# BULLET_* — пункты списка в bullets
# BIGNUM_BODY — описание под крупной цифрой в bignum
BODY_MIN, BODY_MAX, BODY_RATIO = 26, 42, 0.42
BULLET_MIN, BULLET_MAX, BULLET_RATIO = 24, 38, 0.52
# TAIL-02: потолок заголовка bullets (было 96 — раздувало короткие списки, см.
# комментарий у места использования в bullets()). Соседний BIGNUM_TITLE=58 —
# ниже указан для сверки, что оба слайда 4-5 не разъезжаются на глаз.
BULLET_TITLE_MAX = 74
BIGNUM_BODY = 32
# Кегли крупной цифры и её заголовка — вынесены, чтобы под сторис-формат делать
# крупнее, не трогая прошлые карусели: дефолты = прежнее поведение байт-в-байт.
BIGNUM_NUM = 330
BIGNUM_TITLE = 58

# У ОБЛОЖКИ доля меньше, и это геометрия, а не вкус. Внутренний слайд делит кадр
# только с предметами — там текст волен занять половину. Обложка делит его с ЛИЦОМ,
# и лицо перекрывать нельзя. Блок в 50% площади (928x822 при COL=0.86) на портрете
# с лицом по центру не помещается никуда: pick_zone перебирает все позиции и всё
# равно накрывает лицо (замер на фото 9: перекрытие 56%, checks роняли сборку).
# 0.30 — наибольшая доля, которая уходит под лицо целиком на портретах банка.
# Обложке этого хватает: там хук и подзаголовок, а не абзац.
COVER_AREA = 0.30


def grade(img, gain=None):
    """Любой кадр → один тёплый тёмный тон. Это то, что делает набор набором.

    `gain` необязателен: по умолчанию общий GAIN. Обложке иногда нужен кадр
    чуть светлее (чтобы лицо читалось ярче) — тогда gain задаётся выше GAIN.
    Внутренние слайды его не передают и остаются на едином грейде.
    """
    g = GAIN if gain is None else gain
    a = np.asarray(img.convert("RGB")).astype(np.float32) / 255.0
    lum = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    a = a * (1 - DESAT) + lum[..., None] * DESAT
    a = a * np.array(WARM, dtype=np.float32)
    a = a * g + np.array(LIFT, dtype=np.float32)
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))


def photo(photo_key, gain=None):
    """Кадр банка/стока, обрезанный под 1080x1350 и приведённый к грейду."""
    return grade(e.fit(e.PHOTOS[photo_key], H), gain=gain)


# ── СПОКОЙНАЯ ЗОНА ───────────────────────────────────────────────────────────
def detail_map(img):
    """Карта детализации: где в кадре «шумно», туда текст класть нельзя."""
    import cv2
    g = np.asarray(img.convert("L")).astype(np.float32)
    dx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    dy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(dx * dx + dy * dy)
    return cv2.blur(mag, (61, 61))


def pick_zone(img, bw, bh, faces=(), pad=40):
    """
    Куда положить текстовый блок bw x bh. Вертикально сканируем полосу,
    считаем среднюю детализацию под блоком и штрафуем наезд на лицо.
    Возвращаем (x, y) — левый верхний угол блока.
    """
    dm = detail_map(img)
    best, best_s = (M, H - bh - 96), None
    for x in (M, W - M - bw):
        for y in range(pad, H - bh - pad, 24):
            s = float(dm[y:y + bh, x:x + bw].mean())
            for (fx0, fy0, fx1, fy1) in faces:
                ox = max(0, min(x + bw, fx1) - max(x, fx0))
                oy = max(0, min(y + bh, fy1) - max(y, fy0))
                if ox > 0 and oy > 0:
                    s += 10000 * (ox * oy) / (bw * bh)   # лицо перекрывать нельзя
            if best_s is None or s < best_s:
                best, best_s = (x, y), s
    return best


FAR, NEAR = 55, 165     # альфа затемнения: дальний край кадра → сторона текста


def scrim(img, box):
    """
    Затемнение на ВЕСЬ кадр с плавным уклоном к текстовому блоку.
    Полосой под текстом (первый заход) на светлом фото был виден шов: полоса
    тёмная, остальное нет. В референсе затемнён весь кадр целиком — шва нет.
    """
    y0, y1 = box[1], box[3]
    cy = (y0 + y1) / 2
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    far = max(cy, H - cy)
    for y in range(H):
        t = 1 - min(1.0, abs(y - cy) / far)      # 1 у текста → 0 у дальнего края
        a = FAR + (NEAR - FAR) * (t ** 0.8)
        od.line([(0, y), (W, y)], fill=(8, 6, 5, int(a)))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def _lum(c):
    f = [(v / 255) / 12.92 if v / 255 <= 0.04045 else (((v / 255) + 0.055) / 1.055) ** 2.4
         for v in c]
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]


def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


BG_UNDER_TEXT = (30, 27, 23)     # замер: фон после grade+scrim под текстовым блоком


def body_accent(acc):
    """
    Каким цветом красить акцент в МЕЛКОМ тексте. Порог читаемости зависит от
    кегля: крупному хватает 3.0, мелкому нужно 4.5. На тёплом грейде orange (6.89)
    и terracotta (4.86) мелкий акцент тянут, а red (3.60) — нет: на рендере строчка
    красным сливается с фоном. Поэтому решает не правило в голове, а замер.
    """
    if contrast(acc, BG_UNDER_TEXT) >= 4.5:
        return acc
    return WHITE      # цвет не читается мелким — выделяем яркостью, не тоном


def head_accent(acc):
    """То же для КРУПНОГО текста — порог ниже (3.0), поэтому red здесь проходит
    (3.60) и цифра на bignum остаётся цветной, хотя мелким тем же red нельзя."""
    if contrast(acc, BG_UNDER_TEXT) >= 3.0:
        return acc
    return WHITE


def chip_text(acc):
    """Текст на плашке акцента. В белом наборе это всегда INK, но там плашка
    лежит на белом фоне и палитра другая: на red (228,16,43) INK даёт 3.1, а
    белый — 4.6. Правило «на заливке — INK» служит читаемости, поэтому здесь
    оно исполняется по духу: берём то, что реально читается на этом акценте."""
    return e.INK if contrast(e.INK, acc) >= contrast(WHITE, acc) else WHITE


def faces_on(photo_key):
    """Лица кадра — тем же детектором, что и проверка перед показом.

    Обложка и CTA обязаны обходить лицо. Раньше это ловилось постфактум
    (checks.check_cover_face роняла сборку), теперь рамки лиц уходят в
    pick_zone — и текст просто не ложится на лицо по построению. Проверка
    остаётся второй сетью: она сверяет результат, а не заменяет замысел.
    """
    import checks     # лениво: mediapipe тянется секунды, нужен не всем слайдам
    return checks.face_boxes(photo_key)


# Счётчик живёт в правом верхнем углу, а текстовая колонка (COL=0.86) достаёт до
# него по ширине. Пока заголовки были короткие, они до счётчика не дотягивались;
# на длинной первой строке («Хочешь покажу,» в CTA #6) строка полезла прямо под
# плашку. Поэтому блок текста на слайдах со счётчиком начинается ниже его полосы.
COUNTER_PAD = 112


def counter(d, i, n):
    f = font("Bold", 24)
    t = f"{i}/{n}"
    tw = d.textlength(t, font=f)
    x, y = W - M - tw, 50
    d.rounded_rectangle([x - 18, y - 10, x + tw + 18, y + 34], 22, fill=(0, 0, 0, 150))
    d.text((x, y), t, font=f, fill=WHITE)


# ── СЛАЙД: заголовок + тело, уложенные в спокойную зону ──────────────────────
def _autofit(d, title, body, bw, target_h):
    """
    Подобрать кегль так, чтобы блок занял заданную высоту (то есть TEXT_AREA
    площади кадра). Фиксированный кегль здесь не годится: длина текста от слайда
    к слайду разная, а доля площади должна оставаться одной — иначе крупный текст
    получается только там, где мало слов.
    """
    # Высота прыгает скачками: кегль вырос — строка перенеслась, блок сразу выше.
    # Поэтому не «первый, что влезает» (давал 42% вместо 50%), а ближайший к цели.
    # Небольшой перелёт разрешён: 50% — это ориентир, а не край кадра.
    best = None
    for hs in range(190, 47, -2):    # потолок высокий: под 50% площади заголовку
                                     # на коротком тексте нужно ~140, не 120
        fh = font("Black", hs)
        bs = max(BODY_MIN, min(BODY_MAX, int(hs * BODY_RATIO)))
        fb, fbold = font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d, title, fh, bw)
        bl = e.wrap_rich(d, body, fb, bw, fbold)
        lh, blh = int(hs * 1.18), int(bs * 1.45)
        gap = int(hs * 0.42)
        h = len(hl) * lh + gap + len(bl) * blh
        if h > target_h * 1.10 or h > H - 2 * 40:
            continue
        c = dict(fh=fh, fb=fb, hl=hl, bl=bl, lh=lh, blh=blh, gap=gap, h=h)
        if best is None or abs(h - target_h) < abs(best["h"] - target_h):
            best = c
    if best is None:      # текст длиннее, чем влезает даже минимальным кеглем
        hs, bs = 48, BODY_MIN
        fh, fb, fbold = font("Black", hs), font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d, title, fh, bw)
        bl = e.wrap_rich(d, body, fb, bw, fbold)
        best = dict(fh=fh, fb=fb, hl=hl, bl=bl, lh=int(hs * 1.18),
                    blh=int(bs * 1.45), gap=int(hs * 0.42),
                    h=len(hl) * int(hs * 1.18) + int(hs * 0.42) + len(bl) * int(bs * 1.45))
    return best


def statement(title, body, photo_key, acc, out, i=2, n=8, faces=()):
    img = photo(photo_key)
    d0 = ImageDraw.Draw(img)

    bw = int(W * COL)
    target_h = int(TEXT_AREA * W * H / bw)
    L = _autofit(d0, title, body, bw, target_h)

    x, y = pick_zone(img, bw, L["h"], faces, pad=COUNTER_PAD)
    img = scrim(img, (x, y, x + bw, y + L["h"]))
    d = ImageDraw.Draw(img)
    counter(d, i, n)

    y2 = e.draw_rich(d, L["hl"], L["fh"], x, y, L["lh"], acc,
                     base=WHITE, shadow=True) + L["gap"]
    e.draw_rich(d, L["bl"], L["fb"], x, y2, L["blh"], body_accent(acc),
                base=BODY, shadow=True)
    img.save(out)
    return {"text_bbox": (x, y, x + bw, y + L["h"]), "photo": photo_key}


# ── ОБЛОЖКА ──────────────────────────────────────────────────────────────────
def cover(hook, sub, photo_key, acc, out, i=1, n=8, sub_scale=1.0, gain=None):
    """Обложка тёмного набора: лицо автора открыто, текст — в пустоте кадра.

    Единственная разница со statement — здесь текст ОБЯЗАН обойти лицо, поэтому
    рамки лиц идут в pick_zone. Плюс акцентный штрих над заголовком: на белом
    наборе он же служит опознавательным знаком обложки (cover_top).

    `sub_scale` необязателен (по умолчанию 1.0): множитель кегля подзаголовка,
    когда именно он несёт главный смысл и должен звучать крупнее хука. Строки
    подзаголовка пересобираются под новый кегль, высота блока пересчитывается.
    `gain` пробрасывается в photo() — обложку можно сделать светлее.
    """
    img = photo(photo_key, gain=gain)
    d0 = ImageDraw.Draw(img)
    faces = faces_on(photo_key)

    bw = int(W * COL)
    target_h = int(COVER_AREA * W * H / bw)     # не TEXT_AREA: см. COVER_AREA
    L = _autofit(d0, hook, sub, bw, target_h)

    # Подзаголовок крупнее хука — по требованию слайда: пересобираем его строки
    # под увеличенный кегль и пересчитываем высоту блока (её ждёт pick_zone).
    if sub_scale != 1.0:
        bs2 = int(L["fb"].size * sub_scale)
        fb2, fbold2 = font("Regular", bs2), font("Bold", bs2)
        L["fb"] = fb2
        L["bl"] = e.wrap_rich(d0, sub, fb2, bw, fbold2)
        L["blh"] = int(bs2 * 1.45)
        L["h"] = len(L["hl"]) * L["lh"] + L["gap"] + len(L["bl"]) * L["blh"]

    dash_h = 26                      # штрих над заголовком + воздух под ним
    x, y = pick_zone(img, bw, L["h"] + dash_h, faces)
    img = scrim(img, (x, y, x + bw, y + L["h"] + dash_h))
    d = ImageDraw.Draw(img)

    # штрих — заливка акцентом: у заливки порога контраста нет, это не текст
    d.rounded_rectangle([x, y, x + 96, y + 8], 4, fill=acc)

    ty = y + dash_h
    y2 = e.draw_rich(d, L["hl"], L["fh"], x, ty, L["lh"], head_accent(acc),
                     base=WHITE, shadow=True) + L["gap"]
    e.draw_rich(d, L["bl"], L["fb"], x, y2, L["blh"], body_accent(acc),
                base=BODY, shadow=True)
    img.save(out)
    # Ника на обложке нет (правило проекта), счётчика тоже — это первый слайд.
    return {"text_bbox": (x, y, x + bw, y + L["h"] + dash_h), "photo": photo_key}


# ── БУЛЛЕТЫ ──────────────────────────────────────────────────────────────────
def bullets(title, items, photo_key, acc, out, i=5, n=8, faces=()):
    """Список: заголовок + пункты, у каждого акцентная метка-заливка.

    Кегль здесь не через _autofit: тот считает блок «заголовок + абзац», а у
    списка высота складывается из пунктов, и подгонять надо их вместе. Логика
    та же — цель по площади (TEXT_AREA), а не фиксированный кегль.
    """
    img = photo(photo_key)
    d0 = ImageDraw.Draw(img)
    bw = int(W * COL)
    target_h = int(TEXT_AREA * W * H / bw)

    ind = 42                          # отступ текста пункта от метки
    best = None
    # TAIL-02 (разнобой кегля на слайдах 4-5, 03.08): потолок цикла раньше стоял
    # на 96 — при коротком списке (3-4 пункта) в цель TEXT_AREA автоподбор
    # заполнял площадь раздутым заголовком. Замер на реальном контенте (build_c11.py,
    # три длины списка): 3 пункта -> title=96, 5 -> 82, 7 -> 60 (тот самый разброс
    # 60..96, ±60% от медианы). Сосед по слайду, bignum, держит фиксированные
    # 58 — на полном списке (7 пунктов) bullets и так сходится к 60, почти совпадая;
    # раздувает именно короткий список. BULLET_TITLE_MAX=74 срезает раздутие
    # коротких списков, длинные (уже <74) не трогает вовсе.
    for hs in range(BULLET_TITLE_MAX, 39, -2):
        bs = max(BULLET_MIN, min(BULLET_MAX, int(hs * BULLET_RATIO)))
        fh, fb, fbold = font("Black", hs), font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d0, title, fh, bw)
        wrapped = [e.wrap_rich(d0, it, fb, bw - ind, fbold) for it in items]
        lh, blh = int(hs * 1.18), int(bs * 1.42)
        gap, item_gap = int(hs * 0.5), int(bs * 0.85)
        h = (len(hl) * lh + gap +
             sum(len(w) * blh + item_gap for w in wrapped) - item_gap)
        if h > target_h * 1.12 or h > H - 2 * 40:
            continue
        c = dict(fh=fh, fb=fb, hl=hl, wr=wrapped, lh=lh, blh=blh,
                 gap=gap, ig=item_gap, h=h, bs=bs)
        if best is None or abs(h - target_h) < abs(best["h"] - target_h):
            best = c
    if best is None:
        raise ValueError("bullets: текст не влезает даже минимальным кеглем — "
                         "сократи пункты")

    x, y = pick_zone(img, bw, best["h"], faces, pad=COUNTER_PAD)
    img = scrim(img, (x, y, x + bw, y + best["h"]))
    d = ImageDraw.Draw(img)
    counter(d, i, n)

    cy = e.draw_rich(d, best["hl"], best["fh"], x, y, best["lh"], head_accent(acc),
                     base=WHITE, shadow=True) + best["gap"]
    for w in best["wr"]:
        # метка — заливка акцентом, квадрат в кегль строки
        s = int(best["bs"] * 0.34)
        d.rectangle([x, cy + best["bs"] * 0.42, x + s, cy + best["bs"] * 0.42 + s],
                    fill=acc)
        e.draw_rich(d, w, best["fb"], x + ind, cy, best["blh"], body_accent(acc),
                    base=BODY, shadow=True)
        cy += len(w) * best["blh"] + best["ig"]
    img.save(out)
    return {"text_bbox": (x, y, x + bw, y + best["h"]), "photo": photo_key}


# ── КРУПНАЯ ЦИФРА ────────────────────────────────────────────────────────────
def bignum(num, title, body, photo_key, acc, out, i=4, n=8, faces=()):
    """Цифра во весь кадр + подпись. Цифра — крупный текст, поэтому цветом
    акцента: порог для крупного 3.0, его проходит вся палитра, включая red."""
    img = photo(photo_key)
    d0 = ImageDraw.Draw(img)
    bw = int(W * COL)

    fn = font("Black", BIGNUM_NUM)
    nb = d0.textbbox((0, 0), str(num), font=fn)
    nw, nh = nb[2] - nb[0], nb[3] - nb[1]

    bs = BIGNUM_BODY
    fh, fb = font("Black", BIGNUM_TITLE), font("Regular", bs)
    hl = e.wrap_rich(d0, title, fh, bw)
    bl = e.wrap_rich(d0, body, fb, bw, font("Bold", bs))
    lh, blh = int(BIGNUM_TITLE * 1.18), int(bs * 1.44)
    h = nh + 34 + len(hl) * lh + 16 + len(bl) * blh

    x, y = pick_zone(img, bw, h, faces, pad=COUNTER_PAD)
    img = scrim(img, (x, y, x + bw, y + h))
    d = ImageDraw.Draw(img)
    counter(d, i, n)

    d.text((x - nb[0], y - nb[1]), str(num), font=fn, fill=head_accent(acc))
    cy = y + nh + 34
    cy = e.draw_rich(d, hl, fh, x, cy, lh, head_accent(acc), base=WHITE, shadow=True) + 16
    e.draw_rich(d, bl, fb, x, cy, blh, body_accent(acc), base=BODY, shadow=True)
    img.save(out)
    return {"text_bbox": (x, y, x + bw, y + h), "photo": photo_key}


# ── РАЗДЕЛИТЕЛЬ ──────────────────────────────────────────────────────────────
def divider(text, photo_key, acc, out, i=6, n=8, faces=()):
    """Одна короткая мысль на кадр — пауза между блоками. Без подписи и тела:
    если разделителю нужен абзац, это не разделитель, а statement."""
    img = photo(photo_key)
    d0 = ImageDraw.Draw(img)
    bw = int(W * COL)
    target_h = int(TEXT_AREA * W * H / bw)

    best = None
    for hs in range(150, 51, -2):
        f = font("Black", hs)
        lines = e.wrap_rich(d0, text, f, bw)
        lh = int(hs * 1.16)
        h = len(lines) * lh + 30
        if h > target_h * 1.12:
            continue
        c = dict(f=f, lines=lines, lh=lh, h=h)
        if best is None or abs(h - target_h) < abs(best["h"] - target_h):
            best = c
    if best is None:
        raise ValueError("divider: фраза слишком длинная — это не разделитель")

    x, y = pick_zone(img, bw, best["h"], faces, pad=COUNTER_PAD)
    img = scrim(img, (x, y, x + bw, y + best["h"]))
    d = ImageDraw.Draw(img)
    counter(d, i, n)
    d.rounded_rectangle([x, y, x + 96, y + 8], 4, fill=acc)
    e.draw_rich(d, best["lines"], best["f"], x, y + 30, best["lh"],
                head_accent(acc), base=WHITE, shadow=True)
    img.save(out)
    return {"text_bbox": (x, y, x + bw, y + best["h"]), "photo": photo_key}


# ── CTA ──────────────────────────────────────────────────────────────────────
def cta(title, code, sub, photo_key, acc, out, i=8, n=8):
    """Последний слайд: лицо автора + призыв, кодовое слово плашкой — по желанию.

    Лицо здесь второй и последний раз за карусель (правило разбора референса),
    поэтому текст так же обходит его через pick_zone.

    `code` НЕОБЯЗАТЕЛЕН (карусель #9). Пустой — плашки нет, а `sub` набирается
    крупнее и с большим интерлиньяжем. Причина: с плашкой действие просит она,
    без неё — подзаголовок, и тогда 38-м кеглем против 108-го у заголовка он
    просто не слышен. Место, освободившееся от плашки, уходит ему.
    """
    img = photo(photo_key)
    d0 = ImageDraw.Draw(img)
    faces = faces_on(photo_key)
    bw = int(W * COL)

    # CTA — единственный слайд, который идёт против TEXT_AREA: он не рассказывает,
    # а просит действие, и его должно быть слышно громче остальных (решение
    # Валерии по рендеру #6). Поэтому кегль здесь задан, а не подобран под площадь.
    fh = font("Black", 108)
    sub_px = 38 if code else 56
    fb = font("Regular", sub_px)
    hl = e.wrap_rich(d0, title, fh, bw)
    bl = e.wrap_rich(d0, sub, fb, bw, font("Bold", sub_px))
    lh = 122
    blh = 54 if code else 76

    if code:
        fc = font("Black", 84)
        cb = d0.textbbox((0, 0), code, font=fc)
        cw, ch = cb[2] - cb[0], cb[3] - cb[1]
        chip_w, chip_h = cw + 88, ch + 62
        gap = 34 + chip_h + 34
    else:
        gap = 34

    h = len(hl) * lh + gap + len(bl) * blh
    x, y = pick_zone(img, bw, h, faces, pad=COUNTER_PAD)
    img = scrim(img, (x, y, x + bw, y + h))
    d = ImageDraw.Draw(img)
    counter(d, i, n)

    cy = e.draw_rich(d, hl, fh, x, y, lh, head_accent(acc),
                     base=WHITE, shadow=True) + 34
    if code:
        # кодовое слово — заливка акцентом, текст поверх тем, что на ней читается
        d.rounded_rectangle([x, cy, x + chip_w, cy + chip_h], 18, fill=acc)
        d.text((x + 44 - cb[0], cy + 31 - cb[1]), code, font=fc, fill=chip_text(acc))
        cy += chip_h + 34
    e.draw_rich(d, bl, fb, x, cy, blh, body_accent(acc), base=BODY, shadow=True)
    img.save(out)
    return {"text_bbox": (x, y, x + bw, y + h), "photo": photo_key}
