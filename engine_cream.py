# -*- coding: utf-8 -*-
"""
КРЕМОВОЕ СЕМЕЙСТВО (по референсу Валерии, 02.08.2026).

Отдельный набор раскладок: тёплый КРЕМОВЫЙ фон (не белый), фото вписано
скруглённым блоком (по решению — скруглённые углы, без органичных вырезов),
тёмный графитовый заголовок, коралловый акцент и СВЕТЛО-ПЕРСИКОВЫЕ плашки-
хайлайты под мелкими фразами.

ВНИМАНИЕ — почему НЕ engine_white и почему тут два тона.
Правило завода «один цвет = одна координата» (checks.check_palette) держит
белое/тёмное семейства в одном значении акцента. Кремовый стиль по построению
двутонный: коралл — цветной ТЕКСТ ключевых слов (*слово*) + разделитель + бордюр
кода; персик (CHIP) — заливка мелких плашек (==фраза==), текст поверх — INK.
Поэтому check_palette к кремовым слайдам НЕ применяется (как к фото-слайдам):
это осознанное отклонение под отдельное семейство, а не «вторая яркость».

Разметка текста — общая с движком (engine4.tokens):
  *слово*   → коралловый текст (акцент)
  ==фраза== → персиковая плашка, тёмный текст
  **слово** → жирный, тем же цветом что и база
"""
from PIL import Image, ImageDraw
import engine4 as e
# Автокегль — общий с белым семейством: сам подбор (autofit) и цели по доле
# площади живут в engine_white, здесь только применяются. Дублировать 25 строк
# в двух файлах — верный способ поправить их в одном и забыть про второй.
from engine_white import autofit, TGT, TGT_PHOTO, CAP, BODY_MIN, BODY_MAX

W, H, M = e.W, e.H, e.M
font = e.font

# ── ПАЛИТРА КРЕМОВОГО ──
BG    = (240, 232, 220)   # тёплый кремовый фон
INK   = (43, 37, 32)      # графитовый «чёрный» заголовков/тела на плашке
BODY  = (122, 112, 102)   # тёплый серый — обычный текст тела
CHIP  = (245, 208, 186)   # светло-персиковая плашка-хайлайт
LINE  = (222, 214, 202)   # деликатная линия/подложка карточек

# коралл-акцент — единственный цветной тон (передаётся параметром acc)
CORAL = (223, 118, 86)


# ── helpers ───────────────────────────────────────────────────────────────
def _counter(d, i, n):
    """Тёмная пилюля со счётчиком в правом верхнем углу (читается и на кремовом,
    и поверх фото)."""
    f = font("Bold", 24); t = f"{i}/{n}"
    tw = d.textlength(t, font=f); x, y = W - M - tw, 50
    d.rounded_rectangle([x-18, y-10, x+tw+18, y+34], 22, fill=INK)
    d.text((x, y), t, font=f, fill=(255, 255, 255))


def dash(d, x, y, acc, w=60, h=6):
    d.rectangle([x, y, x+w, y+h], fill=acc)


def _fit_box(path, bw, bh, dark=0.0):
    """Фото, обрезанное по центру под пропорции блока bw×bh."""
    img = Image.open(path).convert("RGB")
    tr = bw / bh; iw, ih = img.size
    if iw/ih > tr:
        nw = int(ih*tr); img = img.crop(((iw-nw)//2, 0, (iw-nw)//2+nw, ih))
    else:
        nh = int(iw/tr); img = img.crop((0, 0, iw, nh))
    img = img.resize((bw, bh), Image.LANCZOS)
    if dark:
        img = Image.blend(img, Image.new("RGB", (bw, bh), (10, 8, 7)), dark)
    return img


def _photo_block(base, key, box, radius=44, dark=0.0):
    """Вклеить фото скруглённым блоком в кремовый холст."""
    x0, y0, x1, y1 = box
    bw, bh = x1-x0, y1-y0
    ph = _fit_box(e.PHOTOS[key], bw, bh, dark)
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw-1, bh-1], radius, fill=255)
    base.paste(ph, (x0, y0), mask)


