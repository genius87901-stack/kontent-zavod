# -*- coding: utf-8 -*-
"""
РЕДАКЦИОННОЕ СЕМЕЙСТВО (по референсу владельца, 11.08.2026).

Четвёртый набор слайдов. Фото на весь кадр — как в engine_photo, — но всё
остальное другое, и различий достаточно, чтобы это был отдельный движок, а не
флаг у тёмного фото-стиля:

  • ЗАГОЛОВОК АНТИКВОЙ. Три существующих семейства набраны гротеском (Onest).
    Здесь заголовок — Noto Serif Bold, прописными. Это главный признак стиля:
    именно засечки дают тот «журнальный разворот», ради которого стиль и взят.
  • ТЕКСТ ВСЕГДА У НИЖНЕГО КРАЯ. engine_photo ищет спокойную зону кадра
    (pick_zone) и кладёт текст туда, где тише. Здесь блок ПРИБИТ К НИЗУ на всех
    слайдах: постоянство позиции и есть ритм разворота. Кадр под это подбирают
    так, чтобы низ был свободен, а не наоборот.
  • НАДСТРОЧКА В СКОБКАХ и МЕТКА РАЗДЕЛА в левом верхнем углу — служебная
    типографика, которой в других семействах нет вовсе.
  • БРЕНДОВАЯ СТРОКА внизу — сквозная подпись, одна на всю карусель.
  • ДВА ФОНА, ОДИН СТИЛЬ. `statement_duo()` умеет и фото (`photo_key`
    задан), и однотонную плашку (`photo_key=None`, см. `BG_FLAT`) — оба в
    одном тёплом тёмном регистре, поэтому в карусели чередуются свободно.

ПОЧЕМУ РАЗРЯДКА СВОЯ. Мелкие прописные без разрядки в этом стиле выглядят
слипшимися, а PIL трекинга не умеет: `ImageDraw.text` кладёт строку целиком по
метрикам шрифта. Поэтому `_track()` рисует посимвольно и сам добавляет зазор.
Считать ширину такой строки надо тем же кодом (`_track_w`), иначе центровка и
переносы разъезжаются — мерить одним способом, а рисовать другим нельзя.

АКЦЕНТ. Стиль монохромный и тёплый: цветного пятна в нём нет по замыслу, роль
акцента играет песочный тон надстрочки и меток. Значение живёт там же, где все
остальные — `engine4.ACCENTS["sand"]`; правило «один цвет = одна координата»
не нарушается. `checks.check_palette` к фото-слайдам не применяется (то же
исключение, что у engine_photo и engine_cream).

ГРЕЙД общий с тёмным фото-стилем (`engine_photo.grade`) — иначе кадры из банка
и стока рассыпались бы по тону. Затемнение чуть глубже: под антикву нужен более
спокойный фон, тонкие засечки на пёстром кадре пропадают.

Разметка текста — общая с движком (engine4.tokens):
  *слово*   → песочный акцент
  **слово** → жирный, тем же цветом что и база
"""
import os

from PIL import Image, ImageDraw, ImageFont

import engine4 as e

W, H, M = e.W, e.H, e.M

# ── ПАЛИТРА РЕДАКЦИОННОГО ──
CREAM = (238, 232, 222)   # заголовок и надстрочка — тёплый белый, не чистый
BODY = (203, 195, 185)    # тело
MUTED = (146, 137, 126)   # брендовая строка внизу, метка раздела
SAND = (214, 196, 170)    # песочный акцент — он же engine4.ACCENTS["sand"]

_SERIF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fonts", "NotoSerif")

#: ГРЕЙД СВОЙ, не engine_photo. Тёмный фото-стиль сажает кадр в тон, которого
#: хватает гротеску, но не антикве: её тонкие штрихи и засечки на кадре средней
#: яркости стираются, а на пёстром — рябят. Референс заметно глубже и теплее,
#: почти монохромен. Отсюда три отличия от engine_photo (DESAT .34/WARM
#: 1.08,.99,.85/GAIN .44): десатурация выше — цвет фона не должен спорить с
#: песочной надстрочкой; тёплый баланс сильнее по красному и ниже по синему;
#: общее затемнение глубже. Замерено на четырёх кадрах банка (студия, улица,
#: сцена, портрет): при GAIN .44 заголовок на светлом небе терял штрих, при .30
#: держится везде.
DESAT = 0.52
WARM = (1.10, 0.985, 0.82)
GAIN = 0.30
LIFT = (0.052, 0.036, 0.024)   # подъём чёрных в тёплое — иначе низ уходит в дыру

#: Доля высоты кадра под нижний градиент. Текст прибит к низу, читаемость даёт
#: не локальная плашка (как scrim у engine_photo), а сплошная растяжка снизу —
#: иначе на границе плашки виден шов, и «разворот» рассыпается.
SCRIM_SHARE = 0.62

