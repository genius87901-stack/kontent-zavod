# -*- coding: utf-8 -*-
"""
engine_love — семейство «ЛЮБОВЬ»: полнокадровое фото, затемнение, крупный текст.

ОТКУДА ВЗЯЛСЯ. Референсы клиентки (@an.ni_gray, 27–31.08.2026): карусель-список,
где фотография занимает весь слайд, а поверх неё стоит три яруса текста —
крупные прописные, подзаголовок и рукописная строка. Ни одно из восемнадцати
семейств завода так не делает: `engine_photo` ближе всех, но он уводит кадр в
ОДИН тёплый тон, а здесь кадр остаётся собой.

ТРИ ПРАВИЛА, КОТОРЫЕ И ЕСТЬ СТИЛЬ:

1. ФОТО ВИДНО ВСЕГДА. Затемнение — ровно столько, сколько нужно тексту, и ни
   каплей больше. Альфа считается ПОД КАДР по фактической яркости под текстом,
   а не задаётся одним числом на карусель: светлый кадр гасится сильнее, тёмный
   слабее, читаемость у всех одна. Первый заход держал одно число на всю
   карусель (альфа 205) — прогулочные кадры уходили в чёрное и фотография
   переставала быть фотографией.

2. ТЕКСТ НИКОГДА НЕ ЛОЖИТСЯ НА ЛИЦО. Требование владельца, 31.08.2026. Полоса
   выбирается детектором лиц (`checks.face_boxes_img` — ТОТ ЖЕ детектор, что
   обходит лицо в субтитрах Reels, один на проект). Если блок не помещается ни
   в одну свободную полосу, уменьшается КЕГЛЬ, а не нарушается правило.

3. ЧАСТЬ СМЫСЛА — РУКОПИСНЫМ. MarckScript несёт вывод слайда, а не украшение.
   Рукописного нет ни у одного другого семейства завода — это подпись стиля.

ШРИФТЫ — только те, что уже едут в коробке (`fonts/`), добавлять нечего:
Onest (вариативный, начертания 400–900) и MarckScript.
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import checks
import engine4 as e

W, H = e.W, e.H
M = 76                                  # поле слева/справа
COLW = W - 2 * M
WHITE = (255, 255, 255)
BODY_C = (240, 238, 243)

_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_MARCK = os.path.join(_FONTS, "MarckScript", "MarckScript-Regular.ttf")

# ── ГРЕЙД: мягкий, в отличие от тёплого engine_photo ──
# Референс цвет не крутит: кадр там тёмный сам по себе. Наша задача — снять
# лишнюю пестроту дневного телефонного снимка, а не перекрасить его.
DESAT, GAIN, LIFT = 0.16, 0.72, 0.012

#: Целевая яркость ПОД ТЕКСТОМ. Было 62 — читалось уверенно, но кадр рядом с
#: текстом оставался светлым и общий вид получался вялым; 48 по решению
#: владельца 31.08.2026 («ещё немного затемнения, чтобы контрастнее»).
#: Белый текст на этом уровне читается уверенно,
#: а кадр вокруг остаётся видимым. Замер по девяти слайдам: альфа вышла 59–109
#: (кадр на палубе тёмный сам — ему хватило 59).
TARGET_LUM = 48
ALPHA_MIN, ALPHA_MAX = 40, 185

#: ОБЩЕЕ затемнение всего кадра — пол, ниже которого не опускаемся нигде,
#: даже у дальнего от текста края (решение владельца 31.08.2026: «добавляй
#: небольшое затемнение на фото»). Раньше дальний край гасился долей от
#: местной альфы (0.34), и на светлых прогулочных кадрах угол оставался почти
#: неприглушённым — слайд читался как фотография с подписью, а не как слайд.
BASE_DIM = 78

#: Кегли. Обложка крупнее внутренних — она делит кадр только с лицом,
#: внутренний слайд несёт ещё и абзац.
TS_COVER, TS_INNER = 152, 112
TS_MIN = 52                             # ниже не опускаемся: станет нечитаемо
BODY_MAX_COVER, BODY_MAX_INNER = 58, 50


def marck(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_MARCK, size)


def src_path(photo: str) -> str:
    """Путь к кадру. Принимает И ключ фотобанка, И путь к файлу.

    Весь завод адресует кадры КЛЮЧОМ (`e.PHOTOS[key]`) — так работают все
    остальные движки, дымовые тесты и мост. Но этот движок начинали с кадров
    клиента, которые лежат вне банка, поэтому он умеет и прямой путь. Порядок
    важен: сначала банк, потом файл — ключ короче и не должен случайно
    совпасть с именем файла в текущем каталоге.
    """
    if photo in getattr(e, "PHOTOS", {}):
        return e.PHOTOS[photo]
    return photo


def is_key(photo: str) -> bool:
    return photo in getattr(e, "PHOTOS", {})


def fit(photo: str) -> Image.Image:
    """Кадр под 1080x1350 по центру (cover+кроп). Ключ банка или путь."""
    im = Image.open(src_path(photo)).convert("RGB")
    k = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((x, y, x + W, y + H))


def grade(img: Image.Image) -> Image.Image:
    a = np.asarray(img.convert("RGB")).astype(np.float32) / 255.0
    lum = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    a = a * (1 - DESAT) + lum[..., None] * DESAT
    a = a * GAIN + LIFT
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))


def detail_map(img: Image.Image):
    """Карта детализации: где в кадре «шумно», туда текст класть нельзя."""
    import cv2
    g = np.asarray(img.convert("L")).astype(np.float32)
    dx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    dy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.blur(np.sqrt(dx * dx + dy * dy), (61, 61))


def pick_y(img, bh, faces, pad=72):
    """Полоса под блок высотой bh: тихая по детализации и БЕЗ лица.

    В отличие от `engine_photo.pick_zone` колонка здесь всегда одна и во всю
    ширину — боковых колонок в этом стиле нет, сканируется только вертикаль.
    Наезд на лицо штрафуется так, что любая свободная полоса заведомо лучше:
    порог 9000 в `place()` отделяет «лицо задето» от «просто шумно».

    ШТРАФ ПЛОСКИЙ, А НЕ ДОЛЕВОЙ. Сначала он считался как доля лица в площади
    блока (`10000 * oy / bh`) — и на крупном тексте размывался: чем выше блок,
    тем меньше доля, и при высоте 887 задетое лицо давало меньше порога. Гейт
    молчал, а текст лежал на лице. Лицо задето — штраф полный, независимо от
    размера блока: это запрет, а не предпочтение.
    """
    dm = detail_map(img)
    best, best_s = pad, None
    for y in range(pad, H - bh - pad, 12):
        s = float(dm[y:y + bh, M:W - M].mean())
        for (_fx0, fy0, _fx1, fy1) in faces:
            if min(y + bh, fy1) > max(y, fy0):
                s += 10000.0
                break
        if best_s is None or s < best_s:
            best, best_s = y, s
    return best, best_s


def need_alpha(img, y0, y1) -> int:
    """Сколько затемнения нужно ИМЕННО ЭТОМУ кадру под текстом."""
    a = np.asarray(img.convert("L")).astype(np.float32)[y0:y1, M:W - M]
    lum = float(a.mean())
    if lum <= TARGET_LUM:
        return ALPHA_MIN
    return int(np.clip(255 * (1 - TARGET_LUM / lum), ALPHA_MIN, ALPHA_MAX))


def veil(img, y0, y1, alpha):
    """Затемнение на ВЕСЬ кадр с уклоном к тексту — шва нет.

    Полосой под текстом (первый заход) на светлом фото был виден шов: полоса
    тёмная, остальное нет. Приём взят у `engine_photo.scrim`.
    """
    cy = (y0 + y1) / 2
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    far = max(cy, H - cy)
    lo = max(int(alpha * 0.34), BASE_DIM)
    for y in range(H):
        t = 1 - min(1.0, abs(y - cy) / far)
        od.line([(0, y), (W, y)], fill=(8, 6, 12, int(lo + (alpha - lo) * (t ** 0.7))))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def wrap(d, txt, f, bw):
    out, line = [], ""
    for word in txt.split():
        t = (line + " " + word).strip()
        if d.textlength(t, font=f) <= bw or not line:
            line = t
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    return out


def layout(d, title, body, script, ts, body_w, body_max):
    """Строки блока при кегле заголовка ts. Возвращает (строки, высота).

    `body_w` = 800 — ПОДЗАГОЛОВОК обложки: прописные, толстый, вровень с
    заголовком. Внутри — то же начертание по решению владельца 31.08: тонкий
    абзац рядом с тяжёлым заголовком выглядел чужим.
    """
    rows = []
    ft = e.font("Black", ts)
    for ln in wrap(d, title, ft, COLW):
        rows.append((ln, ft, int(ts * 0.18), WHITE))
    rows[-1] = (rows[-1][0], rows[-1][1], int(ts * 0.34), WHITE)
    if body:
        bs = max(30, min(body_max, int(ts * 0.40)))
        fb = e.font("Regular", bs) if body_w == 400 else _onest(body_w, bs)
        for ln in wrap(d, body, fb, COLW):
            rows.append((ln, fb, int(bs * 0.44), BODY_C))
        rows[-1] = (rows[-1][0], rows[-1][1], int(bs * 0.90), BODY_C)
    if script:
        fs = marck(max(58, min(96, int(ts * 0.72))))
        for ln in wrap(d, script, fs, COLW):
            rows.append((ln, fs, 16, WHITE))
    h = 0
    for txt, f, gap, _c in rows:
        b = d.textbbox((0, 0), txt, font=f)
        h += (b[3] - b[1]) + gap
    return rows, h


def _onest(weight, size):
    """Onest произвольной насыщенности. e.font знает только именованные веса."""
    f = ImageFont.truetype(os.path.join(_FONTS, "Onest.ttf"), size)
    f.set_variation_by_axes([weight])
    return f


def place(img, d, title, body, script, ts_max, body_w, body_max):
    """Наибольший кегль, при котором блок встаёт в полосу БЕЗ лица."""
    faces = checks.face_boxes_img(img)
    ts = ts_max
    while ts >= TS_MIN:
        rows, h = layout(d, title, body, script, ts, body_w, body_max)
        if h < H - 200:
            y, score = pick_y(img, h, faces)
            if score is not None and score < 9000:
                return rows, h, y, ts, faces
        ts -= 6
    rows, h = layout(d, title, body, script, TS_MIN, body_w, body_max)
    y, _s = pick_y(img, h, faces)
    return rows, h, y, TS_MIN, faces


def slide(photo, title, body, script, out, *, align="left", cover=False,
          mark="", photo_key=None, acc=None) -> dict:
    """Один слайд. Возвращает паспорт: где встал текст, сколько дали затемнения.

    ПРО ГЕЙТ ЛИЦА. `checks.check_face_safe` читает ключи `photo` и `text_bbox`,
    но `photo` у него — КЛЮЧ ФОТОБАНКА (`e.PHOTOS`), а не путь к файлу: он сам
    заново прогоняет детектор. Этот движок умеет работать и с чужой папкой
    (кадры клиента лежат вне банка) — тогда ключа нет, и гейт МОЛЧА пропустил
    бы слайд, потому что «нет фото» он считает неприменимым вопросом.
    Молчание вместо проверки — ровно тот случай, на котором завод уже обжигался,
    поэтому движок проверяет себя САМ и кладёт вердикт в `face_clean`.
    Вызывающий обязан на него смотреть; `photo_key` заполняется, только если
    кадр действительно из банка.
    """
    ts_max = TS_COVER if cover else TS_INNER
    body_max = BODY_MAX_COVER if cover else BODY_MAX_INNER
    img = grade(fit(photo))
    d0 = ImageDraw.Draw(img)
    rows, h, y0, ts, faces = place(img, d0, title, body, script, ts_max, 800,
                                   body_max)
    alpha = need_alpha(img, y0, y0 + h)
    img = veil(img, y0, y0 + h, alpha)
    d = ImageDraw.Draw(img)
    y = y0
    for txt, f, gap, col in rows:
        b = d.textbbox((0, 0), txt, font=f)
        x = (W - (b[2] - b[0])) // 2 if align == "center" else M
        # Акцент красит РУКОПИСНУЮ строку — единственное место, где цвет в этой
        # раскладке уместен. Без акцента она белая: таким вид и согласован.
        if acc is not None and f.path == _MARCK:
            col = tuple(acc)
        d.text((x - b[0], y - b[1]), txt, font=f, fill=col)
        y += (b[3] - b[1]) + gap
    if mark:
        d.text((M, H - 54), mark, font=e.font("Regular", 22), fill=(226, 224, 230))
    img.save(out, quality=94)
    bbox = (M, y0, W - M, y0 + h)
    clean = True
    for (fx0, fy0, fx1, fy1) in faces:
        if min(bbox[2], fx1) > max(bbox[0], fx0) and \
           min(bbox[3], fy1) > max(bbox[1], fy0):
            clean = False
    return {"photo": photo_key or (photo if is_key(photo) else None),
            "src": photo, "text_bbox": bbox, "kegl": ts,
            "alpha": alpha, "faces": len(faces), "face_clean": clean}


# ── РАСКЛАДКА «ЛЮБОВЬ 2»: плашки, смешанный регистр, служебные строки ────────
# Второй референс клиентки (@matvienko.viktoriya, 31.08.2026). Тот же кадр во
# весь слайд, но подача другая: регистр смешанный, ключевые слова на сливочной
# плашке, сверху подпись аккаунта и счётчик, внизу «листай» со стрелкой.
# Рукописного здесь нет вовсе — этим два шаблона и различаются на глаз.

PLATE_PAD_X, PLATE_PAD_Y = 18, 10

#: Колонка УЖЕ полной ширины — чтобы вставать СБОКУ от лица, как в референсе.
COL2, COL2_COVER = 0.66, 0.82

#: ЗАГОЛОВОК — НЕ БОЛЬШЕ ДВУХ СТРОК (решение владельца 31.08.2026). Это условие
#: подбора кегля, а не пожелание вёрстки: разложилась фраза на три строки —
#: шрифт уменьшается, пока не уляжется в две.
MAX_HEAD_LINES = 2
TS2_COVER, TS2_INNER, TS2_MIN = 146, 120, 56


def _scan(dm, x, bw, bh, faces, pad):
    """Лучшая высота для колонки на СТОРОНЕ x. Наезд на лицо штрафуется."""
    best, best_s = pad, None
    for y in range(pad, H - bh - 160, 12):
        s = float(dm[y:y + bh, x:x + bw].mean())
        for (fx0, fy0, fx1, fy1) in faces:
            if min(x + bw, fx1) > max(x, fx0) and min(y + bh, fy1) > max(y, fy0):
                s += 10000.0
                break
        if best_s is None or s < best_s:
            best, best_s = y, s
    return best, best_s


def pick_zone(img, bw, bh, faces, pad=150, prefer="left"):
    """Колонка встаёт на СТОРОНЕ prefer. На другую уходит, только если тут лицо.

    Сторона — ПРЕДПОЧТЕНИЕ, правило про лицо — запрет, и запрет сильнее.
    Не «выбрать лучшую сторону по шуму» (тогда сторона гуляла бы от слайда к
    слайду), а «нужная, если свободна». Внутренние слайды идут влево, обложка —
    вправо: так согласовано с владельцем 31.08.2026.
    """
    dm = detail_map(img)
    x1 = M if prefer == "left" else W - M - bw
    x2 = W - M - bw if prefer == "left" else M
    y, s = _scan(dm, x1, bw, bh, faces, pad)
    if s is not None and s < 9000:
        return (x1, y), s
    return ((x2,) + (_scan(dm, x2, bw, bh, faces, pad)[0],)), _scan(
        dm, x2, bw, bh, faces, pad)[1]


def _rows2(d, head, sub, ts, colw):
    """Строки блока. Строка вида ==текст== рисуется на плашке.

    Длинная фраза переносится ВНУТРИ строки, и каждая часть получает СВОЮ
    плашку — так в референсе: «мышление» и «миллиардера» стоят двумя плашками
    подряд. Пока строка была неделимой, одна длинная фраза сбивала кегль всему
    слайду.
    """
    out = []
    for raw in head:
        plate = raw.startswith("==") and raw.endswith("==")
        txt = raw[2:-2] if plate else raw
        f = _onest(700 if plate else 600, ts)
        for ln in wrap(d, txt, f, colw - (2 * PLATE_PAD_X if plate else 0)):
            out.append((ln, f, plate, int(ts * 0.30), WHITE))
    head_rows = len(out)
    if sub:
        # Описание 600, а не 400: тонкий абзац на пёстром кадре расплывался.
        fs = _onest(600, max(30, int(ts * 0.36)))
        for ln in wrap(d, sub, fs, colw):
            out.append((ln, fs, False, int(ts * 0.14), WHITE))
    return out, head_rows


def _size2(d, rows):
    h, w = 0, 0
    for txt, f, plate, gap, _c in rows:
        b = d.textbbox((0, 0), txt, font=f)
        h += (b[3] - b[1]) + (2 * PLATE_PAD_Y if plate else 0) + gap
        w = max(w, (b[2] - b[0]) + (2 * PLATE_PAD_X if plate else 0))
    return w, h


def slide_plate(photo, head, sub, out, *, n=1, total=9, cover=False,
                mark="", tail="Дальше интересно, листай", photo_key=None,
                acc=None) -> dict:
    """Слайд «Любовь 2». `head` — список строк, ==так== помечена плашка."""
    colw = int(W * (COL2_COVER if cover else COL2))
    prefer = "right" if cover else "left"
    img = grade(fit(photo))
    faces = checks.face_boxes_img(img)
    d0 = ImageDraw.Draw(img)

    ts = TS2_COVER if cover else TS2_INNER
    while ts >= TS2_MIN:
        rows, head_rows = _rows2(d0, head, sub, ts, colw)
        bw, bh = _size2(d0, rows)
        if head_rows <= MAX_HEAD_LINES and bw <= colw and bh < H - 300:
            (x, y), score = pick_zone(img, colw, bh, faces, prefer=prefer)
            if score is not None and score < 9000:
                break
        ts -= 4
    else:
        rows, head_rows = _rows2(d0, head, sub, TS2_MIN, colw)
        bw, bh = _size2(d0, rows)
        (x, y), score = pick_zone(img, colw, bh, faces, prefer=prefer)

    img = veil(img, y, y + bh, need_alpha(img, y, y + bh))
    d = ImageDraw.Draw(img)
    cy = y
    for txt, f, plate, gap, col in rows:
        b = d.textbbox((0, 0), txt, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        if plate:
            d.rectangle([x, cy, x + tw + 2 * PLATE_PAD_X, cy + th + 2 * PLATE_PAD_Y],
                        fill=tuple(acc) if acc is not None
                        else e.ACCENTS["vanilla"])
            d.text((x + PLATE_PAD_X - b[0], cy + PLATE_PAD_Y - b[1]), txt,
                   font=f, fill=(22, 20, 18))
            cy += th + 2 * PLATE_PAD_Y + gap
        else:
            d.text((x - b[0], cy - b[1]), txt, font=f, fill=col)
            cy += th + gap

    # Служебные строки. Стоят у самого края, куда затемнение под текстом почти
    # не достаёт: на светлых участках одним тоном яркости не добавишь — тень.
    fs = _onest(500, 28)

    def _sluzh(sx, sy, txt):
        for dx, dy in ((1, 1), (2, 2)):
            d.text((sx + dx, sy + dy), txt, font=fs, fill=(0, 0, 0))
        d.text((sx, sy), txt, font=fs, fill=WHITE)

    if mark:
        _sluzh(M, 66, mark)
    cnt = "%d/%d" % (n, total)
    _sluzh(W - M - d.textlength(cnt, font=fs), 66, cnt)
    if tail:
        d.text((M, H - 96), tail, font=_onest(400, 30), fill=WHITE)
        ax = W - M - 54
        d.line([(ax, H - 82), (ax + 44, H - 82)], fill=WHITE, width=3)
        d.line([(ax + 44, H - 82), (ax + 28, H - 96)], fill=WHITE, width=3)
        d.line([(ax + 44, H - 82), (ax + 28, H - 68)], fill=WHITE, width=3)

    img.save(out, quality=94)
    bbox = (x, y, x + colw, y + bh)
    clean = all(not (min(bbox[2], f[2]) > max(bbox[0], f[0]) and
                     min(bbox[3], f[3]) > max(bbox[1], f[1])) for f in faces)
    return {"photo": photo_key or (photo if is_key(photo) else None),
            "src": photo, "text_bbox": bbox, "kegl": ts,
            "faces": len(faces), "face_clean": clean}


# ── РАСКЛАДКА «ЛЮБОВЬ 3»: центр, нижняя треть, таблетка снизу ────────────────
# Третий референс клиентки (31.08.2026). Кадр остаётся ДНЕВНЫМ — этим шаблон и
# отличается от «Любови 1», где кадр уводится в глубокую темноту. Три яруса по
# центру: белый заголовок, белый подзаголовок мельче, и ТОЛЬКО последняя строка
# на сливочной таблетке. Заход «весь текст в плашках» владельцу не подошёл и
# откатан в тот же день.

PAD3_X, PAD3_Y = 30, 15
COL3, COL3_BIG = 0.86, 0.94     # обложка и призыв идут на широкой колонке
SUB3_COL = 0.82                 # подзаголовок уже заголовка: иначе спорит с ним
BOTTOM3 = 118                   # низ блока прибит к этой линии
GAP3_HEAD, GAP3_PLATE = 26, 14
TS3, TS3_BIG, TS3_MIN = 150, 176, 62

# Грейд мягче, чем у «Любови 1» (0.40): фотография обязана остаться дневной.
DESAT3, GAIN3, LIFT3 = 0.14, 0.80, 0.010
FLOOR3, PEAK3 = 22, 96          # донная завеса: пол на кадр и пик под текстом


def grade3(img):
    a = np.asarray(img.convert("RGB")).astype(np.float32) / 255.0
    lum = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    a = a * (1 - DESAT3) + lum[..., None] * DESAT3
    return Image.fromarray((np.clip(a * GAIN3 + LIFT3, 0, 1) * 255).astype(np.uint8))


def veil3(img, y0):
    """Пол на весь кадр плюс мягкий налив снизу — к текстовому блоку."""
    ov = Image.new("L", (1, H))
    px = ov.load()
    for y in range(H):
        t = 0.0 if y < y0 else (y - y0) / max(1, H - y0)
        px[0, y] = int(FLOOR3 + (PEAK3 - FLOOR3) * (t ** 0.6))
    return Image.composite(Image.new("RGB", (W, H), (8, 6, 10)), img,
                           ov.resize((W, H)))


def _shadow3(img, paint):
    """Трёхслойная мягкая тень под блоком.

    Приём взят у проигравшего варианта сверки, но с ПОНИЖЕННОЙ альфой: у него
    от такой тени вокруг крупных литер стояло тёмное гало — на однотонной
    студийной циклораме референса его бы не было, а на телефонном кадре видно.
    """
    from PIL import ImageFilter
    mask = Image.new("L", (W, H), 0)
    paint(ImageDraw.Draw(mask), 255)
    out = img.convert("RGBA")
    for radius, alpha, dy in ((26, 96, 10), (11, 84, 5), (4, 70, 2)):
        blur = mask.filter(ImageFilter.GaussianBlur(radius))
        layer = Image.new("RGBA", (W, H), (6, 5, 8, 0))
        layer.putalpha(blur.point(lambda v, a=alpha: v * a // 255))
        out = Image.alpha_composite(out, layer.transform(
            (W, H), Image.AFFINE, (1, 0, 0, 0, 1, -dy)))
    return out.convert("RGB")


def _rows3(d, head, sub, plates, ts, colw):
    rows = []
    fh = _onest(900, ts)
    for ln in wrap(d, head, fh, colw):
        rows.append(("head", ln, fh))
    if sub:
        fs = _onest(800, max(34, int(ts * 0.40)))
        for ln in wrap(d, sub, fs, int(W * SUB3_COL)):
            rows.append(("sub", ln, fs))
    fp = _onest(700, max(30, int(ts * 0.34)))
    for txt in plates:
        for ln in wrap(d, txt, fp, colw - 2 * PAD3_X):
            rows.append(("plate", ln, fp))
    h = 0
    for kind, txt, f in rows:
        b = d.textbbox((0, 0), txt, font=f)
        h += (b[3] - b[1]) + (2 * PAD3_Y + GAP3_PLATE if kind == "plate"
                              else int(GAP3_HEAD * 0.6) if kind == "sub"
                              else GAP3_HEAD)
    return rows, h


def slide_pill(photo, head, sub, plates, out, *, big=False, photo_key=None,
               acc=None) -> dict:
    """Слайд «Любовь 3». `big` — обложка и призыв: крупнее и на широкой колонке.

    Обложка и призыв — вход и выход карусели, они должны читаться как пара.
    """
    colw = int(W * (COL3_BIG if big else COL3))
    ts_max = TS3_BIG if big else TS3
    img = grade3(fit(photo))
    faces = checks.face_boxes_img(img)
    d0 = ImageDraw.Draw(img)

    ts = ts_max
    while ts >= TS3_MIN:
        rows, h = _rows3(d0, head, sub, plates, ts, colw)
        y0 = H - BOTTOM3 - h
        if y0 > 90 and all(min(y0 + h, f[3]) <= max(y0, f[1]) for f in faces):
            break
        ts -= 4
    else:
        rows, h = _rows3(d0, head, sub, plates, TS3_MIN, colw)
        y0, _s = pick_y(img, h, faces)

    img = veil3(img, max(0, y0 - 260))

    def paint(d, fill=None):
        y = y0
        for kind, txt, f in rows:
            b = d.textbbox((0, 0), txt, font=f)
            tw, th = b[2] - b[0], b[3] - b[1]
            if kind == "plate":
                pw, ph = tw + 2 * PAD3_X, th + 2 * PAD3_Y
                px = (W - pw) // 2
                d.rounded_rectangle([px, y, px + pw, y + ph], ph // 2,
                                    fill=fill or (tuple(acc) if acc is not None
                                                  else e.ACCENTS["vanilla"]))
                if fill is None:
                    d.text((px + PAD3_X - b[0], y + PAD3_Y - b[1]), txt,
                           font=f, fill=(22, 20, 18))
                y += ph + GAP3_PLATE
            else:
                d.text(((W - tw) // 2 - b[0], y - b[1]), txt, font=f,
                       fill=fill or WHITE)
                y += th + (int(GAP3_HEAD * 0.6) if kind == "sub" else GAP3_HEAD)

    img = _shadow3(img, lambda d, v: paint(d, fill=v))
    paint(ImageDraw.Draw(img))
    img.save(out, quality=94)
    bbox = (M, y0, W - M, y0 + h)
    clean = all(min(bbox[3], f[3]) <= max(bbox[1], f[1]) for f in faces)
    return {"photo": photo_key or (photo if is_key(photo) else None),
            "src": photo, "text_bbox": bbox, "kegl": ts,
            "faces": len(faces), "face_clean": clean}


# ── РАСКЛАДКА «ЛЮБОВЬ 5»: плёнка, рейка услуг, золото внахлёст ───────────────
# Пятый референс клиентки (31.08.2026). Кадр обработан ПОД ПЛЁНКУ: чёрные
# подняты, цвет выцвел в оливково-песочный, контраст мягкий. Это не затемнение,
# а грейд, и он половина впечатления от шаблона.
#
# Пять правок против первого захода — все из разбора сверки:
#   1. GAIN подстраивается под яркость исходника: фиксированный 0.86 на светлом
#      кадре превращал выцветание в засветку (кремовый дом уходил в молочный).
#   2. Зерно 4.2 → 2.8: на 4.2 песочило лицо.
#   3. Нахлёст меряется от ВЫСОТЫ ПРОПИСНОЙ, а не от высоты рукописной строки:
#      bbox у MarckScript плотный, и заход выходил случайным.
#   4. Рейка: контраст считается по её же полосе, а не задан константой — на
#      кремовом сайдинге светло-серая рейка исчезала. Плюс рейка съезжает вниз,
#      если попала на лицо (раньше это только записывалось в отчёт).
#   5. Коллизия свисающих букв: хвост «З»/«У» рукописного садился на точки «Ё»
#      в прописных. Если в строке есть Ё или Й — рукописное сдвигается вправо.

GOLD = None                      # ленивая ссылка на e.ACCENTS["gold"]
DESAT5 = 0.40                    # выцветание: цвет приглушён, но не ч/б
OLIVE = (1.05, 1.02, 0.82)       # тёплый с уходом в оливковый
CONTRAST5 = 0.78                 # мягкий контраст: сжатие к середине
GAIN5 = 0.86                     # база; ниже подстраивается под кадр
LIFT5 = (0.150, 0.144, 0.120)    # подъём чёрных: тени сероватые, не чёрные
GRAIN5 = 2.8
LUM5_REF = 118                   # яркость, под которую подобран GAIN5

RAIL_Y, RAIL_SIZE, RAIL_GAP = 62, 26, 10
RAIL_TRACK = 1.6                 # разрядка: мелкая кириллица так опрятнее
SCRIPT_K, OVERLAP5 = 0.46, 0.34
TS5, TS5_MIN = 148, 64

#: Шрифты заголовка на выбор. Все уже едут в коробке (fonts/), докладывать
#: нечего. Значение — либо ("var", вес) для вариативного Onest, либо путь.
HEAD_FACES = {
    "onest":     ("var", 900),
    "unbounded": ("file", "Unbounded/Unbounded-Black.ttf"),
    "oswald":    ("file", "Oswald/Oswald-SemiBold.ttf"),
    "nunito":    ("file", "Nunito/Nunito-Black.ttf"),
    "playfair":  ("file", "PlayfairDisplay/PlayfairDisplay-Black.ttf"),
    "rubik":     ("file", "Rubik/Rubik-Bold.ttf"),
    "prata":     ("file", "Prata/Prata-Regular.ttf"),
    "golos":     ("var_file", "GolosText/GolosText-Variable.ttf", 800),
}


def head_font(name, size):
    spec = HEAD_FACES.get(name, HEAD_FACES["onest"])
    if spec[0] == "var":
        return _onest(spec[1], size)
    if spec[0] == "var_file":
        f = ImageFont.truetype(os.path.join(_FONTS, spec[1]), size)
        try:
            f.set_variation_by_axes([spec[2]])
        except Exception:                       # статический файл — не беда
            pass
        return f
    return ImageFont.truetype(os.path.join(_FONTS, spec[1]), size)


def grade_film(img, seed=0):
    """Кадр под плёнку. GAIN подстраивается под яркость исходника."""
    a = np.asarray(img.convert("RGB")).astype(np.float32) / 255.0
    lum_src = float(np.asarray(img.convert("L")).mean())
    gain = GAIN5 * min(1.0, LUM5_REF / max(1.0, lum_src)) ** 0.6
    lum = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    a = a * (1 - DESAT5) + lum[..., None] * DESAT5
    a = a * np.array(OLIVE, dtype=np.float32)
    a = (a - 0.5) * CONTRAST5 + 0.5
    lift = np.array(LIFT5, dtype=np.float32)
    a = a * gain * (1 - lift) + lift
    rng = np.random.default_rng(seed)
    a = a + rng.normal(0.0, GRAIN5 / 255.0, a.shape[:2])[..., None]
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8)), gain


def _cap_h(f):
    """Высота ПРОПИСНОЙ — по ней меряется нахлёст, а не по bbox рукописного."""
    b = f.getbbox("Н")
    return b[3] - b[1]


def _shadow_text(d, x, y, txt, f, fill, spread=4, col=(12, 10, 6)):
    for dx in range(-spread, spread + 1, max(1, spread)):
        for dy in range(-spread, spread + 1, max(1, spread)):
            if dx or dy:
                d.text((x + dx, y + dy), txt, font=f, fill=col)
    d.text((x, y), txt, font=f, fill=fill)


def slide_film(photo, rail, script, title, out, *, face="onest", seed=0,
               photo_key=None, acc=None) -> dict:
    """Слайд «Любовь 5». `face` — шрифт заголовка из HEAD_FACES."""
    global GOLD
    if GOLD is None:
        GOLD = e.ACCENTS["gold"]
    img, gain = grade_film(fit(photo), seed)
    faces = checks.face_boxes_img(img)
    d0 = ImageDraw.Draw(img)

    ts = TS5
    while ts >= TS5_MIN:
        fh = head_font(face, ts)
        lines = wrap(d0, title, fh, COLW)
        fs = marck(max(40, int(ts * SCRIPT_K)))
        hs = d0.textbbox((0, 0), script, font=fs)[3] if script else 0
        h = sum(d0.textbbox((0, 0), ln, font=fh)[3] for ln in lines) + \
            int(ts * 0.10) * (len(lines) - 1)
        ov = int(_cap_h(fh) * OVERLAP5) if script else 0
        y0 = int(H * 0.60) - ov
        if y0 + h + ov < H - 96 and all(min(y0 + h, f[3]) <= max(y0 - ov, f[1])
                                        for f in faces):
            break
        ts -= 4
    else:
        fh, fs = head_font(face, TS5_MIN), marck(60)
        lines = wrap(d0, title, fh, COLW)
        h = sum(d0.textbbox((0, 0), ln, font=fh)[3] for ln in lines)
        ov, y0 = 0, pick_y(img, h, faces)[0]

    # РЕЙКА. Цвет и тень — по фактической яркости её же полосы, не константой.
    rail_y = RAIL_Y
    rail_h = 2 * RAIL_SIZE + 2 * RAIL_GAP
    for _ in range(24):                       # съехать вниз, если попала на лицо
        if all(min(rail_y + rail_h, f[3]) <= max(rail_y, f[1]) for f in faces):
            break
        rail_y += 26
    strip = np.asarray(img.convert("L"))[rail_y:rail_y + rail_h, M:W - M]
    rail_light = float(strip.mean()) > 150

    d = ImageDraw.Draw(img)
    fr = _onest(430, RAIL_SIZE)
    y = rail_y
    for ln in rail:
        x = M
        for ch in ln:                          # разрядка по знакам
            if rail_light:
                _shadow_text(d, x, y, ch, fr, (255, 255, 255), spread=2)
            else:
                d.text((x, y), ch, font=fr, fill=(214, 211, 205))
            x += d.textlength(ch, font=fr) + RAIL_TRACK
        y += RAIL_SIZE + RAIL_GAP

    # ЗОЛОТОЕ РУКОПИСНОЕ внахлёст сверху. Сдвиг вправо, если в первой строке
    # прописных есть Ё или Й — иначе хвост садится на их точки.
    if script:
        sx = M + (30 if any(c in lines[0] for c in "ЁЙ") else 0)
        bs = d.textbbox((0, 0), script, font=fs)
        _shadow_text(d, sx - bs[0], y0 - bs[1], script, fs,
                     tuple(acc) if acc is not None else GOLD, spread=3)

    yy = y0 + ov
    for ln in lines:
        b = d.textbbox((0, 0), ln, font=fh)
        _shadow_text(d, M - b[0], yy - b[1], ln, fh, WHITE, spread=5)
        yy += (b[3] - b[1]) + int(ts * 0.10)

    img.save(out, quality=94)
    bbox = (M, y0 - ov, W - M, yy)
    clean = all(min(bbox[3], f[3]) <= max(bbox[1], f[1]) for f in faces)
    return {"photo": photo_key or (photo if is_key(photo) else None),
            "src": photo, "text_bbox": bbox, "kegl": ts,
            "gain": round(gain, 3), "face": face,
            "faces": len(faces), "face_clean": clean}