def _rich(d, lines, f, x, y, lh, acc, base=INK, bg=BG):
    """Рисует размеченные строки в кремовой палитре:
    chip → персиковая плашка + INK; acc → коралловый текст; иначе → base.

    Поле плашки — общая константа e.CHIP_PAD: подложка первого чипа строки
    встаёт ровно по левому краю колонки, буква — правее на CHIP_PAD.
    bg — фон под плашкой: персик на кремовом даёт контраст 1.18 при норме 3.0,
    поэтому плашка получает волосяную обводку графитом (e.chip_edge)."""
    sp = d.textlength(" ", font=f)
    edge = e.chip_edge(CHIP, bg, ink=INK)
    for line in lines:
        cx = x + (e.CHIP_PAD if line and line[0][1] == "chip" else 0)
        for w_, st, ff in line:
            ww = d.textlength(w_, font=ff)
            if st == "chip":
                asc, desc = ff.getmetrics()
                d.rounded_rectangle([cx-e.CHIP_PAD, y-2, cx+ww+e.CHIP_PAD, y+asc+desc-8],
                                    9, fill=CHIP, **edge)
                d.text((cx, y), w_, font=ff, fill=INK)
            elif st == "acc":
                d.text((cx, y), w_, font=ff, fill=acc)
            else:
                d.text((cx, y), w_, font=ff, fill=base)
            cx += ww + sp
        y += lh
    return y


def _block_h(lines, lh):
    return len(lines) * lh


# ── A. ОБЛОЖКА / ФОТО СБОКУ + ТЕКСТОВАЯ КОЛОНКА ────────────────────────────
def photo_side(hook, sub, photo, acc, out, i=1, n=8, side="right",
               hook_px=None, sub_px=27, dark=0.04):
    """
    Референс-слайды 1/3/4: фото скруглённым блоком с одной стороны, узкая
    текстовая колонка с другой. Заголовок графитом (акцентные слова — коралл),
    тонкий коралловый разделитель, подзаголовок серым (плашки — персик).
    Возвращает {'text_bbox', 'photo'} для проверки текста на лице.

    `hook_px=None` (по умолчанию с волны 2) -> автокегль колонки. Явное число —
    прежний фиксированный режим с парой (hook_px, sub_px) как раньше: карусель
    #39 зовёт photo_side(..., hook_px=54) и собирается байт-в-байт.
    Цель здесь считается от ВЫСОТЫ КОЛОНКИ, а не от площади кадра: колонка
    узкая (356 px при side="right"), доля площади для неё не показатель.
    """
    img = Image.new("RGB", (W, H), BG)
    gap = 40
    pw = int(W * 0.50)                     # ширина фото-блока
    if side == "right":
        pbox = (W - M - pw, M, W - M, H - M)
        tx = M
    else:
        pbox = (M, M, M + pw, H - M)
        tx = W - M - pw + gap + 0  # fallback (не используется в шоу-кейсе)
        tx = pbox[2] + gap
    _photo_block(img, photo, pbox, radius=48, dark=dark)
    d = ImageDraw.Draw(img)
    _counter(d, i, n)

    tw = (pbox[0] - M - gap) if side == "right" else (W - M - tx)

    def m(s, sp=None):
        sp = sp or round(s * 0.519)                    # 27 при 52
        fh = font("Black", s); lh = int(s * 1.16)
        fs, fsb = font("Regular", sp), font("Bold", sp)
        hl = e.wrap_rich(d, hook, fh, tw)
        sl = e.wrap_rich(d, sub, fs, tw, fsb) if sub else []
        sub_lh = int(sp * 1.42)
        return dict(fh=fh, fs=fs, hl=hl, sl=sl, lh=lh, sub_lh=sub_lh,
                    block=_block_h(hl, lh) +
                          (24 + 6 + 24 + _block_h(sl, sub_lh) if sl else 0))

    c = (m(hook_px, sub_px) if hook_px
         else autofit(m, 86, 30, int((H - 2*M) * 0.62), H - 2*M))
    fh, fs = c["fh"], c["fs"]
    y = (H - c["block"]) // 2
    y_top = y
    y = _rich(d, c["hl"], fh, tx, y, c["lh"], acc, base=INK)
    if c["sl"]:
        y += 22
        dash(d, tx, y, acc, 60); y += 26
        y = _rich(d, c["sl"], fs, tx, y, c["sub_lh"], acc, base=BODY)
    img.save(out)
    return {"text_bbox": (tx, int(y_top), int(tx+tw), int(y)), "photo": photo}