#: Отступы блока: слева/справа поле M, снизу — воздух под брендовой строкой.
BOTTOM_PAD = 84
BRAND_GAP = 46

#: Воздух между заголовком и телом. На первой сборке стоял 18 — тело липло к
#: заголовку и разворот читался одним пятном; в референсе пауза между ярусами
#: заметно больше самой строки тела. 34 всё ещё читалось слипшимся на трёх
#: слайдах из восьми — держим 52.
BODY_GAP = 34


def serif(weight, size):
    """Антиква заголовков. Noto Serif — OFL, кириллица полная (fonts/NotoSerif)."""
    name = "NotoSerif-Bold.ttf" if weight == "Bold" else "NotoSerif-Regular.ttf"
    return ImageFont.truetype(os.path.join(_SERIF_DIR, name), size)


# ── РАЗРЯДКА ─────────────────────────────────────────────────────────────────
def _track_w(d, text, f, track):
    """Ширина строки, набранной с разрядкой. Мерить только этим."""
    if not text:
        return 0
    return sum(d.textlength(ch, font=f) for ch in text) + track * (len(text) - 1)


def _track(d, text, f, x, y, fill, track):
    """Нарисовать строку с разрядкой посимвольно. Возвращает конечный x."""
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + track
    return x


# ── СЛУЖЕБНАЯ ТИПОГРАФИКА ────────────────────────────────────────────────────
def label(d, text):
    """Метка раздела в левом верхнем углу («СУТЬ», «ШАГИ», «ИТОГ»).

    Ведёт читателя по развороту: на каждом слайде видно, какая это часть
    рассказа. Пустая строка — метки нет (обложке она не нужна).
    """
    if not text:
        return
    f = e.font("SemiBold", 24)
    _track(d, text.upper(), f, M, 58, MUTED, 5.0)


def brand(d, text):
    """Сквозная брендовая строка у нижнего края. Одна на всю карусель."""
    if not text:
        return
    f = e.font("SemiBold", 22)
    _track(d, text.upper(), f, M, H - BOTTOM_PAD + 4, MUTED, 4.6)


def eyebrow(d, text, x, y, acc):
    """Надстрочка в скобках над заголовком: [ РАЗНИЦА В ОДНОМ СЛОВЕ ].

    Скобки ставит движок, а не автор текста: они часть стиля, и если оставить
    их на совести сборочного скрипта — половина слайдов приедет без них.
    Возвращает высоту занятой полосы вместе с воздухом под ней.
    """
    if not text:
        return 0
    f = e.font("SemiBold", 26)
    _track(d, f"[ {text.upper()} ]", f, x, y, acc, 4.4)
    return 52


def counter(d, i, n):
    """Счётчик-кольцо в правом верхнем углу: доля прочитанного видна дугой."""
    r, cx, cy = 34, W - M - 34, 92
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(18, 16, 14, 190))
    # дуга прогресса: от 12 часов по часовой стрелке
    sweep = 360 * max(1, i) / max(1, n)
    d.arc([cx - r + 3, cy - r + 3, cx + r - 3, cy + r - 3],
          start=-90, end=-90 + sweep, fill=CREAM, width=3)
    f = e.font("SemiBold", 22)
    t = f"{i}/{n}"
    tw = d.textlength(t, font=f)
    d.text((cx - tw / 2, cy - 13), t, font=f, fill=CREAM)


def _scrim(img):
    """Сплошной градиент снизу — под ним читается любой кадр.

    Строится маской в L и накладывается одним `paste`: попиксельный проход по
    1080×837 на чистом Python заметен глазом при сборке восьми слайдов.
    """
    h = int(H * SCRIM_SHARE)
    grad = Image.linear_gradient("L").resize((W, h))        # 0 сверху → 255 снизу
    dark = Image.new("RGB", (W, h), (10, 9, 8))
    img.paste(dark, (0, H - h), grad.point(lambda v: int(v * 0.88)))
    return img


def grade(img, gain=None):
    """Любой кадр → один тёплый глубокий тон редакционного разворота."""
    import numpy as np                                   # noqa: WPS433

    g = GAIN if gain is None else gain
    a = np.asarray(img.convert("RGB")).astype(np.float32) / 255.0
    lum = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    a = a * (1 - DESAT) + lum[..., None] * DESAT
    a = a * np.array(WARM, dtype=np.float32)
    a = a * g + np.array(LIFT, dtype=np.float32)
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))


def photo(photo_key, gain=None):
    """Кадр банка/стока, обрезанный под 1080×1350 и посаженный в грейд."""
    return grade(e.fit(e.PHOTOS[photo_key], H), gain=gain)


