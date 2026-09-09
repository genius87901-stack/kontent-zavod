# -*- coding: utf-8 -*-
"""
engine_marker — семейство «МАРКЕР»: фото на весь кадр, заголовок залит акцентом
построчно, как маркером по бумаге.

ОТКУДА ВЗЯЛСЯ. Референс Валерии 02.09.2026 — карусель @наталья_жукова «7 вещей,
которых учили девочек с детства». Ни одно семейство завода так не делает:
`engine_white.cover_marker` заливает акцентом ОДНУ строку обложки и только её,
а здесь маркер — сквозная деталь: он несёт заголовок на КАЖДОМ слайде, и
внутренние слайды им же нумеруются.

ЧЕТЫРЕ ПРАВИЛА, КОТОРЫЕ И ЕСТЬ СТИЛЬ:

1. ФОТО НА КАЖДОМ СЛАЙДЕ. Белых и однотонных слайдов в семействе нет вовсе:
   как в ЖУРНАЛЕ после 01.09 (карусель #44), где финал собирается `photo_cta`,
   а не `cta`, иначе последний слайд выходит белым и ломает ленту.

2. ЗАГОЛОВОК — СПЛОШНОЙ ПЛАШКОЙ, СТРОКА ЗА СТРОКОЙ. Плашка идёт по ширине
   БУКВ, а не колонки: рваный правый край плашек и есть образ маркера. Ширину
   даёт `textbbox` фактической строки, а не метрики шрифта — иначе у капса
   сверху остаётся воздух подстрочных элементов и плашка выглядит надутой.

3. 🔴 БУКВА НА ПЛАШКЕ — БЕЛАЯ, А НЕ INK. Это отступление от общего закона
   завода («акцент — только заливка, текст поверх — тёмный INK»), и оно
   куплено замером, а не вкусом: на red (228,16,43) белый даёт контраст 4.76
   при норме 4.5, а INK — 3.77, то есть НЕ проходит. Поэтому цвет буквы здесь
   не назначен, а ВЫЧИСЛЯЕТСЯ (`ink_on`): берётся тот из двух, что даёт больше
   контраста к акценту. Возьмут семейство с жёлтым `flash` — буква сама станет
   тёмной. Зашей я белый константой, стиль сломался бы молча на первом же
   светлом акценте.

4. ПОДПИСЬ ВНИЗУ — ЧАСТЬ РАСКЛАДКИ, А НЕ УКРАШЕНИЕ. Тонкая линия, имя
   вразрядку слева, шеврон «листай» справа. Держит нижний край кадра и говорит
   зрителю, что слайд не последний. На обложке подписи НЕТ: там кадр должен
   бить в лицо целиком (так и в референсе).

СЧЁТЧИК. На обложке — тёмный, на внутренних — акцентный. Тоже из референса и
тоже осмысленно: на обложке акцент уже занят заголовком, второе красное пятно
рядом спорит с ним за взгляд.

ШРИФТЫ — только Onest из `fonts/`, ничего нового в коробку не едет.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

import engine4 as e

W, H, M = e.W, e.H, e.M
font = e.font
WHITE = e.WHITE
INK = e.INK
COLW = W - 2 * M

# ── ПЛАШКА-МАРКЕР ──
# Поля буквы внутри заливки. Боковое поле заметно больше вертикального: так
# плашка читается мазком маркера, а не кнопкой.
PAD_X, PAD_T, PAD_B = 18, 10, 12
RADIUS = 6                     # почти прямой угол: скругление тут — не образ
LINE_GAP = 8                   # просвет между плашками соседних строк

# ── ЦВЕТА ТЕЛА ──
BODY_C = (238, 236, 234)       # не чистый белый: на фото он звенит
RULE_C = (226, 223, 220)       # линия футера
FOOT_C = (232, 229, 226)

# ── КЕГЛИ ──
TS_COVER, TS_INNER = 62, 50    # заголовок в плашке
TS_MIN = 34                    # ниже не опускаемся: плашка станет полоской
BODY_TS, BODY_LH = 32, 44
SUB_TS, SUB_LH = 30, 42
FOOT_TS = 30                   # подпись: было 22, укрупнено 02.09.2026

# ── ГРАНИЦЫ ТЕКСТОВОГО БЛОКА ──
# Блок живёт между этими двумя чертами и никогда их не переступает: сверху
# место под счётчик, снизу — полоса подписи.
TOP_Y = 132
FOOT_ZONE = 190

# ── ЗАТЕМНЕНИЕ ──
# Кадр гасится трижды: ровным полом по всему слайду, градиентом под текстовым
# блоком и узкой подложкой под подписью. Пол поднят с 0.10 до 0.17 по просьбе
# Валерии 02.09.2026 — на светлых кадрах подпись читалась на пределе.
BASE_DARK = 0.17
VEIL_MAX = 205


# ═══════════════════ БАЗА ═══════════════════

def src_path(photo: str) -> str:
    """Путь к кадру. Принимает И ключ фотобанка, И путь к файлу — как
    engine_love: банк сначала, файл потом (ключ короче и не должен случайно
    совпасть с именем файла в текущем каталоге)."""
    if photo in getattr(e, "PHOTOS", {}):
        return e.PHOTOS[photo]
    return photo


def ink_on(acc):
    """Цвет буквы на заливке акцента: тот из двух, что читается. См. правило 3
    в шапке — это единственное место, где решается вопрос, и оно замерное."""
    return WHITE if e.contrast(WHITE, acc) >= e.contrast(INK, acc) else INK


def _veil(img, y0, y1, top=True):
    """Градиент под текстовым блоком. `top=True` — гуще у верхнего края кадра
    (внутренние слайды), иначе у нижнего (обложка)."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    span = max(1, y1 - y0)
    for yy in range(max(0, y0), min(H, y1)):
        k = (y1 - yy) / span if top else (yy - y0) / span
        od.line([(0, yy), (W, yy)], fill=(6, 5, 5, int(VEIL_MAX * min(1.0, k))))
    # за пределами градиента держим сплошную часть, иначе на длинном заголовке
    # верх кадра окажется светлее середины и текст «поплывёт».
    if top and y0 > 0:
        od.rectangle([0, 0, W, y0], fill=(6, 5, 5, VEIL_MAX))
    if not top and y1 < H:
        od.rectangle([0, y1, W, H], fill=(6, 5, 5, VEIL_MAX))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def _frame(photo):
    return e.fit(src_path(photo), H, dark=BASE_DARK).convert("RGB")


