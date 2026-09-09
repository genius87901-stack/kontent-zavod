# -*- coding: utf-8 -*-
"""
make_bank_catalog — витрина шаблонов каруселей ОДНИМ ЛИСТОМ, из настоящих работ.

ЗАЧЕМ. Клиент выбирает оформление каруселей до первой сборки. Названиями это
невозможно: «журнал · mint» ничего не говорит человеку, который не видел ни одного
слайда. Поэтому мастер сначала показывает картинку, и только потом спрашивает
(правило записано в install/box_root/CLAUDE.md и skills/install/SKILL.md).

ПОЧЕМУ ИЗ РАБОТ ВАЛЕРИИ, А НЕ ИЗ ДЕМО. Демо-слайд с текстом-рыбой показывает
раскладку, но не показывает ГЛАВНОГО — как стиль ведёт себя на живом фото, живом
хуке и живой длине текста. Клиент выбирает по реальной ленте, а такая витрина ещё
и доказывает, что шаблон рабочий: каждая плитка — опубликованный пост.

КОНТРАКТ. Скрипт НИЧЕГО не рендерит и движки не зовёт — он берёт УЖЕ собранные
слайды из out_c*/ и клеит лист. Значит: витрина ровно настолько свежая, насколько
свежи эти папки. Поменяли движок — сначала пересоберите нужную карусель её
собственным build_c*.py, потом гоните этот скрипт.

Запуск (из корня завода):
    python styles/make_bank_catalog.py                 # → ученикам/BANK_SHABLONOV.jpg
    python styles/make_bank_catalog.py --out ПУТЬ.jpg  # в другое место
    python styles/make_bank_catalog.py --check         # только проверить входы

Зависимости: PIL + engine4 (шрифт и палитра). Только то, что уже есть в коробке.
"""
from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import engine4 as e  # noqa: E402  (после правки sys.path)