#: Однотонная плашка-компаньон фото-слайдам (11.08.2026, разведка трёх схем
#: смысловой иерархии — владелец выбрал двухъярусный заголовок, statement_duo
#: ниже). Тон намеренно НЕ отдельный «плоский» цвет, а тот же тёплый тёмный
#: регистр, в который LIFT сажает низ любого кадра этого грейда — поэтому
#: фото-слайд и плашка внутри одной карусели читаются одной семьёй, и их
#: можно свободно чередовать, не ломая ритм разворота.
BG_FLAT = (23, 19, 16)


def _backdrop(photo_key, gain=None):
    """Фон слайда: фото в редакционном грейде + градиент, либо плашка.

    `photo_key=None` -> BG_FLAT. Один слайд-конструктор так обслуживает оба
    варианта фона без раздвоения кода на «фото-версию» и «плашка-версию».
    """
    if not photo_key:
        return Image.new("RGB", (W, H), BG_FLAT)
    return _scrim(photo(photo_key, gain=gain))


# ── УКЛАДКА ЗАГОЛОВКА ────────────────────────────────────────────────────────
def _wrap(d, text, f, maxw):
    """Перенос по словам под антикву. Ширину меряем без разрядки: заголовок
    набирается обычным `text`, разрядка — только у мелкой служебной типографики.
    """
    words, lines, cur = text.split(), [], ""
    for w in words:
        probe = (cur + " " + w).strip()
        if d.textlength(probe, font=f) <= maxw or not cur:
            cur = probe
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _wrap_rich(d, text, f, maxw, upper=False):
    """Перенос размеченного текста (`engine4.tokens`) по словам.

    Возвращает строки списками `(слово, стиль)`. Кегль внутри строки один:
    в антикве выделение держит ЦВЕТ, а не второй шрифт — смена начертания
    посреди заголовка ломает ту самую ровную засечную строку, ради которой
    стиль и взят. Поэтому ширину можно мерить одним шрифтом на всю строку.
    """
    toks = e.tokens(text.upper() if upper else text)
    sp = d.textlength(" ", font=f)
    lines, cur, cw = [], [], 0.0
    for w, st in toks:
        ww = d.textlength(w, font=f)
        add = ww if not cur else ww + sp
        if cur and cw + add > maxw:
            lines.append(cur)
            cur, cw = [(w, st)], ww
        else:
            cur.append((w, st))
            cw += add
    if cur:
        lines.append(cur)
    return lines


def _draw_rich(d, lines, f, x, y, lh, acc, base):
    """Нарисовать размеченные строки: `*слово*` — акцентом, остальное базой."""
    sp = d.textlength(" ", font=f)
    for line in lines:
        cx = x
        for w, st in line:
            d.text((cx, y), w, font=f, fill=acc if st in ("acc", "chip") else base)
            cx += d.textlength(w, font=f) + sp
        y += lh
    return y


def _fit_head_rich(d, text, maxw, hi=104, lo=52, max_lines=3):
    """`_fit_head` для размеченного заголовка: тот же подбор кегля, но строки
    возвращаются токенами, чтобы выделенные слова дожили до отрисовки."""
    for size in range(hi, lo - 1, -2):
        f = serif("Bold", size)
        lines = _wrap_rich(d, text, f, maxw, upper=True)
        if len(lines) <= max_lines:
            return f, lines, int(size * 1.08)
    f = serif("Bold", lo)
    return f, _wrap_rich(d, text, f, maxw, upper=True), int(lo * 1.08)


def _fit_head(d, text, maxw, hi=104, lo=52, max_lines=3):
    """Подобрать кегль заголовка: крупнее — лучше, но не больше max_lines строк.

    Фиксированный кегль тут не годится по той же причине, что и в остальных
    семействах: длина заголовка от слайда к слайду разная, а вес пятна на
    развороте должен оставаться одним.
    """
    for size in range(hi, lo - 1, -2):
        f = serif("Bold", size)
        lines = _wrap(d, text.upper(), f, maxw)
        if len(lines) <= max_lines:
            return f, lines, int(size * 1.08)
    f = serif("Bold", lo)
    return f, _wrap(d, text.upper(), f, maxw), int(lo * 1.08)