def _counter(d, i, n, acc=None):
    """Счётчик N/n в правом верхнем углу. acc=None — тёмная плашка (обложка)."""
    f = font("Bold", 24)
    t = f"{i}/{n}"
    tw = d.textlength(t, font=f)
    x, y = W - M - tw, 50
    fill = acc if acc else (0, 0, 0)
    d.rounded_rectangle([x - 18, y - 10, x + tw + 18, y + 34], 20, fill=fill)
    d.text((x, y), t, font=f, fill=ink_on(acc) if acc else WHITE)


def _track_text(d, x, y, text, f, fill, track=3.0):
    """Текст вразрядку. Pillow разрядки не умеет — печатаем по букве."""
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + track
    return x


def foot_dim(img):
    """Подложка под футер: узкий градиент по нижнему краю.

    ЗАЧЕМ. Текстовый блок сверху лежит на своём затемнении, а подпись внизу —
    на голом кадре. На тёмном фото она читалась, на светлом (кремовая стена,
    витрина, снег) линия и имя пропадали начисто — слайд выходил без подписи,
    и никто бы этого не заметил до публикации. Градиент узкий и слабый: он
    держит подпись, но не отрезает низ кадра чёрной полосой.

    ЗАМЕР 02.09.2026 на самом светлом кадре демо (кремовая стена бутика),
    средняя яркость полосы под подписью, БЕЗ текста в кадре:
        без подложки  116.9 -> контраст подписи 3.68  (норма 4.5 НЕ пройдена)
        с подложкой    84.0 -> контраст подписи 6.04
    """
    top = H - 260
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for yy in range(top, H):
        k = (yy - top) / (H - top)
        od.line([(0, yy), (W, yy)], fill=(6, 5, 5, int(175 * k * k)))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def footer(d, author="КОНТЕНТЗАВОД", last=False):
    """Линия + имя вразрядку + шеврон «листай». На последнем слайде шеврона
    нет: листать больше некуда, а стрелка в никуда — обещание, которого
    раскладка не держит."""
    fy = H - 132
    d.line([(M, fy), (W - M, fy)], fill=RULE_C, width=2)
    fn = font("Bold", FOOT_TS)
    _track_text(d, M, fy + 30, author.upper(), fn, FOOT_C, track=3.8)
    if not last:
        fc = font("Black", 46)
        t = "»"
        d.text((W - M - d.textlength(t, font=fc), fy + 16), t, font=fc, fill=WHITE)


