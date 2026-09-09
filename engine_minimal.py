# -*- coding: utf-8 -*-
"""
МИНИМАЛИЗМ — новый стиль карусели (21.08.2026, по референсу владельца: скрин
собственной Истории Instagram — вечерний кадр, лёгкое затемнение снизу,
короткая подпись мелко в правом нижнем углу, стрелка-подсказка «листай»).

ИДЕЯ. Не карточка с крупным заголовком, как во всех остальных семействах, а
живой кадр, который сам несёт настроение — текст только подписывает его, не
конкурирует с фото за внимание. Фото — БЕЗ грейда (в отличие от engine_photo):
натуральный цвет, никакой стилизации поверх, только лёгкая тень снизу, чтобы
подпись читалась.

РОТАЦИЯ. По решению владельца — каждый 3-й слот в круге стилей (см. HANDOFF,
запись 21.08.2026), отдельно от «оранжевого = переписка» правила 17.08.

ГЕОМЕТРИЯ ПРОТИВ ЛИЦА. Как и у остальных стилей библиотеки-18 (brutalist,
scrapbook, quote, sticker, meme), позиция подписи ФИКСИРОВАНА, а не подбирается
под конкретное лицо (в отличие от engine_photo.pick_zone) — короткая подпись в
нижней трети кадра на портретных кадрах банка обычно не задевает лицо (оно
в кадре обычно по центру/выше), но это не гарантия геометрией, а наблюдение:
безопасность подтверждает checks.verify на каждом реальном фото, не движок.
"""
from PIL import Image, ImageDraw

import engine4 as e

W, H, M = e.W, e.H, e.M
WHITE = (255, 255, 255)

# Затемнение — «небольшое», не полноэкранное: полоса от этой доли высоты кадра
# до самого низа, альфа нарастает плавно. Именно доля кадра, а не фикс. пиксели —
# базовая нижняя граница; фактический верх полосы _compose() поднимает выше,
# если сам текстовый блок выше неё (см. правку 22.08 — на светлых/пёстрых
# фото текст без затемнения был нечитаем даже при 3 строках подписи).
SCRIM_TOP = 0.60
SCRIM_MAX_A = 195
SCRIM_MARGIN = 40  # запас над первой строкой текста, px

COL_W = int(W * 0.50)          # ширина колонки подписи
COL_RIGHT = W - M              # правый край колонки — обычный безопасный отступ
BOTTOM_PAD = 96                # от низа кадра до стрелки/кодового слова
ARROW_GAP = 30                 # зазор между текстом и стрелкой/словом
ARROW_LEN = 56


def _scrim(img, top_y):
    """Мягкая тень снизу — только под подписью, не на весь кадр. top_y — верх
    полосы в пикселях, уже поднятый под фактическую высоту текстового блока
    (см. _compose)."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    band = H - top_y
    for y in range(top_y, H):
        t = (y - top_y) / band
        a = int(SCRIM_MAX_A * (t ** 1.2))
        od.line([(0, y), (W, y)], fill=(6, 6, 8, a))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def _fit_caption(d, text, maxw, hi=52, lo=32, max_lines=3):
    for size in range(hi, lo - 1, -2):
        f = e.font("SemiBold", size)
        lines = e.wrap(d, text, f, maxw)
        if len(lines) <= max_lines:
            return f, lines, int(size * 1.32)
    f = e.font("SemiBold", lo)
    return f, e.wrap(d, text, f, maxw), int(lo * 1.32)


def _arrow(d, cx, cy, length, fill, width=6):
    """Горизонтальная стрелка-подсказка «листай» — та же векторная логика,
    что и у остальных стилей (без эмодзи-шрифта, см. engine_sticker)."""
    x0, x1 = cx - length / 2, cx + length / 2
    d.line([(x0, cy), (x1, cy)], fill=fill, width=width)
    head = length * 0.30
    d.line([(x1 - head, cy - head), (x1, cy)], fill=fill, width=width, joint="curve")
    d.line([(x1 - head, cy + head), (x1, cy)], fill=fill, width=width, joint="curve")


def _brand(d, text):
    if not text:
        return
    fb = e.font("SemiBold", 20)
    d.text((M, H - 54), text.upper(), font=fb, fill=(230, 226, 222))


def _compose(photo_key, out, i, n, caption, *, tail_kind, tail_text="", brand_line=""):
    """tail_kind: "arrow" (свайп-подсказка, обычные слайды) или "word"
    (короткое кодовое слово/призыв вместо стрелки, финальный слайд).

    acc сюда намеренно НЕ доходит (см. cover/frame/cta ниже) — весь стиль
    чёрно-белый по замыслу (см. docstring модуля), заливки акцентом здесь нет
    ни у одного элемента. Параметр остаётся в публичных функциях только ради
    единой сигнатуры со всеми остальными engine_*.py (carousel_bridge и
    build_c*.py зовут все семейства одинаково: hook/caption, photo_key, acc,
    out, i, n)."""
    img = e.fit(e.PHOTOS[photo_key], H)
    probe = ImageDraw.Draw(img)

    f, lines, lh = _fit_caption(probe, caption, COL_W)
    cx = COL_RIGHT - COL_W / 2

    tail_h = ARROW_LEN if tail_kind == "arrow" else int(f.size * 0.62)
    block_h = len(lines) * lh + ARROW_GAP + tail_h
    bottom = H - BOTTOM_PAD
    top = bottom - block_h

    # верх затемнения — выше первой строки текста (с запасом), но не ниже
    # базовой доли кадра SCRIM_TOP: карусели с короткой подписью не получают
    # затемнение на весь низ кадра без нужды.
    scrim_top_y = min(int(H * SCRIM_TOP), top - SCRIM_MARGIN)
    scrim_top_y = max(0, scrim_top_y)
    img = _scrim(img, scrim_top_y)
    d = ImageDraw.Draw(img)

    e.counter(d, i, n)
    _brand(d, brand_line)

    y = top
    for line in lines:
        lw = d.textlength(line, font=f)
        d.text((cx - lw / 2, y), line, font=f, fill=WHITE)
        y += lh

    tail_cy = y + ARROW_GAP + tail_h / 2
    if tail_kind == "arrow":
        _arrow(d, cx, tail_cy, ARROW_LEN, WHITE)
    else:
        fw = e.font("Bold", int(f.size * 0.62))
        tw = d.textlength(tail_text, font=fw)
        d.text((cx - tw / 2, y + ARROW_GAP), tail_text, font=fw, fill=WHITE)

    img.save(out)
    return {"photo": photo_key, "text_bbox": (cx - COL_W / 2, top, cx + COL_W / 2, bottom)}


def cover(hook, photo_key, acc, out, i=1, n=6, brand_line=""):
    """Обложка — тот же минимальный кадр, что и остальные слайды (см. docstring
    модуля): здесь нет отдельной «крупной» композиции, кадр сам держит внимание."""
    return _compose(photo_key, out, i, n, hook, tail_kind="arrow", brand_line=brand_line)


def frame(caption, photo_key, acc, out, i=2, n=6, brand_line=""):
    """Обычный внутренний слайд серии — фото + короткая подпись + стрелка."""
    return _compose(photo_key, out, i, n, caption, tail_kind="arrow", brand_line=brand_line)


def cta(caption, word, photo_key, acc, out, i=6, n=6, brand_line=""):
    """Финал — стрелку сменяет короткое кодовое слово/призыв."""
    return _compose(photo_key, out, i, n, caption, tail_kind="word", tail_text=word,
                    brand_line=brand_line)