def _compose(photo_key, out, i, n, *, head, eyebrow_text="", body="",
             section="", brand_line="", acc=SAND, gain=None, show_counter=True):
    """Общая укладка всех слайдов семейства: кадр → градиент → низ кадра вверх.

    Блок собирается снизу вверх (брендовая строка → тело → заголовок →
    надстрочка), потому что прибит именно низ. Считать сверху пришлось бы
    заранее знать полную высоту — а она зависит от подобранного кегля.
    """
    img = photo(photo_key, gain=gain)
    img = _scrim(img)
    d = ImageDraw.Draw(img, "RGBA")

    bw = W - 2 * M
    fh, lines, lh = _fit_head_rich(d, head, bw)

    fb = e.font("Regular", 30)
    body_lines = _wrap_rich(d, body, fb, bw) if body else []
    blh = int(30 * 1.46)

    y = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0)
    if body_lines:
        y -= len(body_lines) * blh
        by = y
        y -= BODY_GAP
    y -= len(lines) * lh
    hy = y

    _draw_rich(d, lines, fh, M, hy, lh, acc, CREAM)
    if body_lines:
        _draw_rich(d, body_lines, fb, M, by, blh, acc, BODY)

    eyebrow(d, eyebrow_text, M, hy - 52, acc)
    label(d, section)
    brand(d, brand_line)
    if show_counter:
        counter(d, i, n)

    # bbox всего текстового блока (надстрочка..низ) — нужен check_cover_face,
    # чтобы обложка этого семейства проверялась на текст-по-лицу, как и в
    # остальных трёх. eyebrow занимает 52px над hy, только когда она задана.
    top = hy - (52 if eyebrow_text else 0)
    bottom = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0) + 4

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top, W - M, bottom)}


# ── СЛАЙДЫ ───────────────────────────────────────────────────────────────────
def cover(hook, sub, photo_key, acc, out, i=1, n=8, brand_line="", gain=None):
    """Обложка: без счётчика и без метки раздела — это вход в разворот."""
    return _compose(photo_key, out, i, n, head=hook, body=sub,
                    brand_line=brand_line, acc=acc, gain=gain,
                    show_counter=False)


def statement(title, body, photo_key, acc, out, i=2, n=8, section="СУТЬ",
              eyebrow_text="", brand_line=""):
    """Одна крупная мысль на кадре — основной слайд стиля."""
    return _compose(photo_key, out, i, n, head=title, body=body,
                    eyebrow_text=eyebrow_text, section=section,
                    brand_line=brand_line, acc=acc)


def statement_duo(head_a, head_b, body="", photo_key=None, acc=SAND, out=None,
                   i=2, n=8, section="СУТЬ", eyebrow_text="", brand_line="",
                   gain=None, show_counter=True):
    """Двухъярусный заголовок (выбор владельца, 11.08.2026, из трёх схем
    смысловой иерархии, показанных на реальном материале книги Валерии).

    Два яруса, а не один общий блок, потому что размер и цвет держат РАЗНЫЕ
    роли и не должны падать на одну и ту же строку:
      • `head_a` — крупный кегль, нейтральный CREAM: вес несёт РАЗМЕР;
      • `head_b` — кегль меньше, целиком акцентным цветом: вес несёт ЦВЕТ.
    Удар и контрудар в две реплики, а не одна мысль с подсветкой слова внутри.

    Оба яруса подбирают кегль независимо (`_fit_head` дважды) — длина текста в
    каждом заранее не известна. Из-за этого возможен переворот иерархии: если
    `head_a` длинный, автоподбор ужмёт его сильнее, чем короткий `head_b`, и
    «крупный» ярус случайно окажется мельче «мелкого». Это ловится и
    подрезается ниже — иначе на редком сочетании длин конкретной темы
    иерархия слайда молча ломается, а разведка её не поймала (там оба текста
    были короткими и совпадение размеров не всплыло).

    `photo_key=None` -> плашка (`_backdrop`): один и тот же слайд работает и
    на фото, и на однотонном фоне — в карусели можно чередовать оба, не меняя
    стиль текста.
    """
    if out is None:
        raise ValueError("statement_duo: нужен путь out")

    img = _backdrop(photo_key, gain=gain)
    d = ImageDraw.Draw(img, "RGBA")
    bw = W - 2 * M

    fa, lines_a, lh_a = _fit_head_rich(d, head_a, bw, hi=88, lo=48, max_lines=2)
    f2, lines_b, lh_b = _fit_head_rich(d, head_b, bw, hi=64, lo=38, max_lines=2)

    # Страховка иерархии: ярус B обязан остаться мельче яруса A. Автоподбор
    # это не гарантирует сам по себе (см. докстрока) — при столкновении
    # пересчитываем B под потолок «на 16pt меньше A», не ниже его же lo.
    if f2.size >= fa.size:
        forced = max(38, fa.size - 16)
        f2 = serif("Bold", forced)
        lines_b = _wrap_rich(d, head_b, f2, bw, upper=True)
        lh_b = int(forced * 1.16)

    fbody = e.font("Regular", 30)
    body_lines = _wrap_rich(d, body, fbody, bw) if body else []
    blh = int(30 * 1.46)

    # Укладка снизу вверх, как и весь остальной семейства: бренд-строка →
    # тело → ярус B → ярус A → надстрочка.
    y = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0)
    if body_lines:
        y -= len(body_lines) * blh
        by = y
        y -= BODY_GAP
    y -= len(lines_b) * lh_b + 14
    b2y = y
    y -= len(lines_a) * lh_a
    hy = y

    _draw_rich(d, lines_a, fa, M, hy, lh_a, acc, CREAM)
    _draw_rich(d, lines_b, f2, M, b2y, lh_b, acc, acc)
    if body_lines:
        _draw_rich(d, body_lines, fbody, M, by, blh, acc, BODY)

    eyebrow(d, eyebrow_text, M, hy - 52, acc)
    label(d, section)
    brand(d, brand_line)
    if show_counter:
        counter(d, i, n)

    top = hy - (52 if eyebrow_text else 0)
    bottom = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0) + 4

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top, W - M, bottom)}


