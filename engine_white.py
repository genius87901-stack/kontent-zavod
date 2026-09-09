"""
Белые внутренние слайды (со 2-го). Акцент — параметр (меняется от карусели к карусели).
Переиспользуем helpers из engine4.

ОДИН ЦВЕТ: акцент появляется здесь ровно в том значении, что записано в
e.ACCENTS — только заливкой (штрих, буллет, плашка, карточка, кнопка, кружок
шага). Текст поверх заливки — INK. Крупный текст (заголовки, цифра bignum,
заголовок cta) — INK на белом, без цвета. Мелкий акцентный текст (*слово*)
рисуется плашкой: draw_rich(..., acc_as_chip=True). Прежний acc_ink() удалён —
он втихую подменял акцент второй, тёмной яркостью, и карусель ехала в двух
цветах сразу.

ОБЛОЖКА (по умолчанию): cover_top() — текст в верхней трети + акцентный штрих,
лицо/фигура открыты снизу без затемнения. Утверждённый стиль с карусели #3.
cover_ref() (текст снизу на затемнении) — оставлен как альтернативный вариант,
использовать только если попросят иначе.
"""
from PIL import Image, ImageDraw
import engine4 as e

W, H, M = e.W, e.H, e.M
font = e.font
INK   = e.INK    # один «чёрный» на весь движок, см. engine4.INK
GREY  = (92, 88, 84)
CARD  = (244, 242, 239)
CARDL = (236, 233, 229)
BG    = (252, 251, 249)

# ══ АВТОКЕГЛЬ — порт engine_photo._autofit на белые/кремовые раскладки ══════
# ЗАЧЕМ. До волны 2 подбор кегля жил ТОЛЬКО в checklist, остальные семь
# раскладок были на фиксированном кегле. Замер (docs/journals/W2-autofit.md):
# quote на коротком тексте давал блок 220 px из 1350, на длинном — 732 px.
# Плотность гуляла втрое, карусель звучала вразнобой: где мало слов — робкий
# мелкий слайд, где много — риск уехать за край.
#
# ЧЕМ ЭТО ЛУЧШЕ НАИВНОГО ПОДБОРА (тот, что стоял в checklist: «первый кегль,
# который влезает», с break). Высота блока прыгает скачками: кегль вырос на
# двойку — строка перенеслась — блок сразу выше на целую строку. «Первый, что
# влезает» поэтому регулярно останавливается заметно НИЖЕ цели. Здесь
# перебираются все кегли и берётся БЛИЖАЙШИЙ К ЦЕЛЕВОЙ ВЫСОТЕ — именно это
# держит одинаковую плотность от слайда к слайду. Логика дословно из
# engine_photo._autofit (строки 225–259), включая допуск перелёта.
#
# ОТЛИЧИЕ ОТ ОРИГИНАЛА, СОЗНАТЕЛЬНОЕ. В engine_photo, если текст длиннее
# target*over, кандидат отбрасывается и функция сваливается на минимальный
# кегль (48) — даже когда в кадр спокойно влезал бы кегль 60. Здесь такой
# случай ловит `fallback`: самый крупный кегль, который ещё помещается в кадр.
# На длинных чек-листах это разница между «мелко и с воздухом» и «плотно».
TEXT_AREA  = 0.44          # доля площади кадра под текст на чистом фоне
PHOTO_AREA = 0.38          # то же для текста поверх фото (ср. engine_photo=0.40)
SAFE_Y     = 130           # поле сверху/снизу, за которое блок не заходит
COL_W      = W - 2*M
TGT        = int(TEXT_AREA  * W * H / COL_W)     # 685 px
TGT_PHOTO  = int(PHOTO_AREA * W * H / COL_W)     # 592 px
CAP        = H - 2*SAFE_Y                        # 1090 px

# Кегли тела на белом: те же роли, что BODY_MIN/BODY_MAX в engine_photo.
BODY_MIN, BODY_MAX = 24, 56


def autofit(measure, hi, lo, target, cap, over=1.10, step=2):
    """Подобрать масштаб раскладки: ближайший к цели, а не первый влезающий.

    measure(s) -> dict с обязательным ключом 'block' (высота блока в px) и
    необязательным 'bad' (True — кандидат не годится по ШИРИНЕ: подпись в
    карточке, слово на кнопке или пункт списка не помещаются в свою рамку;
    такие раскладки не переносят текст и раньше молча резали его по краю).
    Перебор идёт от hi к lo включительно; `s` — либо кегль заголовка, либо
    масштаб в процентах (для раскладок, где двигается вся сетка сразу).
    """
    best = fallback = None
    for s in range(hi, lo - 1, -step):
        m = measure(s)
        if m["block"] > cap or m.get("bad"):
            continue
        if fallback is None:            # самый крупный, что влезает в кадр
            fallback = m
        if m["block"] > target * over:
            continue
        if best is None or abs(m["block"] - target) < abs(best["block"] - target):
            best = m
    return best or fallback or measure(lo)


def _base(i, n):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # счётчик тёмный на белом
    f = font("Bold", 24); t = f"{i}/{n}"
    tw = d.textlength(t, font=f); x, y = W-M-tw, 50
    d.rounded_rectangle([x-18, y-10, x+tw+18, y+34], 22, fill=INK)
    d.text((x, y), t, font=f, fill=(255,255,255))
    return img, d

def dash(d, x, y, acc, w=48): d.rectangle([x, y, x+w, y+5], fill=acc)