# ═══════════════════ МАРКЕР ═══════════════════
#
# ПЛАШКА ОДНА НА ВЕСЬ ЗАГОЛОВОК. Правка Валерии 02.09.2026 по референсу: сперва
# я заливал каждую строку отдельно, и заголовок распадался на куски с рваным
# просветом между ними — в референсе же это ОДНО пятно, внутри которого текст
# просто переносится. Ширина пятна — по самой длинной строке; короткие строки
# не поджимают заливку, иначе снова получится лесенка.

def _marker_rows(d, text, f):
    """Строки заголовка + геометрия каждой: (строка, ширина, левый вынос)."""
    rows = []
    for line in e.wrap(d, text, f, COLW - 2 * PAD_X):
        x0, _y0, x1, _y1 = d.textbbox((0, 0), line, font=f)
        rows.append((line, x1 - x0, x0))
    return rows


def _cap_h(d, f):
    """Высота капса ЭТОГО кегля. Берём по образцовой строке, а не по метрикам
    шрифта: метрики считают подстрочные элементы, которых в капсе нет, и плашка
    выходит надутой сверху и снизу."""
    _x0, y0, _x1, y1 = d.textbbox((0, 0), "НХ", font=f)
    return y1 - y0, y0


def _marker_size(d, rows, f):
    """Размер единой плашки: (ширина, высота, шаг строки, верхний вынос)."""
    cap, top = _cap_h(d, f)
    lh = int(cap * 1.34)
    w = max((lw for _l, lw, _x in rows), default=0) + 2 * PAD_X
    h = cap + (len(rows) - 1) * lh + PAD_T + PAD_B
    return w, h, lh, top


def _marker_fit(d, text, ts_max, max_h):
    """Кегль заголовка под высоту. Плашка обязана остаться плашкой: ниже
    TS_MIN не опускаемся, а отдаём что есть — пусть лучше вылезет в отладке,
    чем молча превратится в полоску."""
    ts = ts_max
    while ts > TS_MIN:
        f = font("Black", ts)
        rows = _marker_rows(d, text, f)
        if _marker_size(d, rows, f)[1] <= max_h:
            return f, rows
        ts -= 2
    f = font("Black", TS_MIN)
    return f, _marker_rows(d, text, f)


def draw_plate(d, rows, f, x, y, acc):
    """ОДНА плашка на весь заголовок, текст переносится внутри неё.
    Возвращает y под плашкой."""
    if not rows:
        return y
    pw, ph, lh, top = _marker_size(d, rows, f)
    d.rounded_rectangle([x, y, x + pw, y + ph], RADIUS, fill=acc)
    col = ink_on(acc)
    ty = y + PAD_T
    for line, _lw, x0 in rows:
        d.text((x + PAD_X - x0, ty - top), line, font=f, fill=col)
        ty += lh
    return y + ph


def body_lines(d, text, f, fbold):
    """Разметка тела с УВАЖЕНИЕМ К ПУСТОЙ СТРОКЕ.

    `e.wrap_rich` режет текст по переносу строки, но пустую часть отдаёт как
    НИЧТО — двойной перенос в исходнике просто исчезал, и два абзаца слипались
    в один кусок. Здесь пустая часть превращается в пустую строку макета: абзац
    в тексте владельца обязан остаться абзацем на слайде.
    """
    out = []
    for part in (text or "").splitlines():
        if not part.strip():
            if out:
                out.append([])
            continue
        out.extend(e.wrap_rich(d, part, f, COLW, fbold))
    while out and not out[-1]:
        out.pop()
    return out


# ═══════════════════ ЛИЦО РЕШАЕТ, ГДЕ ТЕКСТ ═══════════════════