def bullets(title, items, photo_key, acc, out, i=3, n=8, section="СПИСОК",
            brand_line=""):
    """Список: пункты набираются гротеском — антиква оставлена заголовку.

    Смешивать засечки и гротеск в одном блоке нельзя, а набирать список
    антиквой — значит потерять её вес: она перестаёт быть голосом заголовка.
    """
    img = photo(photo_key)
    img = _scrim(img)
    d = ImageDraw.Draw(img, "RGBA")

    bw = W - 2 * M
    fh, lines, lh = _fit_head(d, title, bw, hi=84, max_lines=2)
    fi = e.font("Regular", 31)
    ilh = int(31 * 1.52)

    wrapped = []
    for it in items:
        wrapped.extend(_wrap(d, it, fi, bw - 44) or [""])

    y = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0) - len(wrapped) * ilh - 26
    hy = y - len(lines) * lh
    for k, line in enumerate(lines):
        d.text((M, hy + k * lh), line, font=fh, fill=CREAM)

    yy = y
    for it in items:
        for k, line in enumerate(_wrap(d, it, fi, bw - 44) or [""]):
            if k == 0:
                d.rectangle([M, yy + 16, M + 20, yy + 20], fill=acc)
            d.text((M + 44, yy), line, font=fi, fill=BODY)
            yy += ilh

    label(d, section)
    brand(d, brand_line)
    counter(d, i, n)
    img.save(out)
    bottom = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0) + 4
    return {"photo": photo_key, "text_bbox": (M, hy, W - M, bottom)}


def prompt_card(title, prompt_text, note, acc, out, i=3, n=8, section="ПРОМТ",
                brand_line="", photo_key=None):
    """Сам промт в песочной рамке — центральный слайд рубрики «ПОЛЬЗА».

    Рамка, а не просто абзац: читатель должен с одного взгляда понять, что
    внутри — текст для копирования, а не рассуждение. По умолчанию идёт на
    плашке (`photo_key=None`): промт длинный, и кадр под ним превращает
    полезное в нечитаемое — фото у этого слайда допустимо только очень
    спокойное.

    Плейсхолдеры базы промтов набраны в квадратных скобках; они попадают в
    акцент через ту же разметку, что и заголовки, — сборочный скрипт пишет
    `*[вставь текст]*`, движок красит.
    """
    PAD = 34
    img = _backdrop(photo_key)
    d = ImageDraw.Draw(img, "RGBA")

    bw = W - 2 * M
    fh, lines, lh = _fit_head_rich(d, title, bw, hi=72, lo=44, max_lines=2)
    fp = e.font("Regular", 31)
    plh = int(31 * 1.52)
    p_lines = _wrap_rich(d, prompt_text, fp, bw - 2 * PAD)
    box_h = len(p_lines) * plh + 2 * PAD

    fn = e.font("Regular", 27)
    n_lines = _wrap_rich(d, note, fn, bw) if note else []
    nlh = int(27 * 1.46)

    y = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0)
    if n_lines:
        y -= len(n_lines) * nlh
        ny = y
        y -= 26
    y -= box_h
    boxy = y
    y -= BODY_GAP
    y -= len(lines) * lh
    hy = y

    _draw_rich(d, lines, fh, M, hy, lh, acc, CREAM)
    d.rounded_rectangle([M, boxy, W - M, boxy + box_h], 10, outline=acc, width=2)
    _draw_rich(d, p_lines, fp, M + PAD, boxy + PAD, plh, acc, CREAM)
    if n_lines:
        _draw_rich(d, n_lines, fn, M, ny, nlh, acc, BODY)

    label(d, section)
    brand(d, brand_line)
    counter(d, i, n)
    img.save(out)
    bottom = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0) + 4
    return {"photo": photo_key, "text_bbox": (M, hy, W - M, bottom)}