# ── A. QUOTE — крупное утверждение (замена split, без фото) ──
def quote(title, body, acc, out, i=2, n=8, hs=None):
    """`hs` (кегль заголовка) необязателен: None -> автокегль под TGT.
    Число = прежнее поведение с фиксированным кеглем (hs=76 даёт кадр
    байт-в-байт как до волны 2 — коэффициенты подобраны так намеренно)."""
    img, d = _base(i, n)

    def m(s):
        fh = font("Black", s)
        bs = max(BODY_MIN, min(BODY_MAX, round(s * 0.526)))   # 40 при 76
        fb, fbold = font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d, title, fh, W-2*M, acc_as_chip=True)
        bl = e.wrap_rich(d, body, fb, W-2*M, fbold, acc_as_chip=True)
        lh, blh, gap = round(s*1.158), round(bs*1.4), round(s*0.53)
        return dict(fh=fh, fb=fb, hl=hl, bl=bl, lh=lh, blh=blh, gap=gap,
                    block=44 + len(hl)*lh + gap + len(bl)*blh)

    c = m(hs) if hs else autofit(m, 118, 44, TGT, CAP)
    y = top = (H - c["block"])//2 - 40
    dash(d, M, y, acc, 64); y += 44
    y = e.draw_rich(d, c["hl"], c["fh"], M, y, c["lh"], acc,
                    base=INK, acc_as_chip=True, bg=BG) + c["gap"]
    e.draw_rich(d, c["bl"], c["fb"], M, y, c["blh"], acc,
                base=(92,88,84), acc_as_chip=True, bg=BG)
    img.save(out)
    return {"block_h": c["block"], "hs": c["fh"].size,
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── B. SPLIT — фото сверху 44% / белый низ с текстом ──
def split(title, body, photo, acc, out, i=2, n=8, cut=0.44, hs=None):
    """`hs=None` -> автокегль; hs=50 — прежний фиксированный кегль байт-в-байт."""
    img = Image.new("RGB", (W, H), BG)
    ch = int(H*cut)
    img.paste(e.fit(e.PHOTOS[photo], ch, dark=0.04), (0, 0))
    d = ImageDraw.Draw(img)
    f = font("Bold", 24); t = f"{i}/{n}"; tw = d.textlength(t, font=f); x, y0 = W-M-tw, 50
    d.rounded_rectangle([x-18, y0-10, x+tw+18, y0+34], 22, fill=(0,0,0,120)); d.text((x, y0), t, font=f, fill=(255,255,255))

    # Цель здесь — доля СВОБОДНОЙ ПОЛОСЫ под фото, а не доля кадра: половину
    # слайда занял снимок, мерить текст от площади всего кадра бессмысленно.
    avail = H - 56 - (ch + 52)

    def m(s):
        bs = max(BODY_MIN, min(BODY_MAX, round(s*0.62)))          # 31 при 50
        fh, fb, fbold = font("Black", s), font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d, title, fh, W-2*M, acc_as_chip=True)
        bl = e.wrap_rich(d, body, fb, W-2*M, fbold, acc_as_chip=True)
        return dict(fh=fh, fb=fb, hl=hl, bl=bl,
                    lh=round(s*1.24), blh=round(bs*1.548),
                    g1=round(s*0.68), g2=round(s*0.52),
                    block=round(s*0.68) + len(hl)*round(s*1.24) +
                          round(s*0.52) + len(bl)*round(bs*1.548))

    c = m(hs) if hs else autofit(m, 80, 28, int(avail*0.80), avail)
    y = top = ch + 52
    dash(d, M, y, acc); y += c["g1"]
    y = e.draw_rich(d, c["hl"], c["fh"], M, y, c["lh"], acc,
                    base=INK, acc_as_chip=True, bg=BG) + c["g2"]
    e.draw_rich(d, c["bl"], c["fb"], M, y, c["blh"], acc,
                base=(92,88,84), acc_as_chip=True, bg=BG)
    img.save(out)
    return {"block_h": c["block"], "hs": c["fh"].size,
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── C. LIST — заголовок + акцентные дефисы ──
def photo_list(title, intro, items, acc, out, i=3, n=8, hs=None):
    """`hs=None` -> автокегль; hs=64 — прежний фиксированный кегль байт-в-байт.

    Пункт рисуется ОДНОЙ строкой без переноса, поэтому подбор ограничен ещё и
    шириной: кегль, на котором самый длинный пункт вылезает за поле, отсекается
    (раньше он просто уезжал за край — молча).
    """
    img, d = _base(i, n)

    def m(s):
        fh = font("Black", s)
        fi = font("Regular", round(s*0.531))          # 34 при 64
        fl = font("Bold", round(s*0.656))             # 42 при 64
        hl = e.wrap_rich(d, title, fh, W-2*M, acc_as_chip=True)
        il = e.wrap(d, intro, fi, W-2*M)
        bd, boff = round(fl.size*0.524), round(fl.size*0.19)   # 22 / 8 при 42
        ind = round(fl.size*1.19)                              # 50 при 42
        lh, ilh, step = round(s*1.1875), round(fi.size*1.353), round(fl.size*1.952)
        g1, g2 = round(s*0.3125), round(s*0.469)               # 20 / 30 при 64
        wide = any(d.textlength(it, font=fl) > W - 2*M - ind for it in items)
        return dict(fh=fh, fi=fi, fl=fl, hl=hl, il=il, lh=lh, ilh=ilh, step=step,
                    g1=g1, g2=g2, bd=bd, boff=boff, ind=ind, bad=wide,
                    block=44 + len(hl)*lh + g1 + len(il)*ilh + g2 + len(items)*step)

    c = m(hs) if hs else autofit(m, 96, 40, TGT, CAP)
    y = top = (H - c["block"])//2 - 30
    dash(d, M, y, acc, 64); y += 44
    y = e.draw_rich(d, c["hl"], c["fh"], M, y, c["lh"], acc,
                    base=INK, acc_as_chip=True, bg=BG) + c["g1"]
    for l in c["il"]:
        d.text((M, y), l, font=c["fi"], fill=(92,88,84)); y += c["ilh"]
    y += c["g2"]
    for it in items:
        d.ellipse([M, y+c["boff"], M+c["bd"], y+c["boff"]+c["bd"]],
                  fill=acc, **e.chip_edge(acc, BG))
        d.text((M+c["ind"], y), it, font=c["fl"], fill=INK); y += c["step"]
    img.save(out)
    return {"block_h": c["block"], "hs": c["fh"].size,
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── C2. CHECKLIST — заголовок + пункты с ПЕРЕНОСОМ (для длинных пунктов) ──
def checklist(title, items, acc, out, i=3, n=8, hs=None):
    """Как photo_list, но пункт МОЖЕТ переноситься на несколько строк — для
    содержательных пунктов с пояснением (photo_list рисует пункт одной строкой
    и длинный обрежет). Кегль подбирается, чтобы все пункты влезли по высоте.
    `hs` — фиксированный кегль заголовка вместо автоподбора (hs=60 = прежний вид)."""
    img, d = _base(i, n)
    ind = 46

    # Формулы здесь ровно те же, что стояли до волны 2 (при hs=60 кадр
    # байт-в-байт). Поменялись две вещи: потолок цикла поднят с 60 до 92 —
    # иначе короткий список упирался в кегль и оставлял полкадра пустым, —
    # и вместо break на первом влезающем берётся ближайший к цели (autofit).
    def m(s):
        fh = font("Black", s)
        bs = max(26, min(BODY_MAX, int(s * 0.56)))
        fb, fbold = font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d, title, fh, W-2*M, acc_as_chip=True)
        wr = [e.wrap_rich(d, it, fb, W-2*M-ind, fbold, acc_as_chip=True) for it in items]
        lh, blh, ig = int(s*1.16), int(bs*1.34), int(bs*0.85)
        block = 44 + len(hl)*lh + 40 + sum(len(w)*blh + ig for w in wr) - ig
        return dict(fh=fh, fb=fb, hl=hl, wr=wr, lh=lh, blh=blh, ig=ig,
                    block=block, bs=bs)

    c = m(hs) if hs else autofit(m, 92, 38, TGT, CAP)
    y = top = (H - c["block"])//2 - 10
    dash(d, M, y, acc, 64); y += 44
    y = e.draw_rich(d, c["hl"], c["fh"], M, y, c["lh"], acc,
                    base=INK, acc_as_chip=True, bg=BG) + 40
    for w in c["wr"]:
        r = int(c["bs"] * 0.26)
        cy = y + (c["blh"] - 2*r)//2 - 2
        d.ellipse([M, cy, M+2*r, cy+2*r], fill=acc, **e.chip_edge(acc, BG))
        e.draw_rich(d, w, c["fb"], M+ind, y, c["blh"], acc,
                    base=INK, acc_as_chip=True, bg=BG)
        y += len(w)*c["blh"] + c["ig"]
    img.save(out)
    return {"block_h": c["block"], "hs": c["fh"].size,
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── D. COMPARE — 2 серые карточки + 1 акцентная ──
def compare(title, old_a, old_b, new, acc, out, i=4, n=8, sc=None):
    """`sc=None` -> автоподбор МАСШТАБА ВСЕЙ СЕТКИ (заголовок + карточки едут
    вместе): растянуть один заголовок над карточками фиксированной высоты —
    значит развалить раскладку. sc=1.0 — прежний вид байт-в-байт.

    Подбор здесь заодно закрывает старую дырку: подписи в карточках рисуются
    БЕЗ переноса, и длинное «Нужен другой человек в центре схемы» молча
    вылезало за карточку. Масштабы, на которых текст не помещается, отсекаются.
    """
    img, d = _base(i, n)

    def m(k):
        s = k / 100.0
        fh = font("Black", round(60*s))
        fk = font("Bold", round(24*s)); fn_ = font("Regular", round(32*s))
        fv = font("Black", round(50*s))
        hl = e.wrap_rich(d, title, fh, W-2*M, acc_as_chip=True)
        lh, g1, g2 = round(72*s), round(44*s), round(24*s)
        pad = round(28*s)
        cw = (W-2*M-g2)//2
        # Название в карточке ТЕПЕРЬ ПЕРЕНОСИТСЯ. Раньше оно рисовалось одной
        # строкой и на длинной подписи («SMM-команда на подряде каждый месяц»)
        # молча уезжало за карточку — видно на любом кадре до правки. Карточка
        # растёт на лишние строки, высота у всех трёх общая, чтобы цифры внизу
        # стояли на одной линии.
        nlh = round(40*s)
        nms = [e.wrap(d, nm, fn_, lim - 2*pad)
               for (lbl, nm, val), lim in ((old_a, cw), (old_b, cw), (new, W-2*M))]
        extra = (max(len(x) for x in nms) - 1) * nlh
        ch1 = round(210*s) + extra
        # lbl и val не переносятся по замыслу (метка и цифра — короткие)
        fits = all(d.textlength(t, font=f) <= lim - pad - round(10*s)
                   for (lbl, nm, val), lim in
                   ((old_a, cw), (old_b, cw), (new, W-2*M))
                   for t, f in ((lbl, fk), (val, fv)))
        return dict(fh=fh, fk=fk, fn_=fn_, fv=fv, hl=hl, lh=lh, g1=g1, g2=g2,
                    ch=ch1, cw=cw, pad=pad, s=s, bad=not fits,
                    nms=nms, nlh=nlh,
                    r1=round(26*s), r2=round(70*s), r3=round(124*s)+extra,
                    rad=round(16*s),
                    block=44 + len(hl)*lh + g1 + ch1 + g2 + ch1)

    c = m(round(sc*100)) if sc else autofit(m, 150, 72, TGT, CAP)
    fk, fn_, fv, cw = c["fk"], c["fn_"], c["fv"], c["cw"]
    y = top = (H - c["block"])//2 - 20
    dash(d, M, y, acc, 64); y += 44
    y = e.draw_rich(d, c["hl"], c["fh"], M, y, c["lh"], acc,
                    base=INK, acc_as_chip=True, bg=BG) + c["g1"]
    for k,(lbl,nm,val) in enumerate([old_a, old_b]):
        x = M + k*(cw+c["g2"])
        d.rounded_rectangle([x, y, x+cw, y+c["ch"]], c["rad"], fill=CARD)
        d.text((x+c["pad"], y+c["r1"]), lbl, font=fk, fill=(165,160,155))
        ny = y + c["r2"]
        for l in c["nms"][k]:
            d.text((x+c["pad"], ny), l, font=fn_, fill=(96,92,88)); ny += c["nlh"]
        d.text((x+c["pad"], y+c["r3"]), val, font=fv, fill=(150,145,140))
    y += c["ch"] + c["g2"]
    ch2 = c["ch"]
    lbl,nm,val = new
    # «яркая» заливка на белом яркой не выглядит: lime к BG даёт 1.14 при норме
    # 3.0 (WCAG 1.4.11) — карточка физически неотличима от фона, и вся идея
    # «две серые и одна яркая» рассыпается. chip_edge добавляет волосяную
    # обводку INK 2 px ровно тем акцентам, которым не хватает контраста.
    d.rounded_rectangle([M, y, W-M, y+ch2], c["rad"], fill=acc, **e.chip_edge(acc, BG))
    d.text((M+c["pad"], y+c["r1"]), lbl, font=fk, fill=INK)
    ny = y + c["r2"]
    for l in c["nms"][2]:
        d.text((M+c["pad"], ny), l, font=fn_, fill=INK); ny += c["nlh"]
    d.text((M+c["pad"], y+c["r3"]), val, font=fv, fill=INK)
    img.save(out)
    return {"block_h": c["block"], "sc": c["s"],
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── E. BIGNUM — крупная акцентная цифра ──
def bignum(pre, num, post, body, acc, out, i=5, n=8, sc=None):
    """`sc=None` -> автоподбор масштаба всей сетки (цифра и подписи едут
    вместе — иначе пропорция «цифра/подпись» ломается). sc=1.0 — прежний вид."""
    img, d = _base(i, n)

    def m(k):
        s = k / 100.0
        fp = font("Black", round(62*s)); fnum = font("Black", round(210*s))
        bs = round(34*s)
        fb, fbold = font("Regular", bs), font("Bold", bs)
        pre_l = e.wrap(d, pre, fp, W-2*M); post_l = e.wrap(d, post, fp, W-2*M)
        bl = e.wrap_rich(d, body, fb, W-2*M, fbold, acc_as_chip=True)
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
    d.text((M, y), num, font=c["fnum"], fill=INK); y += c["nstep"]
    for l in c["post_l"]:
        d.text((M, y), l, font=c["fp"], fill=INK); y += c["pstep"]
    y += c["g1"]
    dash(d, M, y, acc, c["dw"]); y += c["g2"]
    e.draw_rich(d, c["bl"], c["fb"], M, y, c["blh"], acc,
                base=(92,88,84), acc_as_chip=True, bg=BG)
    img.save(out)
    return {"block_h": c["block"], "sc": c["s"],
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── F. STEPS — нумерованные шаги (новый, чистый на белом) ──
def steps(title, items, acc, out, i=6, n=8, sc=None):
    """`sc=None` -> автоподбор масштаба сетки шагов. sc=1.0 — прежний вид."""
    img, d = _base(i, n)

    # Блок прижат к y=200 (утверждённый вид), поэтому цель считается от
    # СВОБОДНОЙ ВЫСОТЫ под ним, а не от площади кадра: список из трёх шагов
    # раньше оставлял под собой 650 px пустоты, из шести — почти упирался в край.
    avail = H - 200 - 72

    def m(k):
        s = k / 100.0
        fh = font("Black", round(60*s))
        hl = e.wrap_rich(d, title, fh, W-2*M, acc_as_chip=True)
        lh, g1, g2, row = round(64*s), round(38*s), round(30*s), round(108*s)
        return dict(fh=fh, hl=hl, lh=lh, g1=g1, g2=g2, row=row, s=s,
                    fnum=font("Black", round(40*s)), ft=font("Bold", round(32*s)),
                    fd=font("Regular", round(27*s)),
                    dia=round(58*s), ind=round(84*s), dw=round(48*s),
                    o1=round(6*s), o2=round(2*s), o3=round(40*s),
                    block=g1 + len(hl)*lh + g2 + len(items)*row)

    c = m(round(sc*100)) if sc else autofit(m, 140, 60, int(avail*0.78), avail)
    fnum, ft, fd, dia, ind = c["fnum"], c["ft"], c["fd"], c["dia"], c["ind"]
    y = top = 200
    dash(d, M, y, acc, c["dw"]); y += c["g1"]
    y = e.draw_rich(d, c["hl"], c["fh"], M, y, c["lh"], acc,
                    base=INK, acc_as_chip=True, bg=BG) + c["g2"]
    for k,(head, sub) in enumerate(items, 1):
        # кружок — заливка акцентом, цифра поверх тёмная: акцентным текстом
        # на белом номер шага не читается (lime к белому — контраст 1.14).
        # Сам кружок на белом тоже пропадает — chip_edge даёт ему волосяную грань.
        d.ellipse([M, y, M+dia, y+dia], fill=acc, **e.chip_edge(acc, BG))
        nw = d.textlength(str(k), font=fnum)
        d.text((M+(dia-nw)//2, y+c["o1"]), str(k), font=fnum, fill=INK)
        d.text((M+ind, y-c["o2"]), head, font=ft, fill=INK)
        for l in e.wrap(d, sub, fd, W-2*M-ind):
            d.text((M+ind, y+c["o3"]), l, font=fd, fill=GREY); break
        y += c["row"]
    img.save(out)
    return {"block_h": c["block"], "sc": c["s"],
            "text_bbox": (M, top, W - M, top + c["block"])}


# ── G. CTA — по центру, кодовое слово в акцентной рамке ──
def cta(title, sub, word, acc, out, i=8, n=8, sc=None, btn_fill=INK):
    """`sc=None` -> автоподбор масштаба (заголовок, подзаголовок и кнопка едут
    вместе). sc=1.0 — прежний вид байт-в-байт. `btn_fill` — цвет текста в
    кнопке кодового слова (по умолчанию INK, как раньше)."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f = font("Bold", 24); t = f"{i}/{n}"; tw = d.textlength(t, font=f); x, y0 = W-M-tw, 50
    d.rounded_rectangle([x-18, y0-10, x+tw+18, y0+34], 22, fill=INK); d.text((x, y0), t, font=f, fill=(255,255,255))
    # КОДОВОЕ СЛОВО НЕОБЯЗАТЕЛЬНО (правка 21.07, как в engine_photo.cta 20.07).
    # Пустой word -> плашки нет, а подзаголовок набирается 48-м кеглем вместо
    # 34-го: без плашки действие просит подзаголовок, и мелким серым он не
    # слышен против заголовка в 66. Ветка с word считает ровно как раньше —
    # сверено побайтово пересборкой build_c8.py.
    has_word = bool(word and word.strip())
    sub_ink = (96, 92, 88) if has_word else INK

    def m(k):
        s = k / 100.0
        fh = font("Black", round(66*s)); fw = font("Black", round(58*s))
        fs = font("Regular", round((34 if has_word else 48)*s))
        f2 = font("Bold", round(27*s))
        sub_lh = round((50 if has_word else 66)*s)
        hl = e.wrap(d, title, fh, W-2*M-40); sl = e.wrap(d, sub, fs, W-2*M-40)
        bw, bh = round(480*s), round(190*s)
        # кнопка не переносится: слово должно поместиться в плашку, иначе
        # раньше оно молча вылезало за её края
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
        # кнопка кодового слова — последний экран карусели: если её заливка на
        # белом не различима (lime 1.14, orange 2.41), кнопка перестаёт читаться
        # кнопкой. Волосяная грань возвращает ей форму.
        d.rounded_rectangle([bx, y, bx+bw, y+bh], c["rad"], fill=acc, **e.chip_edge(acc, BG))
        ww = d.textlength(word, font=fw); d.text((bx+(bw-ww)//2, y+c["o1"]), word, font=fw, fill=btn_fill)
        w2 = d.textlength("написать в директ", font=f2)
        d.text((bx+(bw-w2)//2, y+c["o2"]), "написать в директ", font=f2, fill=btn_fill)
    img.save(out)
    return {"block_h": c["block"], "sc": c["s"],
            "text_bbox": (M, top, W - M, top + c["block"])}


# ═══ ОБЛОЖКА В СТИЛЕ РЕФЕРЕНСОВ (фото + жирный текст снизу + плашки) ═══
def cover_ref(hook, sub, photo, acc, out, i=1, n=8):
    from PIL import Image as _I, ImageDraw as _D
    img = e.fit(e.PHOTOS[photo], H, dark=0.10).convert("RGBA")
    # градиент снизу под текст
    ov = _I.new("RGBA", (W, H), (0,0,0,0)); od = _D.Draw(ov)
    top = int(H*0.40)
    for y in range(top, H):
        a = int(205 * min(1, (y-top)/(H*0.55)))
        od.line([(0,y),(W,y)], fill=(8,7,6,min(a,205)))
    img = _I.alpha_composite(img, ov).convert("RGB")
    d = _D.Draw(img)

    # счётчик
    f = font("Bold", 24); t = f"{i}/{n}"; tw = d.textlength(t, font=f); x, y0 = W-M-tw, 50
    d.rounded_rectangle([x-18, y0-10, x+tw+18, y0+34], 22, fill=(0,0,0,150)); d.text((x, y0), t, font=f, fill=(255,255,255))

    # блок текста прижат вниз
    fh = font("Black", 88); lh = 100
    fs = font("Regular", 34); fsb = font("Bold", 34)
    hlines = e.wrap_rich(d, hook, fh, W-2*M)
    slines = e.wrap_rich(d, sub, fs, W-2*M, fsb)
    handle_h = 52
    total = 8 + len(hlines)*lh + 24 + len(slines)*46
    y = H - 96 - total
    y_top = y          # верх текстового блока — для проверки наезда на лицо
    x1 = M

    # (ник убран по запросу)
    y += 8

    # заголовок с тенью + акцент/плашки
    for line in hlines:
        cx = M + (e.CHIP_PAD if line and line[0][1] == "chip" else 0)
        for w_, st, ff in line:
            ww = d.textlength(w_, font=ff)
            if st == "chip":
                asc, desc = ff.getmetrics()
                d.rounded_rectangle([cx-e.CHIP_PAD, y-4, cx+ww+e.CHIP_PAD, y+asc+desc], 12, fill=acc)
                d.text((cx, y), w_, font=ff, fill=INK)
            else:
                col = acc if st == "acc" else (255,255,255)
                d.text((cx+3, y+4), w_, font=ff, fill=(0,0,0))   # тень
                d.text((cx, y), w_, font=ff, fill=col)
            cx += ww + d.textlength(" ", font=ff)
        x1 = max(x1, cx)
        y += lh
    y += 24

    # подзаголовок (можно с плашкой)
    sp = d.textlength(" ", font=fs)
    for line in slines:
        cx = M + (e.CHIP_PAD if line and line[0][1] == "chip" else 0)
        for w_, st, ff in line:
            ww = d.textlength(w_, font=ff)
            if st == "chip":
                asc, desc = ff.getmetrics()
                d.rounded_rectangle([cx-e.CHIP_PAD, y-2, cx+ww+e.CHIP_PAD, y+asc+desc-6], 8, fill=acc)
                d.text((cx, y), w_, font=ff, fill=INK)
            else:
                col = acc if st == "acc" else (230,227,223)
                d.text((cx+2, y+2), w_, font=ff, fill=(0,0,0))
                d.text((cx, y), w_, font=ff, fill=col)
            cx += ww + sp
        x1 = max(x1, cx)
        y += 46
    img.save(out)
    return {"text_bbox": (M, int(y_top), int(x1), int(y)), "photo": photo}


# ═══ ОБЛОЖКА "МАРКЕР" — строка текста + строка со сплошной плашкой-хайлайтом ═══
def cover_marker(line1, highlight, sub, photo, acc, out, i=1, n=8,
                 scale=1.0, sub_scale=1.0, lift=0):
    """
    Референс: фото на весь кадр, обычная строка белым сверху,
    следующая строка целиком залита акцентом (как маркером), тёмный текст поверх.
    Подзаголовок мельче под ними. Блок текста — на 60-85% высоты кадра.

    `scale`/`sub_scale` НЕОБЯЗАТЕЛЬНЫ (по умолчанию 1.0 → байт-в-байт как раньше):
    множители кегля заголовка и подзаголовка, когда обложку разово нужно
    сделать крупнее (карусель #14 — призыв должен бить сразу при заходе).
    """
    from PIL import Image as _I, ImageDraw as _D
    img = e.fit(e.PHOTOS[photo], H, dark=0.08).convert("RGBA")
    # мягкий градиент снизу, начиная примерно от середины — под текстовый блок
    ov = _I.new("RGBA", (W, H), (0,0,0,0)); od = _D.Draw(ov)
    top = int(H*0.50)
    for yy in range(top, H):
        a = int(175 * min(1, (yy-top)/(H*0.45)))
        od.line([(0,yy),(W,yy)], fill=(6,5,5,min(a,175)))
    img = _I.alpha_composite(img, ov).convert("RGB")
    d = _D.Draw(img)

    # счётчик
    f = font("Bold", 24); t = f"{i}/{n}"; tw = d.textlength(t, font=f); x, y0 = W-M-tw, 50
    d.rounded_rectangle([x-18, y0-10, x+tw+18, y0+34], 22, fill=(0,0,0,150)); d.text((x, y0), t, font=f, fill=(255,255,255))

    fh = font("Black", round(58*scale)); lh = round(68*scale)
    fs = font("Regular", round(30*sub_scale)); fsb = font("Bold", round(30*sub_scale))

    l1_lines = e.wrap(d, line1, fh, W-2*M)
    hl_lines = e.wrap(d, highlight, fh, W-2*M-40)
    slines = e.wrap_rich(d, sub, fs, W-2*M, fsb)

    total = len(l1_lines)*lh + 14 + len(hl_lines)*(lh+16) + 26 + len(slines)*round(44*sub_scale)
    y = H - 88 - lift - total
    y_top = y          # верх текстового блока — для проверки наезда на лицо
    x1 = M

    for l in l1_lines:
        d.text((M+2, y+3), l, font=fh, fill=(0,0,0,150))
        d.text((M, y), l, font=fh, fill=(255,255,255))
        x1 = max(x1, M + d.textlength(l, font=fh))
        y += lh
    y += 14

    for l in hl_lines:
        lw = d.textlength(l, font=fh)
        asc, desc = fh.getmetrics()
        # подложка встаёт по полю, буква — на CHIP_PAD правее (см. engine4.CHIP_PAD)
        tx = M + e.CHIP_PAD
        d.rounded_rectangle([M, y-8, tx+lw+e.CHIP_PAD, y+asc+desc-4], 12, fill=acc)
        d.text((tx, y), l, font=fh, fill=INK)
        x1 = max(x1, tx + lw + e.CHIP_PAD)
        y += lh + 16
    y += 26

    sp = d.textlength(" ", font=fs)
    for line in slines:
        cx = M
        for w_, st, ff in line:
            ww = d.textlength(w_, font=ff)
            col = acc if st == "acc" else (222,219,215)
            d.text((cx+1, y+1), w_, font=ff, fill=(0,0,0,140))
            d.text((cx, y), w_, font=ff, fill=col)
            cx += ww + sp
        x1 = max(x1, cx)
        y += round(44*sub_scale)
    img.save(out)
    return {"text_bbox": (M, int(y_top), int(x1), int(y)), "photo": photo}


# ═══ ОБЛОЖКА (ДЕФОЛТ) — текст сверху, лицо/фигура открыты снизу ═══
def cover_top(hook, sub, photo, acc, out, i=1, n=8):
    from PIL import Image as _I, ImageDraw as _D
    img = e.fit(e.PHOTOS[photo], H, dark=0.06).convert("RGBA")
    # мягкий градиент СВЕРХУ под текст, ниже фото остаётся чистым
    ov = _I.new("RGBA", (W, H), (0,0,0,0)); od = _D.Draw(ov)
    bot = int(H*0.50)
    for y in range(0, bot):
        a = int(190 * (1 - min(1, y/bot)) ** 0.7)
        od.line([(0,y),(W,y)], fill=(6,5,5,min(a,190)))
    img = _I.alpha_composite(img, ov).convert("RGB")
    d = _D.Draw(img)

    # счётчик
    f = font("Bold", 24); t = f"{i}/{n}"; tw = d.textlength(t, font=f); x, y0 = W-M-tw, 50
    d.rounded_rectangle([x-18, y0-10, x+tw+18, y0+34], 22, fill=(0,0,0,150)); d.text((x, y0), t, font=f, fill=(255,255,255))

    # маленький акцентный штрих сверху заголовка
    y = 168
    d.rectangle([M, y, M+44, y+6], fill=acc)
    y += 34

    fh = font("Black", 78); lh = 88
    fs = font("Regular", 30); fsb = font("Bold", 30)
    hlines = e.wrap_rich(d, hook, fh, W-2*M)
    slines = e.wrap_rich(d, sub, fs, W-2*M-32, fsb)
    x1 = M   # правый край текстового блока — нужен для проверки наезда на лицо

    for line in hlines:
        cx = M + (e.CHIP_PAD if line and line[0][1] == "chip" else 0)
        for w_, st, ff in line:
            ww = d.textlength(w_, font=ff)
            if st == "chip":
                asc, desc = ff.getmetrics()
                d.rounded_rectangle([cx-e.CHIP_PAD, y-3, cx+ww+e.CHIP_PAD, y+asc+desc-4], 10, fill=acc)
                d.text((cx, y), w_, font=ff, fill=INK)
            else:
                col = acc if st == "acc" else (255,255,255)
                d.text((cx+2, y+3), w_, font=ff, fill=(0,0,0,160))
                d.text((cx, y), w_, font=ff, fill=col)
            cx += ww + d.textlength(" ", font=ff)
        x1 = max(x1, cx)
        y += lh
    y += 22

    # подзаголовок с вертикальным акцентным штрихом слева.
    # Пустой подзаголовок — обычный вход, а не край: у cover_ref и
    # cover_marker он проходит. Здесь же высота блока получалась нулём,
    # и штрих рисовался с y1 < y0 — PIL валился. Штриху нечего
    # отчёркивать, когда строк нет, поэтому просто не рисуем его.
    block_h = len(slines)*40
    if slines:
        d.rectangle([M, y+4, M+5, y+block_h-4], fill=acc)
    sp = d.textlength(" ", font=fs)
    for line in slines:
        # 28 — отступ от вертикального штриха; плашка первого слова встаёт на него
        cx = M + 28 + (e.CHIP_PAD if line and line[0][1] == "chip" else 0)
        for w_, st, ff in line:
            ww = d.textlength(w_, font=ff)
            if st == "chip":
                asc, desc = ff.getmetrics()
                d.rounded_rectangle([cx-e.CHIP_PAD, y-1, cx+ww+e.CHIP_PAD, y+asc+desc-8], 6, fill=acc)
                d.text((cx, y), w_, font=ff, fill=INK)
            else:
                col = acc if st == "acc" else (225,222,218)
                d.text((cx+1, y+1), w_, font=ff, fill=(0,0,0,150))
                d.text((cx, y), w_, font=ff, fill=col)
            cx += ww + sp
        x1 = max(x1, cx)
        y += 40
    img.save(out)
    return {"text_bbox": (M, 168, int(x1), int(y)), "photo": photo}


# ═══ ВНУТРЕННИЙ СЛАЙД НА ФОТО + ЗАТЕМНЕНИЕ + ТЕКСТ С ПЛАШКАМИ ═══
def photo_dark(title, body, photo, acc, out, i=3, n=8, hs=None, top=False):
    """`hs=None` -> автокегль под TGT_PHOTO (текст поверх фото занимает меньшую
    долю кадра, чем на чистом фоне — снимок тоже должен быть виден).
    hs=56 — прежний фиксированный кегль байт-в-байт. `top=True` — текст сверху
    кадра (градиент затемняет верх), иначе снизу (по умолчанию, как раньше)."""
    from PIL import Image as _I, ImageDraw as _D
    img = e.fit(e.PHOTOS[photo], H, dark=0.50).convert("RGBA")
    ov = _I.new("RGBA",(W,H),(0,0,0,0)); od=_D.Draw(ov)
    if top:
        # текст СВЕРХУ: градиент затемняет верх, ниже фото чище
        gbot=int(H*0.55)
        for yy in range(0,gbot):
            a=int(200*(1-min(1,yy/gbot))**0.8)
            od.line([(0,yy),(W,yy)],fill=(8,7,6,min(a,200)))
    else:
        gtop=int(H*0.34)
        for yy in range(gtop,H):
            a=int(200*min(1,(yy-gtop)/(H*0.5)))
            od.line([(0,yy),(W,yy)],fill=(8,7,6,min(a,200)))
    img=_I.alpha_composite(img,ov).convert("RGB")
    d=_D.Draw(img)
    f=font("Bold",24); t=f"{i}/{n}"; tw=d.textlength(t,font=f); x,y0=W-M-tw,50
    d.rounded_rectangle([x-18,y0-10,x+tw+18,y0+34],22,fill=(0,0,0,150)); d.text((x,y0),t,font=f,fill=(255,255,255))

    def m(s):
        bs = max(BODY_MIN, min(BODY_MAX, round(s*0.607)))         # 34 при 56
        fh, fb, fbold = font("Black", s), font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d, title, fh, W-2*M)
        bl = e.wrap_rich(d, body, fb, W-2*M, fbold)
        lh, blh = round(s*1.25), round(bs*1.47)
        g1, g2 = round(s*0.857), round(s*0.5)                     # 48 / 28 при 56
        return dict(fh=fh, fb=fb, hl=hl, bl=bl, lh=lh, blh=blh, g1=g1, g2=g2,
                    dw=s, adv=round(s*0.714),                     # 40 при 56
                    block=g1 + len(hl)*lh + g2 + len(bl)*blh)

    # Потолок по высоте — не край кадра, а «блок не выше y=130»: текст прижат
    # низом (y = H-100-total), и без потолка длинный текст уползал бы под счётчик.
    # Кроме случая top=True (текст сверху) — там блок стартует от фиксированного
    # y=150, автокегль тот же, просто якорь другой.
    c = m(hs) if hs else autofit(m, 84, 34, TGT_PHOTO, H-100-SAFE_Y)
    fh, fb = c["fh"], c["fb"]
    hlines, blines = c["hl"], c["bl"]
    total = c["block"]
    y = y0 = (150 if top else H-100-total)
    dash(d,M,y,acc,c["dw"]); y+=c["adv"]
    # заголовок с тенью/плашками
    for line in hlines:
        cx=M+(e.CHIP_PAD if line and line[0][1]=="chip" else 0)
        for w_,st,ff in line:
            ww=d.textlength(w_,font=ff)
            if st=="chip":
                asc,desc=ff.getmetrics(); d.rounded_rectangle([cx-e.CHIP_PAD,y-4,cx+ww+e.CHIP_PAD,y+asc+desc],10,fill=acc); d.text((cx,y),w_,font=ff,fill=INK)
            else:
                col=acc if st=="acc" else (255,255,255)
                d.text((cx+3,y+4),w_,font=ff,fill=(0,0,0)); d.text((cx,y),w_,font=ff,fill=col)
            cx+=ww+d.textlength(" ",font=ff)
        y+=c["lh"]
    y+=c["g2"]
    sp=d.textlength(" ",font=fb)
    for line in blines:
        cx=M+(e.CHIP_PAD if line and line[0][1]=="chip" else 0)
        for w_,st,ff in line:
            ww=d.textlength(w_,font=ff)
            if st=="chip":
                asc,desc=ff.getmetrics(); d.rounded_rectangle([cx-e.CHIP_PAD,y-2,cx+ww+e.CHIP_PAD,y+asc+desc-6],8,fill=acc); d.text((cx,y),w_,font=ff,fill=INK)
            else:
                col=acc if st=="acc" else (232,229,225)
                d.text((cx+2,y+2),w_,font=ff,fill=(0,0,0)); d.text((cx,y),w_,font=ff,fill=col)
            cx+=ww+sp
        y+=c["blh"]
    img.save(out)
    return {"block_h": c["block"], "hs": c["fh"].size, "photo": photo,
            "text_bbox": (M, y0, W - M, y0 + total)}


# ══ РАСКЛАДКИ ПОВЕРХ ФОТО: СПИСОК И ПРИЗЫВ ══════════════════════════════════
# ЗАЧЕМ. До сих пор фото поверх всего кадра умел только photo_dark — заголовок
# плюс абзац. Чек-лист (checklist) и призыв (cta) рисовались ТОЛЬКО на белом,
# и в карусели, где фото просят на каждом слайде, середина проваливалась в три
# белых кадра подряд. split не выручает: он режет верхние 44% кадра, а
# вертикальный снимок с айфона отдаёт в эту полосу одну макушку.
#
# photo_dark не трогаем — он остаётся байт-в-байт. Общий кусок (кадр, градиент,
# счётчик) вынесен в _photo_base и используется только новыми раскладками.

def _photo_base(photo, i, n, dark=0.50, gtop=0.34):
    """Кадр на весь слайд + градиент снизу + счётчик. Возвращает (img, draw)."""
    from PIL import Image as _I, ImageDraw as _D
    img = e.fit(e.PHOTOS[photo], H, dark=dark).convert("RGBA")
    ov = _I.new("RGBA", (W, H), (0, 0, 0, 0)); od = _D.Draw(ov)
    g0 = int(H*gtop)
    for yy in range(g0, H):
        a = int(200*min(1, (yy-g0)/(H*0.5)))
        od.line([(0, yy), (W, yy)], fill=(8, 7, 6, min(a, 200)))
    img = _I.alpha_composite(img, ov).convert("RGB")
    d = _D.Draw(img)
    f = font("Bold", 24); t = f"{i}/{n}"
    tw = d.textlength(t, font=f); x, y0 = W-M-tw, 50
    d.rounded_rectangle([x-18, y0-10, x+tw+18, y0+34], 22, fill=(0, 0, 0, 150))
    d.text((x, y0), t, font=f, fill=(255, 255, 255))
    return img, d


def _rich_dark(d, lines, f, x, y, lh, acc, base):
    """Строки wrap_rich поверх фото: тень под буквой, плашка для *слова*."""
    for line in lines:
        cx = x + (e.CHIP_PAD if line and line[0][1] == "chip" else 0)
        for w_, st, ff in line:
            ww = d.textlength(w_, font=ff)
            if st == "chip":
                asc, desc = ff.getmetrics()
                d.rounded_rectangle([cx-e.CHIP_PAD, y-4, cx+ww+e.CHIP_PAD,
                                     y+asc+desc], 10, fill=acc)
                d.text((cx, y), w_, font=ff, fill=INK)
            else:
                col = acc if st == "acc" else base
                d.text((cx+2, y+3), w_, font=ff, fill=(0, 0, 0))
                d.text((cx, y), w_, font=ff, fill=col)
            cx += ww + d.textlength(" ", font=ff)
        y += lh
    return y


def photo_checklist(title, items, photo, acc, out, i=5, n=8, hs=None):
    """Чек-лист поверх фото. Кегли и промежутки — как в checklist, цвета — как
    в photo_dark (белый текст, тень, акцентный кружок буллета)."""
    img, d = _photo_base(photo, i, n)
    ind = 46

    def m(s):
        fh = font("Black", s)
        bs = max(26, min(BODY_MAX, int(s*0.56)))
        fb, fbold = font("Regular", bs), font("Bold", bs)
        hl = e.wrap_rich(d, title, fh, W-2*M)
        wr = [e.wrap_rich(d, it, fb, W-2*M-ind, fbold) for it in items]
        lh, blh, ig = int(s*1.16), int(bs*1.34), int(bs*0.85)
        block = 44 + len(hl)*lh + 40 + sum(len(w)*blh + ig for w in wr) - ig
        return dict(fh=fh, fb=fb, hl=hl, wr=wr, lh=lh, blh=blh, ig=ig,
                    block=block, bs=bs)

    c = m(hs) if hs else autofit(m, 92, 38, TGT_PHOTO, H-100-SAFE_Y)
    total = c["block"]
    y = top = H - 100 - total
    dash(d, M, y, acc, 64); y += 44
    y = _rich_dark(d, c["hl"], c["fh"], M, y, c["lh"], acc, (255, 255, 255)) + 40
    for wl in c["wr"]:
        r = int(c["bs"]*0.26)
        cy = y + (c["blh"] - 2*r)//2 - 2
        d.ellipse([M, cy, M+2*r, cy+2*r], fill=acc)
        _rich_dark(d, wl, c["fb"], M+ind, y, c["blh"], acc, (236, 233, 229))
        y += len(wl)*c["blh"] + c["ig"]
    img.save(out)
    return {"block_h": total, "hs": c["fh"].size, "photo": photo,
            "text_bbox": (M, top, W - M, top + total)}


def photo_cta(title, sub, word, photo, acc, out, i=8, n=8, sc=None,
              btn_fill=INK):
    """Призыв поверх фото: тот же центрированный блок, что в cta (заголовок,
    подзаголовок, плашка кодового слова), только на кадре, а не на белом."""
    img, d = _photo_base(photo, i, n, dark=0.58, gtop=0.20)
    has_word = bool(word and word.strip())

    def m(k):
        s = k/100.0
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

    c = m(round(sc*100)) if sc else autofit(m, 140, 62, TGT_PHOTO,
                                            H-100-SAFE_Y)
    fh, fs, fw, f2 = c["fh"], c["fs"], c["fw"], c["f2"]
    total = c["block"]
    y = top = H - 120 - total
    dw = c["dw"]; d.rectangle([(W-dw)//2, y, (W+dw)//2, y+c["dh"]], fill=acc)
    y += c["g1"]
    for l in c["hl"]:
        lw = d.textlength(l, font=fh)
        d.text(((W-lw)//2+3, y+4), l, font=fh, fill=(0, 0, 0))
        d.text(((W-lw)//2, y), l, font=fh, fill=(255, 255, 255))
        y += c["lh"]
    y += c["g2"]
    for l in c["sl"]:
        lw = d.textlength(l, font=fs)
        d.text(((W-lw)//2+2, y+3), l, font=fs, fill=(0, 0, 0))
        d.text(((W-lw)//2, y), l, font=fs, fill=(236, 233, 229))
        y += c["sub_lh"]
    if has_word:
        y += c["g3"]
        bw, bh = c["bw"], c["bh"]; bx = (W-bw)//2
        d.rounded_rectangle([bx, y, bx+bw, y+bh], c["rad"], fill=acc)
        ww = d.textlength(word, font=fw)
        d.text((bx+(bw-ww)//2, y+c["o1"]), word, font=fw, fill=btn_fill)
        w2 = d.textlength("написать в директ", font=f2)
        d.text((bx+(bw-w2)//2, y+c["o2"]), "написать в директ", font=f2,
               fill=btn_fill)
    img.save(out)
    return {"block_h": total, "sc": c["s"], "photo": photo,
            "text_bbox": (M, top, W - M, top + total)}