# ── СОСТАВ ВИТРИНЫ ───────────────────────────────────────────────────────────
# Порядок здесь = порядок на листе. Каждая запись:
#   title    — как стиль зовут в разговоре с клиентом (он назовёт это вслух);
#   accent   — имя акцента из engine4.ACCENTS (сверяется, опечатка = ошибка);
#   engine   — каким движком собран (для подписи «из чего собрано»);
#   what     — одна строка: из чего состоит вид;
#   mood     — кому идёт: настроение + ниши;
#   src      — папка готовой карусели, откуда берём плитки;
#   cover    — файл-обложка; inner — файл внутреннего слайда.
# Пары «обложка + внутренний» обязательны: по одной обложке стиль не читается —
# именно внутренние слайды показывают, как стиль держит ТЕКСТ, а не фото.
STYLES = [
    {
        "title": "CREMA · coral",
        "accent": "coral",
        "engine": "engine_cream",
        "what": "тёплый кремовый фон, скруглённые фото, коралловый акцент",
        "mood": "мягко, премиально, женственно — коучи, психологи, wellness, бьюти",
        "src": "out_c39", "cover": "01.png", "inner": "04.png",
        "badge": "новый стиль",
    },
    {
        "title": "CREMA · визитка · coral",
        "accent": "coral",
        "engine": "engine_cream",
        "what": "фото без полей и скруглений на всю высоту, текстовая колонка с "
                "меткой и тонкой линией — карточка эксперта",
        "mood": "экспертная подача, консультации, личный бренд — эксперты, "
                "коучи, консультанты",
        "src": "out_c71", "cover": "01.png", "inner": "03.png",
        "badge": "новый стиль",
    },
    {
        "title": "ПРЕМИУМ-ФОТО · orange",
        "accent": "orange",
        "engine": "engine_photo",
        "what": "фото на всех слайдах + мягкое затемнение, текст поверх",
        "mood": "лайфстайл, эксперт, тепло — когда много живых кадров себя",
        "src": "out_c27", "cover": "01.png", "inner": "03.png",
    },
    {
        "title": "ПРЕМИУМ-ФОТО · red",
        "accent": "red",
        "engine": "engine_photo",
        "what": "фото на всех слайдах, красный акцент",
        "mood": "энергия, продажа, драйв — холодный трафик, запуски",
        "src": "out_c21", "cover": "01.png", "inner": "03.png",
    },
    {
        "title": "ЖУРНАЛ · mint",
        "accent": "mint",
        "engine": "engine_white",
        "what": "белый фон, фото на обложке, много воздуха",
        "mood": "спокойно, свежо, забота — здоровье, семья, услуги",
        "src": "out_c30", "cover": "01.png", "inner": "03.png",
    },
    {
        "title": "ЖУРНАЛ · lime",
        "accent": "lime",
        "engine": "engine_white",
        "what": "белый фон, лаймовый акцент",
        "mood": "дерзко, ярко, молодёжно — инфобиз, обучение, молодая ЦА",
        "src": "out_c40", "cover": "01.png", "inner": "03.png",
    },
    {
        "title": "ЖУРНАЛ · sky",
        "accent": "sky",
        "engine": "engine_white",
        "what": "белый фон, голубой акцент",
        "mood": "доверие, спокойствие — ИТ, финансы, B2B, консалтинг",
        "src": "out_c29", "cover": "01.png", "inner": "03.png",
    },
    {
        # Здесь стоял ЗЕМЛЯ · terracotta из c20. Убран из витрины: единственная
        # терракотовая карусель имеет на обложке вывеску чужого бренда во весь
        # кадр — в каталоге это читается как часть шаблона. Сам акцент из банка
        # никуда не делся, вернём строку, когда появится вторая такая карусель.
        # На её месте — самая свежая работа (06.08) и вторая половина пары
        # «один цвет в двух семействах»: сравнить с ПРЕМИУМ-ФОТО · red выше.
        "title": "ЖУРНАЛ · red",
        "accent": "red",
        "engine": "engine_white",
        "what": "белый фон, крупная обложка-фото, красный акцент",
        "mood": "тот же красный, но другое семейство — сравните с фото-версией выше",
        "src": "out_c44", "cover": "01.png", "inner": "03.png",
    },
    # Библиотека 18 стилей, этап 1/3 (15.08.2026) — все шесть собраны боевыми
    # каруселями на реальных фото и хуках Валерии (out_c59..c64), не демо.
    {
        "title": "РЕДАКТУРА · sand",
        "accent": "sand",
        "engine": "engine_editorial",
        "what": "фото на обложке, антиква/буквица, светлый бумажный разворот",
        "mood": "премиум-эксперт, журнальная подача — юриспруденция, консалтинг, люкс",
        "src": "out_c59", "cover": "01.png", "inner": "02.png",
        "badge": "новый стиль",
    },
    {
        "title": "БЕТОН · signal",
        "accent": "signal",
        "engine": "engine_brutalist",
        "what": "гигантская типографика впритык к полю, нулевые скругления",
        "mood": "молодёжно, дерзко — digital-агентства, стартапы, tech",
        "src": "out_c60", "cover": "01.png", "inner": "02.png",
        "badge": "новый стиль",
    },
    {
        "title": "ДНЕВНИК · clay",
        "accent": "clay",
        "engine": "engine_scrapbook",
        "what": "полароиды под углом, рукописный акцент, тёплая бумага",
        "mood": "лайфстайл, личный, тёплый — travel, хендмейд, семейные ниши",
        "src": "out_c61", "cover": "01.png", "inner": "02.png",
        "badge": "новый стиль",
    },
    {
        "title": "МАНИФЕСТ · gold",
        "accent": "gold",
        "engine": "engine_quote",
        "what": "одна цитата на слайд крупным серифом на тёмном фоне",
        "mood": "психология, философия — медитативный ритм, пауза между мыслями",
        "src": "out_c62", "cover": "01.png", "inner": "02.png",
        "badge": "новый стиль",
    },
    {
        "title": "СТИКЕР · bubblegum",
        "accent": "bubblegum",
        "engine": "engine_sticker",
        "what": "рамки-стикеры как в сторис, опрос-пилюли, векторные акценты",
        "mood": "нативно, молодёжно — личный бренд, блогеры",
        "src": "out_c63", "cover": "01.png", "inner": "02.png",
        "badge": "новый стиль",
    },
    {
        "title": "РЕПЛИКА · flash",
        "accent": "flash",
        "engine": "engine_meme",
        "what": "жёлтая плашка-цитата или скриншот-рамка, жирный гротеск капсом",
        "mood": "дерзко, разговорно — маркетологи, продажи, личный бренд экспертов",
        "src": "out_c64", "cover": "01.png", "inner": "02.png",
        "badge": "новый стиль",
    },
    {
        "title": "МИНИМАЛИЗМ",
        "accent": "coral",
        "engine": "engine_minimal",
        "what": "натуральный кадр без грейда, короткая подпись внизу, стрелка «листай»",
        "mood": "спокойно, дорого-незаметно — эксперты, у которых говорит сам кадр",
        "src": "out_c93", "cover": "01.png", "inner": "03.png",
        "badge": "без цвета по замыслу",
    },
    {
        "title": "ТИШИНА · azure",
        "accent": "azure",
        "engine": "engine_swiss",
        "what": "широкие поля, 15–20 слов на слайд, акцент одной точкой",
        "mood": "воздух и пауза — консультанты, архитекторы, всё премиальное",
        "src": "out_c80", "cover": "01.png", "inner": "02.png",
    },
    {
        "title": "КАДР · amber",
        "accent": "amber",
        "engine": "engine_cinematic",
        "what": "леттербокс, зерно, подпись субтитром вразрядку",
        "mood": "кинематографично — режиссёры, фотографы, авторские проекты",
        "src": "out_c81", "cover": "01.png", "inner": "02.png",
    },
    {
        "title": "ПАНОРАМА · dune",
        "accent": "dune",
        "engine": "engine_panorama",
        "what": "один кадр, панорамирующий по слайдам смещающимся окном",
        "mood": "плавно, как проезд камеры — travel, интерьеры, витрины",
        "src": "out_c82", "cover": "01.png", "inner": "02.png",
    },
    {
        "title": "РАЗВИЛКА · meadow",
        "accent": "meadow",
        "engine": "engine_split",
        "what": "экран пополам: «как все» серым слева, «как надо» акцентом справа",
        "mood": "спорно и наглядно — методологи, наставники, продажи через контраст",
        "src": "out_c84", "cover": "01.png", "inner": "02.png",
    },
    {
        "title": "ЦИФРА · cobalt",
        "accent": "cobalt",
        "engine": "engine_data",
        "what": "тёмный фон, гигантские числа моноширинным, мини-график",
        "mood": "доказательно — аналитика, финансы, отчёты о результатах",
        "src": "out_c85", "cover": "01.png", "inner": "02.png",
    },
    {
        "title": "ЧЕК-ЛИСТ · sprout",
        "accent": "sprout",
        "engine": "engine_checklist",
        "what": "чекбоксы и прогресс «k из N», вид рабочей тетради",
        "mood": "прикладно — всё, что сохраняют и возвращаются выполнять",
        "src": "out_c70", "cover": "01.png", "inner": "02.png",
    },
    {
        "title": "ЛЮБОВЬ 1 · vanilla",
        "accent": "vanilla",
        "engine": "engine_love",
        "what": "фото во весь слайд, крупные прописные, вывод рукописной строкой",
        "mood": "тепло и лично — женский личный бренд, наставничество",
        "src": "out_styles_preview/love1", "cover": "01.png", "inner": "02.png",
        "badge": "образец",
    },
    {
        "title": "ЛЮБОВЬ 2 · vanilla",
        "accent": "vanilla",
        "engine": "engine_love",
        "what": "то же фото во весь слайд, но ключевое слово вынесено на плашку",
        "mood": "мягче и разговорнее первой — списки и разборы по шагам",
        "src": "out_styles_preview/love2", "cover": "01.png", "inner": "02.png",
        "badge": "образец",
    },
    {
        "title": "ЛЮБОВЬ 3 · vanilla",
        "accent": "vanilla",
        "engine": "engine_love",
        "what": "кадр остаётся дневным, ключевая строка на светлой таблетке",
        "mood": "легко и днём — lifestyle, бьюти, всё, что не любит затемнения",
        "src": "out_styles_preview/love3", "cover": "01.png", "inner": "02.png",
        "badge": "образец",
    },
    {
        "title": "МАРКЕР · red",
        "accent": "red",
        "engine": "engine_marker",
        "what": "заголовок одной плашкой-маркером, разбор белым под ней",
        "mood": "жёстко и по делу — списки, разборы, всё с нумерацией",
        "src": "out_c87", "cover": "01.png", "inner": "02.png",
    },
]