def steps(title, items, photo_key, acc, out, i=5, n=8, section="ШАГИ",
          brand_line=""):
    """Пошаговые действия: крупный номер антиквой + шаг гротеском.

    Номер набран той же засечной, что и заголовки, и окрашен акцентом — слайд
    читается как инструкция ещё до чтения текста. Сам шаг остаётся гротеском по
    правилу семейства (см. `bullets`): антиква — голос заголовка, и если ею
    набрать ещё и пункты, вес заголовка растворяется.

    Номер вынесен в свою колонку шириной NUMW, а не приписан к строке: при
    переносе второй строки пункта текст должен вставать под текст, а не под
    цифру, иначе колонка «съезжает» и лесенка перестаёт читаться.
    """
    NUMW = 78
    img = _backdrop(photo_key)
    d = ImageDraw.Draw(img, "RGBA")

    bw = W - 2 * M
    fh, lines, lh = _fit_head_rich(d, title, bw, hi=80, lo=46, max_lines=2)
    fn = serif("Bold", 54)
    fi = e.font("Regular", 31)
    ilh = int(31 * 1.52)
    STEP_GAP = 22

    wrapped = [_wrap_rich(d, it, fi, bw - NUMW) or [[]] for it in items]
    block_h = sum(len(w) * ilh + STEP_GAP for w in wrapped) - STEP_GAP

    y = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0) - block_h
    hy = y - 30 - len(lines) * lh
    _draw_rich(d, lines, fh, M, hy, lh, acc, CREAM)

    yy = y
    for k, w in enumerate(wrapped, 1):
        d.text((M, yy - 6), str(k), font=fn, fill=acc)
        _draw_rich(d, w, fi, M + NUMW, yy, ilh, acc, BODY)
        yy += len(w) * ilh + STEP_GAP

    label(d, section)
    brand(d, brand_line)
    counter(d, i, n)
    img.save(out)
    bottom = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0) + 4
    return {"photo": photo_key, "text_bbox": (M, hy, W - M, bottom)}


def bignum(num, title, body, photo_key, acc, out, i=4, n=8, section="ЦИФРА",
           brand_line=""):
    """Крупная цифра антиквой + пояснение. Цифра — главный вес слайда."""
    img = photo(photo_key)
    img = _scrim(img)
    d = ImageDraw.Draw(img, "RGBA")

    bw = W - 2 * M
    fn = serif("Bold", 210)
    ft, tl, tlh = _fit_head(d, title, bw, hi=72, lo=44, max_lines=2)
    fb = e.font("Regular", 30)
    bl = _wrap(d, body, fb, bw) if body else []
    blh = int(30 * 1.46)

    y = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0)
    if bl:
        y -= len(bl) * blh
        by = y
        y -= BODY_GAP
    y -= len(tl) * tlh
    ty = y
    ny = ty - 232

    d.text((M, ny), str(num), font=fn, fill=acc)
    for k, line in enumerate(tl):
        d.text((M, ty + k * tlh), line, font=ft, fill=CREAM)
    if bl:
        for k, line in enumerate(bl):
            d.text((M, by + k * blh), line, font=fb, fill=BODY)

    label(d, section)
    brand(d, brand_line)
    counter(d, i, n)
    img.save(out)
    bottom = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0) + 4
    return {"photo": photo_key, "text_bbox": (M, ny, W - M, bottom)}


def cta(title, sub, word, photo_key, acc, out, i=8, n=8, brand_line=""):
    """Финал: призыв + кодовое слово в песочной рамке.

    Рамка, а не заливка: заливка песочным на тёплом тёмном кадре читается как
    ещё одно пятно света и спорит с лицом. Обводка держит тот же тон, но не
    тянет на себя взгляд.
    """
    img = photo(photo_key)
    img = _scrim(img)
    d = ImageDraw.Draw(img, "RGBA")

    bw = W - 2 * M
    fh, lines, lh = _fit_head(d, title, bw, hi=84, max_lines=3)
    fs = e.font("Regular", 30)
    sl = _wrap(d, sub, fs, bw) if sub else []
    slh = int(30 * 1.46)

    fw = e.font("SemiBold", 34)
    ww = _track_w(d, word.upper(), fw, 6.0)
    box_h = 78

    y = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0) - box_h - 26
    if sl:
        y -= len(sl) * slh
        sy = y
        y -= BODY_GAP
    y -= len(lines) * lh
    hy = y

    for k, line in enumerate(lines):
        d.text((M, hy + k * lh), line, font=fh, fill=CREAM)
    if sl:
        for k, line in enumerate(sl):
            d.text((M, sy + k * slh), line, font=fs, fill=BODY)

    bx = H - BOTTOM_PAD - (BRAND_GAP if brand_line else 0) - box_h
    d.rounded_rectangle([M, bx, M + ww + 68, bx + box_h - 8], 6,
                        outline=acc, width=2)
    _track(d, word.upper(), fw, M + 34, bx + 16, acc, 6.0)

    brand(d, brand_line)
    counter(d, i, n)
    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, hy, M + ww + 68, bx + box_h - 8)}