# ── B. СПЛИТ: фото сверху скруглённым блоком / кремовый низ с текстом ───────
def split(title, body, photo, acc, out, i=2, n=8, cut=0.46, dark=0.04, hs=None):
    """`hs=None` -> автокегль под свободную полосу; hs=52 — прежний вид."""
    img = Image.new("RGB", (W, H), BG)
    ph_h = int(H * cut)
    _photo_block(img, photo, (M, M, W-M, ph_h), radius=44, dark=dark)
    d = ImageDraw.Draw(img)
    _counter(d, i, n)
    avail = H - 56 - (ph_h + 54)

    def m(s):
        bs = max(BODY_MIN, min(BODY_MAX, round(s*0.596)))       # 31 при 52
        fh, fb, fbold = font("Black", s), font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d, title, fh, W-2*M)
        bl = e.wrap_rich(d, body, fb, W-2*M, fbold)
        lh, blh = round(s*1.192), round(bs*1.548)
        g1, g2 = round(s*0.654), round(s*0.5)                   # 34 / 26 при 52
        return dict(fh=fh, fb=fb, hl=hl, bl=bl, lh=lh, blh=blh, g1=g1, g2=g2,
                    block=g1 + len(hl)*lh + g2 + len(bl)*blh)

    c = m(hs) if hs else autofit(m, 82, 28, int(avail*0.80), avail)
    y = top = ph_h + 54
    dash(d, M, y, acc); y += c["g1"]
    y = _rich(d, c["hl"], c["fh"], M, y, c["lh"], acc, base=INK) + c["g2"]
    _rich(d, c["bl"], c["fb"], M, y, c["blh"], acc, base=BODY)
    img.save(out)
    return {"block_h": c["block"], "hs": c["fh"].size,
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── C. QUOTE: крупная мысль на кремовом, без фото ──────────────────────────
def quote(title, body, acc, out, i=2, n=8, hs=None):
    """`hs=None` -> автокегль под TGT; hs=76 — прежний вид байт-в-байт."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _counter(d, i, n)

    def m(s):
        bs = max(BODY_MIN, min(BODY_MAX, round(s * 0.526)))     # 40 при 76
        fh, fb, fbold = font("Black", s), font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d, title, fh, W-2*M)
        bl = e.wrap_rich(d, body, fb, W-2*M, fbold)
        lh, blh, gap = round(s*1.158), round(bs*1.4), round(s*0.53)
        return dict(fh=fh, fb=fb, hl=hl, bl=bl, lh=lh, blh=blh, gap=gap,
                    block=44 + len(hl)*lh + gap + len(bl)*blh)

    c = m(hs) if hs else autofit(m, 118, 44, TGT, CAP)
    y = top = (H - c["block"])//2 - 30
    dash(d, M, y, acc, 64); y += 44
    y = _rich(d, c["hl"], c["fh"], M, y, c["lh"], acc, base=INK) + c["gap"]
    _rich(d, c["bl"], c["fb"], M, y, c["blh"], acc, base=BODY)
    img.save(out)
    return {"block_h": c["block"], "hs": c["fh"].size,
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── D. BIGNUM: крупная коралловая цифра на кремовом ────────────────────────
def bignum(pre, num, post, body, acc, out, i=5, n=8, sc=None):
    """`sc=None` -> автоподбор масштаба сетки; sc=1.0 — прежний вид."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _counter(d, i, n)

    def m(k):
        s = k / 100.0
        fp = font("Black", round(62*s)); fnum = font("Black", round(210*s))
        bs = round(34*s)
        fb, fbold = font("Regular", bs), font("Bold", bs)
        pre_l = e.wrap(d, pre, fp, W-2*M); post_l = e.wrap(d, post, fp, W-2*M)
        bl = e.wrap_rich(d, body, fb, W-2*M, fbold)
        pstep, nstep = round(72*s), round(200*s)
        g1, g2, blh = round(30*s), round(40*s), round(50*s)
        wide = d.textlength(str(num), font=fnum) > W - 2*M
        return dict(fp=fp, fnum=fnum, fb=fb, pre_l=pre_l, post_l=post_l, bl=bl,
                    pstep=pstep, nstep=nstep, g1=g1, g2=g2, blh=blh,
                    dw=round(64*s), s=s, bad=wide,
                    block=len(pre_l)*pstep + nstep + len(post_l)*pstep +
                          g1 + g2 + len(bl)*blh)

    c = m(round(sc*100)) if sc else autofit(m, 150, 60, TGT, CAP)
    y = top = (H - c["block"])//2 - 30
    for l in c["pre_l"]:
        d.text((M, y), l, font=c["fp"], fill=INK); y += c["pstep"]
    d.text((M, y), num, font=c["fnum"], fill=acc); y += c["nstep"]   # цифра — коралл
    for l in c["post_l"]:
        d.text((M, y), l, font=c["fp"], fill=INK); y += c["pstep"]
    y += c["g1"]
    dash(d, M, y, acc, c["dw"]); y += c["g2"]
    _rich(d, c["bl"], c["fb"], M, y, c["blh"], acc, base=BODY)
    img.save(out)
    return {"block_h": c["block"], "sc": c["s"],
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── E. CHECKLIST: заголовок + пункты с коралловыми буллетами ───────────────
def checklist(title, items, acc, out, i=3, n=8, hs=None):
    """`hs=None` -> автокегль (потолок поднят с 60 до 92, выбор — ближайший к
    цели, а не первый влезающий); hs=60 — прежний вид байт-в-байт."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _counter(d, i, n)
    ind = 46

    def m(s):
        fh = font("Black", s)
        bs = max(26, min(BODY_MAX, int(s * 0.56)))
        fb, fbold = font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d, title, fh, W-2*M)
        wr = [e.wrap_rich(d, it, fb, W-2*M-ind, fbold) for it in items]
        lh, blh, ig = int(s*1.16), int(bs*1.34), int(bs*0.85)
        block = 44 + len(hl)*lh + 40 + sum(len(w)*blh + ig for w in wr) - ig
        return dict(fh=fh, fb=fb, hl=hl, wr=wr, lh=lh, blh=blh, ig=ig,
                    block=block, bs=bs)

    c = m(hs) if hs else autofit(m, 92, 38, TGT, CAP)
    y = top = (H - c["block"])//2 - 10
    dash(d, M, y, acc, 64); y += 44
    y = _rich(d, c["hl"], c["fh"], M, y, c["lh"], acc, base=INK) + 40
    for w in c["wr"]:
        r = int(c["bs"] * 0.26)
        cy = y + (c["blh"] - 2*r)//2 - 2
        d.ellipse([M, cy, M+2*r, cy+2*r], fill=acc)
        _rich(d, w, c["fb"], M+ind, y, c["blh"], acc, base=INK)
        y += len(w)*c["blh"] + c["ig"]
    img.save(out)
    return {"block_h": c["block"], "hs": c["fh"].size,
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── G. ФОТО НА ВЕСЬ КАДР + ТЁМНАЯ ВУАЛЬ СНИЗУ + ТЕКСТ (утв. вариант V4) ─────
def photo_full(title, body, photo, acc, out, i=3, n=8, wash=0.12, hs=None):
    """
    Фото на весь слайд (тёплая гамма) под лёгкой кремовой вуалью для единства
    палитры; снизу — мягкая ТЁМНАЯ вуаль-градиент, на ней белый заголовок с
    коралловым акцентом (*слово*) и светлое тело. Утверждённый вид V4 —
    контрастно и читаемо, при этом кадр остаётся в тёплой кремовой гамме.

    Разметка та же: *слово* — коралл, ==фраза== — персиковая плашка.
    Возвращает {'text_bbox','photo'} для проверки текста на лице.
    """
    LIGHT = (238, 232, 226)                    # светлое тело на тёмной вуали
    base = _fit_box(e.PHOTOS[photo], W, H).convert("RGB")
    if wash:                                   # лёгкая кремовая вуаль — в гамму
        base = Image.blend(base, Image.new("RGB", (W, H), BG), wash)
    img = base.convert("RGBA")

    d0 = ImageDraw.Draw(img)

    def m(s):
        bs = max(BODY_MIN, min(BODY_MAX, round(s*0.533)))       # 32 при 60
        fh, fb, fbold = font("Black", s), font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d0, title, fh, W-2*M)
        bl = e.wrap_rich(d0, body, fb, W-2*M, fbold) if body else []
        lh, blh = round(s*1.167), round(bs*1.5)                 # 70 / 48
        g0, g1 = round(s*0.433), round(s*0.367)                 # 26 / 22
        return dict(fh=fh, fb=fb, hl=hl, bl=bl, lh=lh, blh=blh, g0=g0, g1=g1,
                    dw=s,
                    block=_block_h(hl, lh) + (g1 + _block_h(bl, blh) if bl else 0))

    # Блок прижат низом (y = H-M-block-10), потолок — чтобы верх не залез
    # выше SAFE_Y и не встал под пилюлю счётчика.
    c = m(hs) if hs else autofit(m, 90, 34, TGT_PHOTO, H - M - 10 - 130)
    fh, fb = c["fh"], c["fb"]
    hl, bl = c["hl"], c["bl"]
    lh, blh = c["lh"], c["blh"]
    block = c["block"]

    # тёмная вуаль снизу под текст
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    top = int(H*0.42)
    for yy in range(top, H):
        a = int(212 * min(1, (yy-top)/(H*0.5)))
        od.line([(0, yy), (W, yy)], fill=(20, 16, 14, min(a, 212)))
    img = Image.alpha_composite(img, ov)
    d = ImageDraw.Draw(img)
    _counter(d, i, n)

    y = H - M - block - 10
    y_top = y
    dash(d, M, y, acc, c["dw"]); y += c["g0"]
    # bg=None: здесь текст лежит на ТЁМНОЙ вуали, персиковая плашка на ней
    # различима без обводки (контраст 7+), обводка была бы грязью.
    y = _rich(d, hl, fh, M, y, lh, acc, base=(255, 255, 255), bg=None) + c["g1"]
    if bl:
        _rich(d, bl, fb, M, y, blh, acc, base=LIGHT, bg=None)
    img.convert("RGB").save(out)
    return {"text_bbox": (M, int(y_top), W-M, H-M), "photo": photo}


# ── F. CTA: центр, кодовое слово в коралловой рамке ────────────────────────
def cta(title, sub, word, acc, out, i=8, n=8, sc=None):
    """`sc=None` -> автоподбор масштаба; sc=1.0 — прежний вид байт-в-байт."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _counter(d, i, n)
    has_word = bool(word and word.strip())
    sub_ink = BODY if has_word else INK

    def m(k):
        s = k / 100.0
        fh = font("Black", round(66*s)); fw = font("Black", round(58*s))
        fs = font("Regular", round((34 if has_word else 48)*s))
        f2 = font("Bold", round(27*s))
        sub_lh = round((50 if has_word else 66)*s)
        hl = e.wrap(d, title, fh, W-2*M-40); sl = e.wrap(d, sub, fs, W-2*M-40)
        bw, bh = round(480*s), round(190*s)
        fits = (not has_word or
                (d.textlength(word, font=fw) <= bw - round(40*s) and
                 d.textlength("написать в директ", font=f2) <= bw - round(40*s)))
        return dict(fh=fh, fw=fw, fs=fs, f2=f2, hl=hl, sl=sl, sub_lh=sub_lh,
                    lh=round(78*s), g1=round(44*s), g2=round(44*s),
                    g3=round(70*s), bw=bw, bh=bh, dw=round(64*s),
                    dh=round(6*s), rad=round(18*s),
                    o1=round(46*s), o2=round(124*s), s=s, bad=not fits,
                    block=round(45*s) + len(hl)*round(78*s) + round(44*s) +
                          len(sl)*sub_lh + (round(70*s) + bh if has_word else 0))

    c = m(round(sc*100)) if sc else autofit(m, 140, 62, TGT, CAP)
    fh, fs, fw, f2 = c["fh"], c["fs"], c["fw"], c["f2"]
    y = top = (H-c["block"])//2
    dw = c["dw"]; d.rectangle([(W-dw)//2, y, (W+dw)//2, y+c["dh"]], fill=acc)
    y += c["g1"]
    for l in c["hl"]:
        lw = d.textlength(l, font=fh); d.text(((W-lw)//2, y), l, font=fh, fill=INK); y += c["lh"]
    y += c["g2"]
    for l in c["sl"]:
        lw = d.textlength(l, font=fs); d.text(((W-lw)//2, y), l, font=fs, fill=sub_ink); y += c["sub_lh"]
    if has_word:
        y += c["g3"]
        bw, bh = c["bw"], c["bh"]; bx = (W-bw)//2
        d.rounded_rectangle([bx, y, bx+bw, y+bh], c["rad"], fill=acc)
        ww = d.textlength(word, font=fw); d.text((bx+(bw-ww)//2, y+c["o1"]), word, font=fw, fill=INK)
        w2 = d.textlength("написать в директ", font=f2)
        d.text((bx+(bw-w2)//2, y+c["o2"]), "написать в директ", font=f2, fill=INK)
    img.save(out)
    return {"block_h": c["block"], "sc": c["s"],
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── H. BIO_SPLIT: портрет БЕЗ полей/скруглений на всю высоту одной стороны +
#     повторяющиеся блоки заголовок/линия/подпись (референс владельца, 18.08.2026) ──
def _label_track(d, text, f, x, y, fill, track=2):
    """Мелкая метка с ручной разрядкой (см. engine_editorial._track — тот же
    приём: PIL не умеет letter-spacing, поэтому рисуем посимвольно)."""
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + track
    return x


def _line_w(d, line, sp):
    """Ширина одной строки wrap_rich (см. draw_rich в engine4) — нужна, чтобы
    ловить СЛОВО ШИРЕ КОЛОНКИ: wrap_rich не умеет переносить внутри слова,
    просто кладёт его отдельной строкой и молча вылезает за maxw. В узкой
    колонке bio_split (≈390px против обычных 936px) это реальный риск, не
    гипотетический — поймано на «СОБСТВЕННИКОВ» вживую."""
    if not line:
        return 0
    w = sum(d.textlength(w_, font=ff) for w_, st, ff in line) + sp * (len(line) - 1)
    if line[0][1] == "chip":
        w += e.CHIP_PAD
    return w


def bio_split(blocks, photo, acc, out, i=1, n=8, surname="", side="right",
              split_at=0.46, dark=0.0, hs=None):
    """
    Карточка эксперта: фото — БЕЗ полей и скруглений (в отличие от `photo_side`)
    на всю высоту одной половины кадра, кремовая колонка текста — на другой.
    Замысел референса — фото ОДНО И ТО ЖЕ на каждом слайде карусели, меняется
    только текст колонки: удостоверение, а не фотосессия.

    `blocks` — список из 1-2 пар (headline, sub); поддерживают разметку
    *слово* (коралл) как везде в семействе. `surname` — печатает служебную
    метку «ФАМИЛИЯ | NN» в левом верхнем углу колонки (пусто — метки нет).
    Возвращает {'text_bbox', 'photo'} для проверки текста на лице (здесь
    заведомо не пересекаются — раздельные зоны, но интерфейс общий с семейством).
    """
    img = Image.new("RGB", (W, H), BG)
    split_x = int(W * split_at)
    if side == "right":
        pbox = (split_x, 0, W, H)
        tx, tw = M, split_x - M - 36
    else:
        pbox = (0, 0, W - split_x, H)
        tx, tw = (W - split_x) + 36, split_x - M - 36
    ph = _fit_box(e.PHOTOS[photo], pbox[2] - pbox[0], H, dark)
    img.paste(ph, (pbox[0], pbox[1]))
    d = ImageDraw.Draw(img)
    _counter(d, i, n)

    if surname:
        fl = font("SemiBold", 20, family="Inter")
        _label_track(d, f"{surname} | {i:02d}", fl, tx, M, BODY)

    def m(s):
        fh = font("Bold", s, family="Inter")
        sp = max(18, round(s * 0.40))
        fs, fsb = font("Regular", sp, family="Inter"), font("SemiBold", sp, family="Inter")
        lh, sub_lh = round(s * 1.18), round(sp * 1.5)
        hsp, ssp = d.textlength(" ", font=fh), d.textlength(" ", font=fs)
        parts, total, bad = [], 0, False
        for hk, sub in blocks:
            hl = e.wrap_rich(d, hk, fh, tw)
            sl = e.wrap_rich(d, sub, fs, tw, fsb) if sub else []
            if any(_line_w(d, ln, hsp) > tw for ln in hl) or \
               any(_line_w(d, ln, ssp) > tw for ln in sl):
                bad = True
            h_block = len(hl) * lh + (26 + 4 + 20 + len(sl) * sub_lh if sl else 0)
            parts.append(dict(hl=hl, sl=sl))
            total += h_block
        gap = round(s * 0.9)
        total += gap * (len(blocks) - 1)
        return dict(fh=fh, fs=fs, lh=lh, sub_lh=sub_lh, parts=parts, gap=gap,
                    block=total, bad=bad)

    top_zone = M + (56 if surname else 0)
    bottom_zone = M + 40                    # под номером страницы
    avail = H - top_zone - bottom_zone
    c = m(hs) if hs else autofit(m, 58, 28, int(avail * 0.72), avail)

    y = y_top = top_zone + (avail - c["block"]) // 2
    for idx, part in enumerate(c["parts"]):
        y = _rich(d, part["hl"], c["fh"], tx, y, c["lh"], acc, base=INK)
        if part["sl"]:
            y += 26
            dash(d, tx, y, acc, 46, 3); y += 24
            y = _rich(d, part["sl"], c["fs"], tx, y, c["sub_lh"], acc, base=BODY)
        if idx < len(c["parts"]) - 1:
            y += c["gap"]

    fn = font("Regular", 22, family="Inter")
    d.text((tx, H - M - 26), f"{i:02d}", font=fn, fill=BODY)

    img.save(out)
    # БЕЗ ключа "photo": check_face_safe детектит лицо через e.fit() на весь
    # холст 1080x1350 — здесь фото стоит в СВОЁМ боксе (pbox) со своим кропом,
    # координаты лица оттуда не совпадут с этим кадром и дадут ложный
    # результат. Разнос текста и фото по непересекающимся X-зонам (tx1 всегда
    # < split_x) гарантирует отсутствие «текст на лице» геометрией раскладки,
    # без детектора — сверено на смоук-тесте глазами.
    return {"text_bbox": (tx, y_top, tx + tw, y)}