# ── ГЕОМЕТРИЯ ЛИСТА ──────────────────────────────────────────────────────────
TILE_W = 470                     # ширина плитки-слайда (слайд 1080x1350 → 4:5)
TILE_H = round(TILE_W * 1350 / 1080)
GAP = 22                         # зазор между плитками
PAD = 28                         # поле листа
HEAD_H = 132                     # шапка блока стиля (название + две строки)
SHEET_W = PAD * 2 + TILE_W * 2 + GAP

BG = (18, 16, 15)                # фон листа — тёмный, чтобы белые слайды не сливались
FG = (248, 246, 244)
DIM = (150, 144, 138)
CARD = (30, 27, 25)


def _font(weight, size):
    return e.font(weight, size)


def _tile(path, label):
    """Плитка: слайд + подпись-ярлык в углу («обложка» / «внутр. слайд»)."""
    im = Image.open(path).convert("RGB").resize((TILE_W, TILE_H), Image.LANCZOS)
    d = ImageDraw.Draw(im, "RGBA")
    f = _font("Bold", 19)
    tw = d.textlength(label, font=f)
    d.rounded_rectangle((12, 12, 12 + tw + 26, 48), 10, fill=(18, 16, 15, 215))
    d.text((25, 20), label, font=f, fill=FG)
    return im