# ═══ РЕГИСТР «РЕДАКТУРА» (стиль 01 из библиотеки 18, 14.08.2026) ═══════════
# Обложка остаётся как есть — тёмное фото+грейд (cover() выше), это уже
# утверждённый и работающий вид, его не трогаем. Референс (карточка 01,
# karusel-styles-library.html) для ВНУТРЕННИХ развортов зовёт другой,
# СВЕТЛЫЙ регистр — бумажный фон, буквица, пул-квота, колофон — это другая
# половина того же журнального стиля, не замена тёмной. Три новые функции
# ниже её и дают: drop_intro (буквица+лид+квота), pull_quote_slide (слайд-
# цитата), colophon_cta (финал). У светлого регистра свой набор мелкой
# типографики (_light_*) вместо label/brand/eyebrow — те зашиты под тёмный
# фон (CREAM/MUTED) и на бумаге нечитаемы.
#
# ШРИФТ. Референс просит Playfair Display на заголовках всего стиля;
# существующую тёмную обложку/развороты на NotoSerif не мигрируем (живой
# фирменный вид, его выбрала Валерия 11.08 — трогать без её отдельного ок
# нельзя), Playfair Display используется только в новых светлых функциях,
# где шрифта раньше не было вовсе — это не конфликт, а введение через
# новый материал.
#
# АКЦЕНТ. Референс рисует рыжий #B4472B — в движке остаётся SAND
# (engine4.ACCENTS["sand"]), тот же, что и у тёмной обложки: заводить
# отдельный глобальный акцент ради одной половины одного стиля непропорц
# ионально (задело бы ACCENT_STYLE/ротацию/test_carousel_style.py по всему
# заводу). SAND для мелкого текста на бумаге дополнительно притемнён
# (LSAND_TEXT) — сырой SAND тонирован под тёмный фон и на кремовом почти не
# читается; для checks.check_palette это не нарушение: у SAND (214,196,170)
# насыщенность 0.21 — уже ниже порога S_MIN=0.25, из-под сети проверки цвета
# он и так выпадает, затемнённая версия ещё темнее и ещё менее насыщена.
LCREAM = (246, 243, 236)    # тёплая бумага — #F6F3EC референса
LINK_LIGHT = (30, 27, 22)   # почти чёрный текст на бумаге — #14110D референса
LMUTED = (150, 143, 130)    # служебный серый (счётчик, бренд-строка)
LSAND_TEXT = (113, 103, 89)  # SAND, притемнённый для чтения на светлом

_DISPLAY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fonts", "PlayfairDisplay")


def display(weight, size):
    """Playfair Display — дисплейный сериф светлого регистра «Редактура».
    Только Bold/Black довендорены (без Italic) — курсив цитаты имитируется
    уменьшенным кеглем + приглушённым цветом, не наклоном."""
    name = "PlayfairDisplay-Black.ttf" if weight == "Black" else "PlayfairDisplay-Bold.ttf"
    return ImageFont.truetype(os.path.join(_DISPLAY_DIR, name), size)


def _light_label(d, text, x, y):
    if not text:
        return
    f = e.font("SemiBold", 24)
    _track(d, text.upper(), f, x, y, LSAND_TEXT, 5.0)


def _light_counter(d, i, n):
    f = e.font("SemiBold", 22)
    t = f"{i}/{n}"
    tw = d.textlength(t, font=f)
    d.text((W - M - tw, 58), t, font=f, fill=LMUTED)


def _light_brand(d, text):
    if not text:
        return
    f = e.font("SemiBold", 22)
    _track(d, text.upper(), f, M, H - 80, LMUTED, 4.6)


def _light_rule(d, x, y, w, fill=LSAND_TEXT):
    d.rectangle([x, y, x + w, y + 1], fill=fill)


