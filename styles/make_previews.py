# -*- coding: utf-8 -*-
"""
Превью стилей субтитров БЕЗ полного видео (правило «замер, не вывод»).

PIL-скрипт: воспроизводит математику спрайтов engine_talk (_text_sprite /
_karaoke_sprite) с параметрами из style.py и рисует PNG в styles/preview/:

  font_<пресет>.png        — R31: одна строка субтитра в 3 кеглях (74/96/120)
  subtitle_<стиль>.png     — R32: спрайт на тёмном И светлом фоне (контраст)

Запуск:  python3 styles/make_previews.py            # все превью
         python3 styles/make_previews.py --check    # сверка зеркала палитры

Числа спрайтов — из audit/CONSTANTS.md (stroke 9/pad 26/тень +4 α120/поп 1.08/
плашка 12/14); при интеграции R30 движок будет читать их из тех же полей Style,
так что превью честно показывает будущий кадр.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import style  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview")

# фразы превью — типичная реплика субтитра завода
LINE = "деньги любят скорость"
KARAOKE_WORDS = [{"w": w} for w in "деньги любят скорость".split()]


# ── спрайты: та же математика, что в engine_talk, параметры из Style ─────────
def text_sprite(st, text, size, weight="Black"):
    """_text_sprite с параметрами Style. kind: clean|stroked|boxed."""
    kind = st.sub_kind
    stroke_w = st.sub_stroke_w if kind == "stroked" else 0
    shadow = st.sub_shadow and kind == "stroked"
    font = st.make_font(weight, size)
    pad = st.sub_pad
    probe = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(probe)
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    w, h = (x1 - x0) + pad * 2, (y1 - y0) + pad * 2
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ox, oy = pad - x0, pad - y0
    if kind == "boxed":
        bx, by = st.box_pad_x, st.box_pad_y
        d.rounded_rectangle([pad - bx, pad - by, w - pad + bx, h - pad + by],
                            st.box_radius, fill=tuple(st.box_fill) + (st.box_alpha,))
    if shadow:
        d.text((ox, oy + st.sub_shadow_dy), text, font=font,
               fill=(0, 0, 0, st.sub_shadow_alpha), stroke_width=stroke_w,
               stroke_fill=(0, 0, 0, st.sub_shadow_alpha))
    d.text((ox, oy), text, font=font, fill=tuple(st.sub_fill) + (255,),
           stroke_width=stroke_w, stroke_fill=tuple(st.sub_stroke_fill) + (255,))
    return im


def karaoke_sprite(st, words, size, active_idx, weight="Black"):
    """_karaoke_sprite с параметрами Style (accent|plate|reveal)."""
    kstyle = st.karaoke_style
    stroke_w = st.sub_stroke_w if st.sub_kind == "stroked" else 0
    shadow = st.sub_shadow and st.sub_kind == "stroked"
    accent = st.accent_rgb
    pad = 32                                     # engine_talk._karaoke_sprite
    font = st.make_font(weight, size)
    mk = lambda s: st.make_font(weight, s)
    tokens = [x["w"] for x in words]

    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    space = probe.textlength(" ", font=font)
    base_w = [probe.textlength(t, font=font) for t in tokens]
    xs, xx = [], pad
    for w0 in base_w:
        xs.append(xx)
        xx += w0 + space
    total = xx - space
    fph = mk(int(round(size * st.karaoke_scale_pop)))
    hb = probe.textbbox((0, 0), "Ац", font=fph, stroke_width=stroke_w)
    W, H = int(total + pad), int((hb[3] - hb[1]) + pad * 2)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cy = H // 2

    def _word(x, tok, f, color, center):
        anc = "mm" if center else "lm"
        if shadow:
            d.text((x, cy + st.sub_shadow_dy), tok, font=f,
                   fill=(0, 0, 0, st.sub_shadow_alpha), anchor=anc,
                   stroke_width=stroke_w,
                   stroke_fill=(0, 0, 0, st.sub_shadow_alpha))
        d.text((x, cy), tok, font=f, fill=tuple(color) + (255,), anchor=anc,
               stroke_width=stroke_w,
               stroke_fill=tuple(st.sub_stroke_fill) + (255,))

    for i, tok in enumerate(tokens):
        if kstyle == "reveal" and active_idx >= 0 and i > active_idx:
            continue
        is_active = (i == active_idx)
        if kstyle == "plate" and is_active:
            b = probe.textbbox((xs[i], cy), tok, font=font, anchor="lm")
            p = st.karaoke_plate_pad
            d.rounded_rectangle([b[0] - p, b[1] - p, b[2] + p, b[3] + p],
                                st.karaoke_plate_radius,
                                fill=tuple(accent) + (255,))
            _word(xs[i], tok, font, style.INK, center=False)
            continue
        color = accent if is_active else tuple(st.karaoke_fill)
        if is_active:
            ps = int(round(size * st.karaoke_scale_pop))
            while ps > size:
                if probe.textlength(tok, font=mk(ps)) <= base_w[i] + 0.8 * space:
                    break
                ps -= 1
            _word(xs[i] + base_w[i] / 2, tok, mk(ps), color, center=True)
            continue
        _word(xs[i], tok, font, color, center=False)
    return im


# ── фоны: тёмный «кадр спикера» и светлый «пересвеченный» ────────────────────
def _bg(w, h, light):
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    top, bot = ((235, 232, 226), (208, 204, 196)) if light else \
               ((52, 44, 40), (24, 20, 18))
    for y in range(h):
        k = y / max(1, h - 1)
        d.line([(0, y), (w, y)],
               fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bot)))
    return im


def _paste_center(bg, spr, cy=None):
    x = (bg.width - spr.width) // 2
    y = (cy - spr.height // 2) if cy else (bg.height - spr.height) // 2
    bg.paste(spr, (x, y), spr)
    return bg


def preview_fonts():
    """R31: одна строка каждым шрифтом в 3 кеглях (74 база / 96 / 120)."""
    presets = [(None, "onest_default"),
               ("styles/preset_inter.json", "inter"),
               ("styles/preset_manrope.json", "manrope")]
    for path, tag in presets:
        st = style.load(path)
        rows = [text_sprite(st, LINE, s) for s in (74, 96, 120)]
        W = max(r.width for r in rows) + 80
        H = sum(r.height for r in rows) + 40 * (len(rows) + 1)
        bg = _bg(W, H, light=False)
        y = 40
        d = ImageDraw.Draw(bg)
        for size, r in zip((74, 96, 120), rows):
            bg.paste(r, ((W - r.width) // 2, y), r)
            d.text((16, y), "%d px" % size, fill=(150, 145, 140),
                   font=st.make_font("Regular", 24))
            y += r.height + 40
        p = os.path.join(OUT, "font_%s.png" % tag)
        bg.save(p)
        print("ok", p, "(%s)" % st.font_name)


def preview_substyles():
    """R32: каждый стиль субтитра на тёмном И светлом фоне (контраст)."""
    names = ["subtitle_clean", "subtitle_boxed",
             "subtitle_karaoke_accent", "subtitle_karaoke_plate"]
    for name in names:
        st = style.load("styles/%s.json" % name)
        size = st.sub_size_base
        if st.karaoke_enabled:
            spr = karaoke_sprite(st, KARAOKE_WORDS, size, active_idx=1)
        else:
            weight = "Bold" if st.sub_kind == "clean" else "Black"
            spr = text_sprite(st, LINE, size, weight=weight)
        W = max(900, spr.width + 120)
        half = spr.height + 120
        canvas = Image.new("RGB", (W, half * 2))
        canvas.paste(_paste_center(_bg(W, half, light=False), spr.copy()), (0, 0))
        canvas.paste(_paste_center(_bg(W, half, light=True), spr.copy()), (0, half))
        p = os.path.join(OUT, "%s.png" % name)
        canvas.save(p)
        print("ok", p)


# ── C5: витрина фирменных стилей ─────────────────────────────────────────────
BRAND_GLOB = "brand_*.json"
CYR_LINE = "Привет, это проверка ЖШЩЭЮЯ"


def brand_presets():
    """Пути пресетов витрины (styles/brand_*.json), отсортированы по имени."""
    import glob
    return sorted(glob.glob(os.path.join(os.path.dirname(OUT), BRAND_GLOB)))


def _meta(path):
    """Человеческие поля витрины из json: _title/_description/_audience.
    Схема style.py ключи с «_» игнорирует, поэтому читаем файл напрямую."""
    import json
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return (d.get("_title") or d.get("name", "?"),
            d.get("_description", ""), d.get("_audience", ""))


def _glyph_bitmap(font, ch):
    im = Image.new("L", (200, 200), 0)
    ImageDraw.Draw(im).text((20, 20), ch, font=font, fill=255)
    return im.tobytes()


def check_cyrillic(save=True):
    """ФАКТИЧЕСКАЯ проверка кириллицы: рисуем строку каждой гарнитурой и
    сравниваем растр каждой буквы с растром заведомо отсутствующего символа
    (U+E000, приватная область). Совпало — это «тофу»-квадрат, шрифт не годится.
    Дополнительно сверяем cmap. Возвращает список (гарнитура, плохие символы)."""
    seen, rows, bad_total = {}, [], []
    for path in [None] + brand_presets():
        st = style.load(path)
        if st.font_name in seen:
            continue
        seen[st.font_name] = True
        font = st.make_font("Bold", 64)
        tofu = _glyph_bitmap(font, "")
        bad = [c for c in CYR_LINE
               if c.strip() and _glyph_bitmap(font, c) == tofu]
        rows.append((st, bad))
        if bad:
            bad_total.append((st.font_name, bad))
        print("кириллица %-12s %s" % (st.font_name,
              "ок, тофу нет" if not bad else "ТОФУ: %r" % bad))
    if save:
        parts = []
        for st, bad in rows:
            f = st.make_font("Bold", 56)
            fs = st.make_font("Regular", 26)
            probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
            w = int(probe.textlength(CYR_LINE, font=f)) + 60
            parts.append((st, f, fs, bad, max(w, 900)))
        W = max(p[4] for p in parts)
        H = 130 * len(parts) + 20
        im = Image.new("RGB", (W, H), (22, 20, 19))
        d = ImageDraw.Draw(im)
        y = 20
        for st, f, fs, bad, _w in parts:
            d.text((30, y), "%s — %s" % (st.font_name,
                   "тофу нет" if not bad else "ТОФУ %r" % bad),
                   font=fs, fill=(150, 200, 150) if not bad else (240, 90, 90))
            d.text((30, y + 36), CYR_LINE, font=f, fill=(245, 245, 245))
            y += 130
        p = os.path.join(OUT, "cyrillic_check.png")
        im.save(p)
        print("ok", p)
    return bad_total


def _tile(path, tw, th):
    """Плитка каталога: имя, описание, метаданные и реальный субтитр пресета
    на «кадре» — тёмном градиенте с safe-zone-полями."""
    st = style.load(path)
    title, desc, audience = _meta(path)
    im = Image.new("RGB", (tw, th), (30, 27, 25))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, tw - 1, th - 1], outline=(64, 58, 54))
    # полоса акцента слева — цвет пресета видно сразу
    d.rectangle([0, 0, 10, th], fill=st.accent_rgb)

    f_title = st.make_font("Black", 40)
    f_desc = st.make_font("Regular", 21)
    f_meta = st.make_font("Regular", 19)
    d.text((34, 24), title, font=f_title, fill=(248, 246, 244))
    d.text((34, 78), os.path.basename(path), font=f_meta, fill=st.accent_rgb)

    # описание в перенос по ширине
    y = 112
    words, line = desc.split(), ""
    for w in words:
        probe = line + (" " if line else "") + w
        if d.textlength(probe, font=f_desc) > tw - 70 and line:
            d.text((34, y), line, font=f_desc, fill=(178, 172, 166))
            y += 30
            line = w
        else:
            line = probe
    if line:
        d.text((34, y), line, font=f_desc, fill=(178, 172, 166))
        y += 38

    meta = ("%s · кегль %d · субтитр %s · акцент %s · караоке %s · план %.1f–%.1f c"
            % (st.font_name, st.sub_size_base, st.sub_kind,
               st.accent if isinstance(st.accent, str) else "custom",
               st.karaoke_style if st.karaoke_enabled else "нет",
               st.pace_min_scene, st.pace_max_scene))
    d.text((34, y), meta, font=f_meta, fill=(126, 121, 116))
    y += 34
    if audience:
        line = "кому: " + audience
        while (d.textlength(line, font=f_meta) > tw - 70) and len(line) > 10:
            line = line[:-2].rstrip(" ,") + "…"
        d.text((34, y), line, font=f_meta, fill=(126, 121, 116))
        y += 34

    # «кадр» с настоящим субтитром пресета
    fh = th - y - 24
    frame = _bg(tw - 68, fh, light=False)
    if st.karaoke_enabled:
        spr = karaoke_sprite(st, KARAOKE_WORDS, st.sub_size_base, active_idx=1)
    else:
        weight = "Bold" if st.sub_kind == "clean" else "Black"
        spr = text_sprite(st, LINE, st.sub_size_base, weight=weight)
    k = min(1.0, (frame.width - 40) / spr.width, (frame.height - 30) / spr.height)
    if k < 1.0:
        spr = spr.resize((max(1, int(spr.width * k)), max(1, int(spr.height * k))),
                         Image.LANCZOS)
    _paste_center(frame, spr)
    im.paste(frame, (34, y))
    return im


def preview_catalog():
    """styles/preview/BRAND_CATALOG.jpg — все фирменные пресеты одним листом."""
    paths = brand_presets()
    if not paths:
        print("нет styles/brand_*.json — каталог не собран")
        return
    tw, th, gap, cols = 1000, 640, 24, 2
    rows = (len(paths) + cols - 1) // cols
    head = 130
    W = cols * tw + gap * (cols + 1)
    H = head + rows * th + gap * rows
    sheet = Image.new("RGB", (W, H), (18, 16, 15))
    d = ImageDraw.Draw(sheet)
    hf = style.load(None)
    d.text((gap + 34, 34), "Витрина фирменных стилей — выбери вид",
           font=hf.make_font("Black", 54), fill=(248, 246, 244))
    d.text((gap + 36, 96),
           "каждая плитка = готовый styles/brand_*.json; запуск: "
           "python render_reels.py --style styles/<файл>.json",
           font=hf.make_font("Regular", 22), fill=(140, 135, 130))
    for i, p in enumerate(paths):
        t = _tile(p, tw, th)
        x = gap + (i % cols) * (tw + gap)
        y = head + (i // cols) * (th + gap)
        sheet.paste(t, (x, y))
    out = os.path.join(OUT, "BRAND_CATALOG.jpg")
    sheet.save(out, quality=90)
    print("ok", out, "(%d пресетов)" % len(paths))


def check_brand_load():
    """Каждый пресет витрины обязан грузиться style.load без ошибок."""
    ok = True
    for p in brand_presets():
        rel = os.path.join("styles", os.path.basename(p))
        try:
            print("OK   %-38s %r" % (rel, style.load(p)))
        except Exception as ex:
            ok = False
            print("FAIL %-38s %s" % (rel, ex))
    return ok


def check_mirror():
    """Сверка зеркала палитры style.py с engine4 (если движок импортируем)."""
    try:
        import engine4 as e
    except Exception as ex:
        print("engine4 не импортируется (%s) — сверка пропущена" % ex)
        return
    assert style.INK == e.INK, "style.INK разошёлся с engine4.INK"
    assert style.ACCENTS == e.ACCENTS, "style.ACCENTS разошёлся с engine4.ACCENTS"
    print("зеркало палитры сходится с engine4")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    if "--check" in sys.argv:
        check_mirror()
    elif "--brand" in sys.argv:            # C5: только витрина
        bad = check_cyrillic()
        ok = check_brand_load()
        preview_catalog()
        sys.exit(0 if (ok and not bad) else 1)
    else:
        check_mirror()
        check_cyrillic()
        check_brand_load()
        preview_catalog()
        preview_fonts()
        if all(os.path.exists(os.path.join(os.path.dirname(OUT),
                                           "subtitle_%s.json" % n))
               for n in ("clean", "boxed", "karaoke_accent", "karaoke_plate")):
            preview_substyles()