def faces_on(img):
    """Лица на готовом кадре 1080x1350. Детектор — ОДИН НА ПРОЕКТ
    (`checks.face_boxes_img`): тот же, что обходит лицо в субтитрах Reels и в
    семействе «ЛЮБОВЬ». Заводить здесь второй — значит развести «текст обходит
    лицо» на два разных смысла.

    Осечка детектора НЕ роняет сборку: нет лиц — раскладка встаёт по умолчанию,
    как и раньше. Молчание детектора хуже неверного выбора стороны только тем,
    что о нём никто не узнает, — поэтому и возвращаем пустой список честно.
    """
    try:
        import checks
        return checks.face_boxes_img(img)
    except Exception:                                       # noqa: BLE001
        return []


def _overlap(y0, y1, faces):
    """Сколько пикселей по вертикали блок [y0,y1) отъедает у лиц."""
    return sum(max(0, min(y1, fy1) - max(y0, fy0)) for _x0, fy0, _x1, fy1 in faces)


def pick_side(img, block_h, faces, prefer="top"):
    """Куда встаёт текстовый блок: наверх или вниз. Возвращает ("top"|"bottom", y).

    ПРАВИЛО ВАЛЕРИИ 02.09.2026: текст не ложится на лицо. Двигать его вбок в
    этом семействе нельзя — плашка идёт от поля до поля по своей природе, —
    поэтому свобода ровно одна: верх кадра или низ. Сторона `prefer` это
    ПРЕДПОЧТЕНИЕ (внутренние слайды сверху, обложка снизу — так в референсе),
    а «не на лицо» — ЗАПРЕТ, и запрет сильнее предпочтения.

    Обе стороны заняты лицами — берём ту, где перекрытие меньше: выбор из двух
    плохих всё равно лучше слепого следования предпочтению.
    """
    y_top = TOP_Y
    y_bot = H - FOOT_ZONE - block_h
    if y_bot < y_top:                       # блок выше кадра — низа просто нет
        return "top", y_top
    if not faces:
        return (prefer, y_top if prefer == "top" else y_bot)

    ov_top = _overlap(y_top, y_top + block_h, faces)
    ov_bot = _overlap(y_bot, y_bot + block_h, faces)
    want = (prefer, y_top if prefer == "top" else y_bot)
    other = ("bottom", y_bot) if prefer == "top" else ("top", y_top)
    ov_want = ov_top if prefer == "top" else ov_bot
    ov_other = ov_bot if prefer == "top" else ov_top

    if ov_want == 0:
        return want
    if ov_other == 0:
        return other
    return want if ov_want <= ov_other else other


def _veil_side(img, side, y0, y1):
    """Затемнение под блоком со стороны, которую выбрало лицо."""
    if side == "top":
        return _veil(img, 0, y1 + 160, top=True)
    return _veil(img, y0 - 160, H, top=False)


# ═══════════════════ РАСКЛАДКИ ═══════════════════

def cover(hook, tail, sub, photo, acc, out, i=1, n=8):
    """Обложка: заголовок маркером, продолжение белым капсом, тонкая строка
    приглашения.

    Блок ПО УМОЛЧАНИЮ внизу (так в референсе), но уходит наверх, если внизу
    лицо, — см. pick_side.
    """
    img = _frame(photo)
    d = ImageDraw.Draw(img)

    f, rows = _marker_fit(d, hook.upper(), TS_COVER, int(H * 0.34))
    ft = font("Black", 44)
    fs = font("Regular", SUB_TS)
    fsb = font("SemiBold", SUB_TS)

    tail_lines = e.wrap(d, tail.upper(), ft, COLW) if tail else []
    sub_lines = e.wrap_rich(d, sub, fs, COLW, fsb) if sub else []

    block = _marker_size(d, rows, f)[1]
    if tail_lines:
        block += 22 + len(tail_lines) * 54
    if sub_lines:
        block += 48 + len(sub_lines) * SUB_LH

    side, y = pick_side(img, block, faces_on(img), prefer="bottom")
    img = _veil_side(img, side, y, y + block)
    d = ImageDraw.Draw(img)

    y_top = y
    _counter(d, i, n)
    y = draw_plate(d, rows, f, M, y, acc) + 22

    for line in tail_lines:
        d.text((M + 2, y + 3), line, font=ft, fill=(0, 0, 0))
        d.text((M, y), line, font=ft, fill=WHITE)
        y += 54

    if sub_lines:
        y += 26
        e.draw_rich(d, sub_lines, fs, M, y, SUB_LH, acc, base=BODY_C, shadow=True)

    img.save(out)
    return {"text_bbox": (M, int(y_top), W - M, int(y_top + block)), "photo": photo}