def drop_intro(lead_letter, body, quote, acc, out, i=2, n=8, section="",
               brand_line=""):
    """«Буквица + лид + пул-квота» — второй архетип «Редактуры» (слайд-
    разворот референса: крупная буквица, тело, тонкая линейка, цитата).

    lead_letter — буква буквицы отдельным параметром, а не первый символ
    body: сборочный скрипт формулирует текст свободно (падеж/регистр буквицы
    не обязаны буквально совпадать с началом строки).
    """
    img = Image.new("RGB", (W, H), LCREAM)
    d = ImageDraw.Draw(img)
    bw = W - 2 * M

    fcap = display("Black", 140)
    fbody = e.font("Regular", 32)
    blh = int(32 * 1.5)

    # Отступ под буквицу — от РЕАЛЬНОЙ ширины конкретной буквы (у «Ш»/«Ю» она
    # не та же, что у «К»), плюс воздух 40px. Хардкод здесь однажды уже дал
    # 2px просвета вместо отступа — буква и первая строка тела визуально
    # склеились.
    cap_bbox = d.textbbox((0, 0), lead_letter, font=fcap)
    indent = (cap_bbox[2] - cap_bbox[0]) + 40

    head_lines = _wrap(d, body, fbody, bw - indent)[:2]
    used = len(" ".join(head_lines).split()) if head_lines else 0
    rest = " ".join(body.split()[used:])
    rest_lines = _wrap(d, rest, fbody, bw) if rest else []

    fq = display("Bold", 38)
    q_lines = _wrap(d, quote, fq, bw - 40) if quote else []
    qlh = int(38 * 1.3)

    top = y = 190
    d.text((M, y - 8), lead_letter, font=fcap, fill=LINK_LIGHT)
    # cap_bbox[3] уже смещение НИЗА глифа от точки привязки (anchor "la") —
    # не высота, складывать с bbox[1] нельзя (та же ошибка, что с индентом:
    # спутать «высоту бокса» с «нижней границей», сложить лишний раз и
    # получить точку выше настоящего низа буквицы).
    cap_bottom = (y - 8) + cap_bbox[3]

    yy = y
    for line in head_lines:
        d.text((M + indent, yy), line, font=fbody, fill=LINK_LIGHT)
        yy += blh
    yy = max(yy, cap_bottom + 12)
    for line in rest_lines:
        d.text((M, yy), line, font=fbody, fill=LINK_LIGHT)
        yy += blh

    if q_lines:
        yy += 34
        _light_rule(d, M, yy, 64)
        yy += 26
        for line in q_lines:
            d.text((M, yy), line, font=fq, fill=LSAND_TEXT)
            yy += qlh

    _light_label(d, section, M, 58)
    _light_counter(d, i, n)
    _light_brand(d, brand_line)

    img.save(out)
    return {"text_bbox": (M, top - 8, W - M, yy)}


def pull_quote_slide(quote, author, acc, out, i=4, n=8, brand_line=""):
    """Слайд-цитата: одна мысль крупным Playfair Display по центру, автор
    мелко снизу — «цитата-акцент» в последовательности стиля «Редактура»."""
    img = Image.new("RGB", (W, H), LCREAM)
    d = ImageDraw.Draw(img)
    bw = W - 2 * M

    def fit(hi=76, lo=44, max_lines=4):
        for size in range(hi, lo - 1, -2):
            f = display("Bold", size)
            lines = _wrap(d, quote, f, bw)
            if len(lines) <= max_lines:
                return f, lines, int(size * 1.22)
        f = display("Bold", lo)
        return f, _wrap(d, quote, f, bw), int(lo * 1.22)

    f, lines, lh = fit()
    block_h = len(lines) * lh
    top = y = (H - block_h) // 2
    for line in lines:
        lw = d.textlength(line, font=f)
        d.text(((W - lw) // 2, y), line, font=f, fill=LINK_LIGHT)
        y += lh
    if author:
        fa = e.font("Regular", 26)
        at = "— " + author
        aw = d.textlength(at, font=fa)
        d.text(((W - aw) // 2, y + 20), at, font=fa, fill=LMUTED)
        y += 20 + 34

    _light_counter(d, i, n)
    _light_brand(d, brand_line)

    img.save(out)
    return {"text_bbox": (M, top, W - M, y)}


def colophon_cta(rubric, title, cta_text, acc, out, i=8, n=8, brand_line=""):
    """Финал-колофон: рубрика + крупный вывод + призыв — «CTA-колофон» в
    последовательности стиля «Редактура». Заголовок — та же NotoSerif
    антиква, что у тёмных развортов (Playfair — оформление буквицы/цитаты
    по спеке референса, не самого заголовка-вывода)."""
    img = Image.new("RGB", (W, H), LCREAM)
    d = ImageDraw.Draw(img)
    bw = W - 2 * M

    fh, lines, lh = _fit_head(d, title, bw, hi=88, lo=48, max_lines=3)
    fc = e.font("Regular", 30)
    cta_lines = _wrap(d, cta_text, fc, bw) if cta_text else []
    clh = int(30 * 1.46)

    top = y = 190
    if rubric:
        _light_label(d, rubric, M, y)
        y += 52
    for line in lines:
        d.text((M, y), line, font=fh, fill=LINK_LIGHT)
        y += lh
    y += 30
    for line in cta_lines:
        d.text((M, y), line, font=fc, fill=LSAND_TEXT)
        y += clh

    _light_rule(d, M, H - 100, 64)
    _light_counter(d, i, n)
    _light_brand(d, brand_line)

    img.save(out)
    return {"text_bbox": (M, top, W - M, y)}