def _fit(draw, text, font, max_w):
    """Обрезать строку многоточием, если не влезает в ширину."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    while len(text) > 4 and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text.rstrip(" ,") + "…"


def check(styles=STYLES):
    """Проверить входы ДО отрисовки. Возвращает список проблем (пусто = всё есть)."""
    problems = []
    for s in styles:
        if s["accent"] not in e.ACCENTS:
            problems.append("%s: акцента '%s' нет в engine4.ACCENTS"
                            % (s["title"], s["accent"]))
        for key in ("cover", "inner"):
            p = os.path.join(ROOT, s["src"], s[key])
            if not os.path.isfile(p):
                problems.append("%s: нет файла %s" % (s["title"], p))
    return problems


def build(out_path, styles=STYLES):
    rows = len(styles)
    sheet_h = PAD + 150 + rows * (HEAD_H + TILE_H + GAP * 2)
    sheet = Image.new("RGB", (SHEET_W, sheet_h), BG)
    d = ImageDraw.Draw(sheet)

    d.text((PAD, PAD), "БАНК ШАБЛОНОВ КАРУСЕЛЕЙ",
           font=_font("Black", 52), fill=FG)
    # две строки, а не одна: в одну подзаголовок не влезает по ширине листа и
    # обрезается ровно на самом важном — «настоящие посты».
    d.text((PAD, PAD + 64), "выбери стиль — завод собирает все твои карусели в нём",
           font=_font("Regular", 22), fill=DIM)
    d.text((PAD, PAD + 96), "все примеры ниже — настоящие опубликованные посты",
           font=_font("Regular", 22), fill=DIM)

    y = PAD + 150
    for s in styles:
        acc = e.ACCENTS[s["accent"]]
        d.rounded_rectangle((PAD - 12, y - 10, SHEET_W - PAD + 12,
                             y + HEAD_H + TILE_H + GAP), 16, fill=CARD)
        # цветная метка акцента — тот самый цвет, что появится на слайдах
        d.rounded_rectangle((PAD, y + 8, PAD + 10, y + 46), 5, fill=acc)

        f_title = _font("Black", 34)
        d.text((PAD + 26, y + 6), s["title"], font=f_title, fill=FG)
        if s.get("badge"):
            bx = PAD + 26 + d.textlength(s["title"], font=f_title) + 16
            fb = _font("Bold", 18)
            bw = d.textlength(s["badge"], font=fb)
            d.rounded_rectangle((bx, y + 14, bx + bw + 22, y + 44), 9, fill=acc)
            d.text((bx + 11, y + 20), s["badge"], font=fb, fill=e.INK)

        f_meta = _font("Regular", 21)
        d.text((PAD + 26, y + 54),
               _fit(d, s["what"], f_meta, SHEET_W - PAD * 2 - 40),
               font=f_meta, fill=(214, 208, 202))
        d.text((PAD + 26, y + 86),
               _fit(d, "кому: " + s["mood"], f_meta, SHEET_W - PAD * 2 - 40),
               font=f_meta, fill=DIM)

        ty = y + HEAD_H
        sheet.paste(_tile(os.path.join(ROOT, s["src"], s["cover"]), "обложка"),
                    (PAD, ty))
        sheet.paste(_tile(os.path.join(ROOT, s["src"], s["inner"]), "внутр. слайд"),
                    (PAD + TILE_W + GAP, ty))
        y = ty + TILE_H + GAP * 2

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    sheet.save(out_path, "JPEG", quality=88, optimize=True)
    return sheet.size


def main(argv=None):
    ap = argparse.ArgumentParser(description="Витрина шаблонов каруселей одним листом.")
    ap.add_argument("--out", default=os.path.join(ROOT, "ученикам", "BANK_SHABLONOV.jpg"),
                    help="куда положить лист (по умолчанию ученикам/BANK_SHABLONOV.jpg)")
    ap.add_argument("--check", action="store_true",
                    help="только проверить входы, ничего не рисовать")
    args = ap.parse_args(argv)

    problems = check()
    if problems:
        print("Витрину собрать нельзя:")
        for p in problems:
            print("  - " + p)
        return 2
    if args.check:
        print("Входы на месте: %d стилей, все слайды найдены." % len(STYLES))
        return 0

    size = build(args.out)
    print("Готово: %s  (%dx%d, стилей: %d)" % (args.out, size[0], size[1], len(STYLES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