def slide(num, claim, body, photo, acc, out, i=2, n=8, author="КОНТЕНТЗАВОД"):
    """Внутренний слайд: номер и утверждение маркером, разбор белым под ними,
    подпись по нижнему краю.

    num — номер пункта (int или строка). Он входит В ПЛАШКУ, а не живёт
    отдельной цифрой: в референсе счёт ведёт сам маркер.

    Блок ПО УМОЛЧАНИЮ сверху и уходит вниз, если сверху лицо (pick_side).
    """
    img = _frame(photo)
    d = ImageDraw.Draw(img)

    head = f"{num}. {claim}".strip() if num not in (None, "") else claim
    f, rows = _marker_fit(d, head.upper(), TS_INNER, int(H * 0.30))
    fb = font("Regular", BODY_TS)
    fbb = font("Bold", BODY_TS)
    lines = body_lines(d, body, fb, fbb) if body else []

    head_h = _marker_size(d, rows, f)[1]
    block = head_h + (34 + len(lines) * BODY_LH if lines else 0)

    side, y = pick_side(img, block, faces_on(img), prefer="top")
    img = _veil_side(img, side, y, y + block)
    img = foot_dim(img)
    d = ImageDraw.Draw(img)

    _counter(d, i, n, acc)
    draw_plate(d, rows, f, M, y, acc)
    if lines:
        e.draw_rich(d, lines, fb, M, y + head_h + 34, BODY_LH, acc,
                    base=BODY_C, shadow=True)
    footer(d, author)

    img.save(out)
    return {"text_bbox": (M, int(y), W - M, int(y + block)), "photo": photo}


def cta(claim, body, word, photo, acc, out, i=8, n=8, author="КОНТЕНТЗАВОД"):
    """Финал. Раскладка внутреннего слайда без номера, плюс кодовое слово
    плашкой и без шеврона: листать дальше нечего.

    ФОТО ОБЯЗАТЕЛЬНО, как и везде в семействе — белый финальный слайд ломал бы
    ленту (та же грабля, что чинилась в ЖУРНАЛЕ переходом на photo_cta).
    """
    img = _frame(photo)
    d = ImageDraw.Draw(img)

    f, rows = _marker_fit(d, claim.upper(), TS_INNER, int(H * 0.30))
    fb = font("Regular", BODY_TS)
    fbb = font("Bold", BODY_TS)
    lines = body_lines(d, body, fb, fbb) if body else []

    head_h = _marker_size(d, rows, f)[1]
    fw = font("Black", 46)
    word_h = 0
    if word:
        _wx0, wy0, _wx1, wy1 = d.textbbox((0, 0), word.upper(), font=fw)
        word_h = 34 + (wy1 - wy0) + PAD_T + PAD_B

    block = head_h + (34 + len(lines) * BODY_LH if lines else 0) + word_h

    side, y = pick_side(img, block, faces_on(img), prefer="top")
    img = _veil_side(img, side, y, y + block)
    img = foot_dim(img)
    d = ImageDraw.Draw(img)

    _counter(d, i, n, acc)
    draw_plate(d, rows, f, M, y, acc)
    cur = y + head_h
    if lines:
        cur += 34
        e.draw_rich(d, lines, fb, M, cur, BODY_LH, acc, base=BODY_C, shadow=True)
        cur += len(lines) * BODY_LH

    if word:
        t = word.upper()
        x0, ty0, x1, ty1 = d.textbbox((0, 0), t, font=fw)
        wy = cur + 34
        d.rounded_rectangle([M, wy, M + (x1 - x0) + 2 * PAD_X,
                             wy + (ty1 - ty0) + PAD_T + PAD_B], RADIUS, fill=acc)
        d.text((M + PAD_X - x0, wy + PAD_T - ty0), t, font=fw, fill=ink_on(acc))

    footer(d, author, last=True)
    img.save(out)
    return {"text_bbox": (M, int(y), W - M, int(y + block)), "photo": photo}
