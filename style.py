# -*- coding: utf-8 -*-
"""
style.py — слой визуального стиля клиента для Reels-движка (R30, AUDIT.md Часть 4).

Идея: все «магические» визуальные константы engine_talk.py (audit/CONSTANTS.md)
собраны в ОДИН объект Style, который грузится из json-пресета клиента.
Дефолт = ТЕКУЩИЕ значения движка байт-в-байт: load(None) и load("styles/default.json")
обязаны давать одинаковый Style, а движок с ним — прежнюю картинку.

Контракт:
    import style
    st = style.load()                       # дефолт (= константы engine_talk)
    st = style.load("styles/preset_inter.json")   # пресет клиента
    f  = st.make_font("Black", 74)          # PIL-шрифт гарнитуры стиля

Пресет-json может быть ЧАСТИЧНЫМ: указываются только отличия от дефолта,
вложенные блоки сливаются поверх дефолтных (см. _merge). Неизвестные ключи —
ошибка (защита от опечаток); ключи с ведущим «_» («_note» и т.п.) — комментарии
автора пресета и схемой не проверяются. Все ошибки — StyleError с путём до поля.

Зависимости: только stdlib + PIL (правило коробки).
"""

import copy
import json
import os

from PIL import ImageFont

# корень проекта = папка этого файла; все пути в json — относительно него
ROOT = os.path.dirname(os.path.abspath(__file__))

VW, VH = 1080, 1920                 # размер кадра Reels (engine_talk.py:97)

# Зеркало engine4.INK / engine4.ACCENTS (единственный источник — engine4.py;
# зеркалим, чтобы style.py грузился без cv2-стека. При изменении палитры в
# engine4 обновить и здесь — сверку делает styles/make_previews.py --check).
INK = (26, 22, 20)
ACCENTS = {
    "terracotta": (201, 113, 78),
    "lime":       (204, 255, 0),
    "mint":       (94, 214, 184),
    "orange":     (240, 138, 74),
    "red":        (228, 16, 43),
    "sky":        (108, 168, 255),
}

SUB_KINDS = ("clean", "stroked", "boxed")
# регистр субтитра-реплики и выравнивание блока — новые ОПциональные оси стиля.
# Дефолты (keep/center/cy_force=False) дают прежнее поведение движка байт-в-байт.
SUB_CASES = ("keep", "upper")             # keep — как в тексте; upper — ПРОПИСНЫЕ
SUB_ALIGNS = ("center", "left", "right")  # прижим блока субтитра к краю зоны
KARAOKE_STYLES = ("accent", "plate", "reveal")
FONT_WEIGHT_NAMES = ("Regular", "SemiBold", "Bold", "Black")   # имена движка

# ── ДЕФОЛТ = текущие константы движка (audit/CONSTANTS.md) ───────────────────
# Каждое число подписано строкой-источником в engine_talk.py / talk_plan.py /
# render_reels.py. Менять только синхронно с движком.
_DEFAULT = {
    "name": "default",
    "font_family": {
        "name": "Onest",                       # engine4.FAMILY
        "variable": "fonts/Onest.ttf",         # engine4._ONEST (вариативный)
        "static": {},                          # либо файлы на каждый вес
        "weights": {"Regular": 400, "SemiBold": 600,
                    "Bold": 700, "Black": 900},  # engine4._WEIGHT
        "axes": {},                            # прочие оси вариативного файла,
                                               # напр. {"Optical size": 32} у Inter
        "license": "fonts/OFL-Onest.txt",      # SIL OFL рядом с файлом
    },
    # R7 (слито в движок после написания R30): базовый кегль обычных раскладок
    # поднят 74→100 (5.2% высоты кадра), а split и ×N-путь оставлены на 74 —
    # у шва свой стиль (реф. Пример2), а demo/compare калиброваны как 74×3=222.
    # Поэтому кеглей ДВА, и оба обязаны совпадать с engine_talk.render:1333-1334.
    # T7 (владелец, показ split/card/pip Валерии): «шрифт субтитров крупнее в
    # 2 раза». Кегль автоподбирается под безопасную ширину (engine_talk._fit_size
    # берёт ЭТУ константу как верхнюю границу hi), поэтому правка — здесь, ОДНА
    # точка на все три раскладки (card/pip идут по sub_size_base, split — по
    # sub_size_split), а не подгонка под конкретный текст. 100→200 / 74→148,
    # ровно ×2. sub_size_floor (пол автоподбора при слишком длинной реплике) НЕ
    # тронут нарочно: это аварийный минимум читаемости при нехватке безопасной
    # ширины, а не часть видимого базового кегля — см. T7-style-tweaks.md §1.
    # ВЕРТИКАЛЬНОЕ ПОЛОЖЕНИЕ ЛИЦА на полнокадровых кусках спикера, доля
    # высоты кадра (0 = выключено, прежний cover-кроп байт-в-байт).
    # Зачем: подпись стоит на 0.4536 (референс владельца), а простой
    # cover-кроп ставит лицо по центру — подпись падает на рот. Замер
    # 28.08: задето 6 реплик из 45, до 51% лица. Поднимаем лицо выше
    # подписи. Кадры со вставкой это не трогает — там своё кадрирование.
    "speaker_face_y": 0.0,
    # ШИРОКИЙ ПЛАН спикера на полнокадровых раскладках: доля кадра, до
    # которой уменьшается человек (1.0 = выключено, прежний кадр
    # байт-в-байт). Нужен там, где съёмка 9:16 и выход 9:16: cover берёт
    # кадр целиком, слака для сдвига лица нет, и подпись некуда увести.
    "speaker_wide": 1.0,
    "sub_size_base": 200,                      # обычные раскладки (A/B/cutout/…)
    "sub_size_split": 148,                     # split и ×N (sub_scale>1)
    "sub_size_floor": 34,                      # пол автокегля (:1387 size<=34)
    # R7+R8+R42 (слито после R30): реплика выросла 2/15 → 3/24, разрыв по знакам
    # препинания. Числа обязаны совпадать с chunk_cues(:158) — иначе стиль-дефолт
    # изменит нарезку и сломает байт-в-байт.
    "sub_chunk": {"max_words": 3, "max_chars": 24},   # chunk_cues (:158)
    "sub_style": {
        "kind": "stroked",                     # A/B/cutout: Black+обводка+тень
        # E4-49: было [245,245,245]. Единый белый субтитра завода —
        # engine_talk.WHITE = 255; см. комментарий в karaoke.fill ниже.
        "fill": [255, 255, 255],               # engine_talk.WHITE
        "stroke_w": 9,                         # _text_sprite (:277)
        "stroke_fill": "ink",                  # обводка цветом INK
        "pad": 26,                             # поле спрайта (:277)
        "shadow": True,                        # тень (:290)
        "shadow_dy": 4,                        # смещение тени (:290)
        "shadow_alpha": 120,                   # альфа тени (:290)
        "line_gap": 1.12,                      # межстрочный ×N (:316)
        # boxed (подложка-плашка за всем блоком) — параметры для kind="boxed";
        # в дефолте не активны (kind="stroked"), числа — стартовые для R32.
        "box_fill": [0, 0, 0],
        "box_alpha": 160,
        "box_pad_x": 28,
        "box_pad_y": 16,
        "box_radius": 18,
        # split-шаблон исторически рисует ЧИСТЫЙ белый без обводки/тени
        # (реф. Пример2). True = прежнее поведение движка байт-в-байт.
        "split_uses_clean": True,
        # РЕГИСТР субтитра-реплики: "keep" (дефолт) — как в исходном тексте,
        # движок регистр НЕ трогает (байт-в-байт); "upper" — поднять в ПРОПИСНЫЕ.
        # (Заголовок формата text-behind живёт отдельно, тут ни при чём.)
        "case": "keep",
        # ВЫРАВНИВАНИЕ блока субтитра: "center" (дефолт, как сейчас) | "left" |
        # "right". left/right прижимают блок к краю безопасной зоны (safe_left+
        # margin слева, VW-safe_right-margin справа) — не под кнопки интерфейса.
        "align": "center",
        # ПОЗИЦИЯ ПОВЕРХ ЗАЩИТЫ ОТ ЛИЦА: False (дефолт) — движок сам уводит
        # субтитр от лица (_place_sub_cy); True — ставить РОВНО в sub_cy из
        # пресета, без ухода от лица (только clamp по краям кадра остаётся).
        "cy_force": False,
        # ЗАМОК ПОЗИЦИИ (владелец 28.08.2026): "текст всегда в одном месте
        # и никуда не скачет". True — подпись НЕ уводится от лица вообще,
        # стоит ровно в sub_cy. Это СОЗНАТЕЛЬНО отменяет правило "никогда
        # не на лице" в пользу неподвижности: от лиц теперь защищает отбор
        # врезок (talk_broll: полоса подписи, запрет фрагментов тела).
        "cy_lock": False,
        # ТОЛЬКО ОДНА СТРОКА (владелец 02.09.2026): движок сам уходил в
        # две строки, чтобы поставить кегль крупнее. Запрещаем.
        "single_line": False,
        # СНЯТЬ ЗНАКИ ПРЕПИНАНИЯ в субтитре (владелец: «запятые точки не
        # применяй»). Тайминги и слова не трогаются — только вид.
        "strip_punct": False,
        # БЕЛОЕ СВЕЧЕНИЕ вокруг букв: радиус размытия в пикселях (0 = нет).
        # Мягкое осветление фона под текстом — читаемость без плашки.
        "glow": 0,
        "glow_alpha": 150,
    },
    "accent": "lime",                          # render_reels --acc default (:409)
    # E4-57/U9: РЕЗЕРВНЫЕ АКЦЕНТЫ ТОГО ЖЕ БРЕНДА для авто-подбора по WCAG
    # (engine_talk.pick_accent). Пусто (дефолт) = резерва нет → движок ВСЕГДА
    # оставляет исходный accent, что бы ни намерил на фоне — байт-в-байт со
    # старым поведением. Пресет заполняет списком имён/[R,G,B] (та же схема,
    # что у accent) — оттенки/соседи ТОГО ЖЕ бренда, а не любой цвет с рынка.
    "accent_family": [],
    # позиция центра строки субтитра по шаблонам (engine_talk :1342-1357).
    # fmt_A — формат A (PIP), ключ по fmt, не по template. default — B/cutout.
    "sub_cy_overrides": {
        "split": 905,
        "lower-third": 1090,
        "fmt_A": 560,
        "text-behind": 1200,
        "demo": 1200,
        "compare": 1400,
        "default": 1200,
    },
    "karaoke": {
        "enabled": False,                      # флаг --karaoke (не дефолт)
        "style": "accent",                     # --karaoke-style default (:588)
        "max_words": 4,                        # chunk_cues_karaoke (:181)
        "max_chars": 26,                       # R8: было 22, движок живёт на 26
        # E4-49: РАСХОЖДЕНИЕ 245/255 СВЕДЕНО. Белых субтитра было два, и выбирал
        # их путь отрисовки: караоке — 255, split — хардкод 255, прочие
        # раскладки без караоке — 245. Караоке включено по умолчанию у всех
        # шести говорящих раскладок, поэтому фактическим белым завода был 255;
        # на нём и свели (engine_talk.WHITE). Поле оставлено отдельным: пресет
        # вправе красить неактивные слова иначе, чем обычный субтитр.
        "fill": [255, 255, 255],               # цвет НЕактивного слова
        "scale_pop": 1.08,                     # увеличение активного слова (:418)
        "plate_pad": 12,                       # плашка за словом (:440-441)
        "plate_radius": 14,
    },
    "pace": {                                  # talk_plan.py :44-47
        "min_scene": 4.0,
        "max_scene": 8.0,
        "broll_share": 0.5,
        "max_series": 2,
        # пол числа перебивок на ролик (talk_plan.MIN_BROLL, правило П1).
        # 0 = не проверять — так ведут себя все прежние стили завода.
        "min_broll": 0,
        # потолок числа врезок (talk_broll.MAX_BROLL_INSERTS). 3 — прежнее
        # решение T7, оно и остаётся дефолтом завода.
        "max_broll": 3,
        # РАВНОМЕРНОСТЬ (владелец 02.09.2026): доля хронометража, дольше
        # которой ролик не должен идти БЕЗ врезки. 1.0 = правило выключено.
        # Ставится, потому что врезки сбивались в один конец ролика: все
        # четыре попадали во вторую половину, первые 28 c шли без них.
        "max_gap": 1.0,
    },
    "safe_zone": {                             # engine_talk :101-104
        "profile": "ig-current",
        "top": 170,
        "bottom": 430,
        "right": 150,
        "left": 0,
        "margin": 72,
    },
    "punch": {                                 # engine_talk :1156, render_reels :302
        "scale": 1.10,
        "ramp": 5,
        "hold": 1.8,
    },
}


class StyleError(ValueError):
    """Ошибка схемы/значений стиля — всегда с путём до поля и подсказкой."""


# ── валидация ────────────────────────────────────────────────────────────────
def _fail(path, msg):
    raise StyleError("стиль: поле '%s' — %s" % (path, msg))


def _need(d, path, key, types):
    if key not in d:
        _fail("%s.%s" % (path, key), "отсутствует")
    v = d[key]
    if not isinstance(v, types):
        _fail("%s.%s" % (path, key),
              "ожидался %s, получен %s (%r)"
              % ("/".join(t.__name__ for t in types), type(v).__name__, v))
    return v


def _num(d, path, key, lo, hi, integer=False):
    types = (int,) if integer else (int, float)
    v = _need(d, path, key, types)
    if isinstance(v, bool) or not (lo <= v <= hi):
        _fail("%s.%s" % (path, key), "нужно число в [%s..%s], получено %r"
              % (lo, hi, v))
    return int(v) if integer else float(v)


def _rgb(v, path):
    if isinstance(v, str):
        if v == "ink":
            return INK
        if v in ACCENTS:
            return ACCENTS[v]
        _fail(path, "неизвестное имя цвета %r (знаю: ink, %s) или [R,G,B]"
              % (v, ", ".join(sorted(ACCENTS))))
    if (isinstance(v, (list, tuple)) and len(v) == 3
            and all(isinstance(c, int) and 0 <= c <= 255 for c in v)):
        return tuple(v)
    _fail(path, "нужен [R,G,B] 0..255 или имя цвета, получено %r" % (v,))


def _check_keys(d, path, allowed):
    # ключи с ведущим «_» — комментарии автора пресета (_note, _sources…),
    # схемой не проверяются и в Style не попадают
    extra = set(k for k in d if not str(k).startswith("_")) - set(allowed)
    if extra:
        _fail(path, "неизвестные ключи: %s (опечатка? допустимы: %s)"
              % (", ".join(sorted(extra)), ", ".join(sorted(allowed))))


# словари со свободными ключами (имена осей/весов/файлов) — заменяются целиком,
# без сверки ключей с дефолтом
_FREEFORM = {"style.font_family.axes", "style.font_family.static",
             "style.font_family.weights"}


def _merge(base, over, path="style"):
    """Слить пресет поверх дефолта: словари — рекурсивно, остальное — заменой.
    Ключи, которых нет в дефолте, — ошибка (защита от опечаток); ключи с
    ведущим «_» — комментарии автора пресета, отбрасываются."""
    out = copy.deepcopy(base)
    _check_keys(over, path, base.keys())
    for k, v in over.items():
        if str(k).startswith("_"):
            continue
        sub = "%s.%s" % (path, k)
        if isinstance(base[k], dict) and isinstance(v, dict) and sub not in _FREEFORM:
            out[k] = _merge(base[k], v, sub)
        else:
            out[k] = copy.deepcopy(v)
    return out


# ── Style ────────────────────────────────────────────────────────────────────
class Style:
    """Валидированный стиль клиента. Поля — как в _DEFAULT, плюс производные:
    accent_rgb, sub_fill, sub_stroke_fill, box_fill (кортежи RGB)."""

    def __init__(self, data):
        d = data
        self.raw = copy.deepcopy(d)
        self.name = _need(d, "style", "name", (str,))

        # шрифт
        ff = _need(d, "style", "font_family", (dict,))
        _check_keys(ff, "font_family", _DEFAULT["font_family"].keys())
        self.font_name = _need(ff, "font_family", "name", (str,))
        self.font_variable = ff.get("variable") or None
        self.font_static = dict(_need(ff, "font_family", "static", (dict,)))
        self.font_weights = dict(_need(ff, "font_family", "weights", (dict,)))
        self.font_axes = dict(ff.get("axes") or {})
        for ax, val in self.font_axes.items():
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                _fail("font_family.axes.%s" % ax, "нужно число, получено %r" % (val,))
        self.font_license = _need(ff, "font_family", "license", (str,))
        for w in FONT_WEIGHT_NAMES:
            if w not in self.font_weights:
                _fail("font_family.weights", "нет веса %r (движок им пользуется; "
                      "нужны все: %s)" % (w, ", ".join(FONT_WEIGHT_NAMES)))
        for w, val in self.font_weights.items():
            if not (isinstance(val, int) and 1 <= val <= 1000):
                _fail("font_family.weights.%s" % w, "вес оси 1..1000, получено %r" % (val,))
        if not self.font_variable and not self.font_static:
            _fail("font_family", "нужен либо 'variable' (один вариативный ttf), "
                  "либо 'static' {вес: файл}")
        for p, where in ([(self.font_variable, "font_family.variable")]
                         if self.font_variable else []) + \
                        [(v, "font_family.static.%s" % k)
                         for k, v in self.font_static.items()] + \
                        [(self.font_license, "font_family.license")]:
            if not os.path.exists(os.path.join(ROOT, p)):
                _fail(where, "файла нет: %s (шрифты вендорятся в репо, "
                      "скачивание в рантайме запрещено)" % os.path.join(ROOT, p))

        # кегль и нарезка
        self.speaker_face_y = _num(d, "style", "speaker_face_y", 0.0, 0.9)
        self.speaker_wide = _num(d, "style", "speaker_wide", 0.5, 1.0)
        self.sub_size_base = _num(d, "style", "sub_size_base", 24, 300, integer=True)
        self.sub_size_split = _num(d, "style", "sub_size_split", 24, 300, integer=True)
        self.sub_size_floor = _num(d, "style", "sub_size_floor", 16, 300, integer=True)
        for _fld, _val in (("sub_size_base", self.sub_size_base),
                           ("sub_size_split", self.sub_size_split)):
            if self.sub_size_floor > _val:
                _fail("sub_size_floor", "пол автокегля (%d) больше %s (%d)"
                      % (self.sub_size_floor, _fld, _val))
        ch = _need(d, "style", "sub_chunk", (dict,))
        _check_keys(ch, "sub_chunk", ("max_words", "max_chars"))
        self.sub_max_words = _num(ch, "sub_chunk", "max_words", 1, 8, integer=True)
        self.sub_max_chars = _num(ch, "sub_chunk", "max_chars", 4, 60, integer=True)

        # стиль начертания субтитра
        ss = _need(d, "style", "sub_style", (dict,))
        _check_keys(ss, "sub_style", _DEFAULT["sub_style"].keys())
        self.sub_kind = _need(ss, "sub_style", "kind", (str,))
        if self.sub_kind not in SUB_KINDS:
            _fail("sub_style.kind", "%r; допустимо: %s" % (self.sub_kind, "|".join(SUB_KINDS)))
        self.sub_fill = _rgb(ss["fill"], "sub_style.fill")
        self.sub_stroke_w = _num(ss, "sub_style", "stroke_w", 0, 40, integer=True)
        self.sub_stroke_fill = _rgb(ss["stroke_fill"], "sub_style.stroke_fill")
        self.sub_pad = _num(ss, "sub_style", "pad", 0, 120, integer=True)
        self.sub_shadow = bool(_need(ss, "sub_style", "shadow", (bool,)))
        self.sub_shadow_dy = _num(ss, "sub_style", "shadow_dy", 0, 30, integer=True)
        self.sub_shadow_alpha = _num(ss, "sub_style", "shadow_alpha", 0, 255, integer=True)
        self.sub_line_gap = _num(ss, "sub_style", "line_gap", 0.8, 2.5)
        self.box_fill = _rgb(ss["box_fill"], "sub_style.box_fill")
        self.box_alpha = _num(ss, "sub_style", "box_alpha", 0, 255, integer=True)
        self.box_pad_x = _num(ss, "sub_style", "box_pad_x", 0, 120, integer=True)
        self.box_pad_y = _num(ss, "sub_style", "box_pad_y", 0, 120, integer=True)
        self.box_radius = _num(ss, "sub_style", "box_radius", 0, 80, integer=True)
        self.split_uses_clean = bool(_need(ss, "sub_style", "split_uses_clean", (bool,)))
        # регистр/выравнивание/форс-позиция субтитра — новые ОПциональные оси.
        # Дефолты keep/center/False дают прежнее поведение движка байт-в-байт.
        self.sub_case = _need(ss, "sub_style", "case", (str,))
        if self.sub_case not in SUB_CASES:
            _fail("sub_style.case", "%r; допустимо: %s"
                  % (self.sub_case, "|".join(SUB_CASES)))
        self.sub_align = _need(ss, "sub_style", "align", (str,))
        if self.sub_align not in SUB_ALIGNS:
            _fail("sub_style.align", "%r; допустимо: %s"
                  % (self.sub_align, "|".join(SUB_ALIGNS)))
        self.sub_cy_force = bool(_need(ss, "sub_style", "cy_force", (bool,)))
        self.sub_cy_lock = bool(_need(ss, "sub_style", "cy_lock", (bool,)))
        self.sub_single_line = bool(_need(ss, "sub_style", "single_line", (bool,)))
        self.sub_strip_punct = bool(_need(ss, "sub_style", "strip_punct", (bool,)))
        self.sub_glow = _num(ss, "sub_style", "glow", 0, 40, integer=True)
        self.sub_glow_alpha = _num(ss, "sub_style", "glow_alpha", 0, 255,
                                   integer=True)

        # акцент: имя из палитры завода или явный RGB
        acc = d.get("accent", "lime")
        self.accent = acc if isinstance(acc, str) else list(acc)
        self.accent_rgb = _rgb(acc, "accent")

        # E4-57/U9: резервные акценты того же бренда (см. комментарий в _DEFAULT).
        # Каждый элемент — та же схема, что accent (имя из ACCENTS или [R,G,B]).
        fam = d.get("accent_family", [])
        if not isinstance(fam, list):
            _fail("accent_family", "нужен список имён/[R,G,B], получено %r" % (fam,))
        self.accent_family = list(fam)
        self.accent_family_rgb = [_rgb(v, "accent_family[%d]" % i)
                                   for i, v in enumerate(fam)]

        # позиции субтитра
        cy = _need(d, "style", "sub_cy_overrides", (dict,))
        _check_keys(cy, "sub_cy_overrides", _DEFAULT["sub_cy_overrides"].keys())
        self.sub_cy = {}
        for k in _DEFAULT["sub_cy_overrides"]:
            self.sub_cy[k] = _num(cy, "sub_cy_overrides", k, 0, VH, integer=True)

        # караоке
        ka = _need(d, "style", "karaoke", (dict,))
        _check_keys(ka, "karaoke", _DEFAULT["karaoke"].keys())
        self.karaoke_enabled = bool(_need(ka, "karaoke", "enabled", (bool,)))
        self.karaoke_style = _need(ka, "karaoke", "style", (str,))
        if self.karaoke_style not in KARAOKE_STYLES:
            _fail("karaoke.style", "%r; допустимо: %s"
                  % (self.karaoke_style, "|".join(KARAOKE_STYLES)))
        self.karaoke_max_words = _num(ka, "karaoke", "max_words", 1, 10, integer=True)
        self.karaoke_max_chars = _num(ka, "karaoke", "max_chars", 4, 80, integer=True)
        self.karaoke_fill = _rgb(ka["fill"], "karaoke.fill")
        self.karaoke_scale_pop = _num(ka, "karaoke", "scale_pop", 1.0, 1.5)
        self.karaoke_plate_pad = _num(ka, "karaoke", "plate_pad", 0, 60, integer=True)
        self.karaoke_plate_radius = _num(ka, "karaoke", "plate_radius", 0, 60, integer=True)

        # темп монтажа
        pa = _need(d, "style", "pace", (dict,))
        _check_keys(pa, "pace", _DEFAULT["pace"].keys())
        self.pace_min_scene = _num(pa, "pace", "min_scene", 0.5, 60)
        self.pace_max_scene = _num(pa, "pace", "max_scene", 0.5, 120)
        if self.pace_min_scene > self.pace_max_scene:
            _fail("pace", "min_scene (%s) больше max_scene (%s)"
                  % (self.pace_min_scene, self.pace_max_scene))
        self.pace_broll_share = _num(pa, "pace", "broll_share", 0.0, 1.0)
        self.pace_max_series = _num(pa, "pace", "max_series", 1, 10, integer=True)
        self.pace_min_broll = _num(pa, "pace", "min_broll", 0, 40, integer=True)
        # 0 — ОСМЫСЛЕННОЕ значение: формат без перебивок вовсе («СУБТИТРЫ
        # СТАНДАРТ», владелец 04.09.2026). На нём render_reels даже не
        # опрашивает сток. Раньше нижней границей стояла 1, и такой
        # формат нельзя было описать пресетом вообще.
        self.pace_max_broll = _num(pa, "pace", "max_broll", 0, 40, integer=True)
        self.pace_max_gap = _num(pa, "pace", "max_gap", 0.05, 1.0)
        if self.pace_min_broll > self.pace_max_broll:
            _fail("pace", "min_broll (%s) больше max_broll (%s)"
                  % (self.pace_min_broll, self.pace_max_broll))

        # безопасная зона: dict с числами ИЛИ строка-профиль из
        # styles/platform_zones.json ("ig"|"tiktok"|"shorts"|"conservative")
        sz = d.get("safe_zone")
        if isinstance(sz, str):
            sz = _platform_zone(sz)
        if not isinstance(sz, dict):
            _fail("safe_zone", "нужен dict {top,bottom,right,left,margin} или "
                  "имя профиля из styles/platform_zones.json")
        _check_keys(sz, "safe_zone", ("profile", "top", "bottom", "right",
                                      "left", "margin", "note"))
        self.safe_profile = str(sz.get("profile", "custom"))
        self.safe_top = _num(sz, "safe_zone", "top", 0, VH // 2, integer=True)
        self.safe_bottom = _num(sz, "safe_zone", "bottom", 0, VH // 2, integer=True)
        self.safe_right = _num(sz, "safe_zone", "right", 0, VW // 2, integer=True)
        self.safe_left = _num(sz, "safe_zone", "left", 0, VW // 2, integer=True)
        self.safe_margin = _num(sz, "safe_zone", "margin", 0, 200, integer=True)
        if self.safe_top + self.safe_bottom >= VH - 200:
            _fail("safe_zone", "top+bottom (%d) съедают почти весь кадр %d"
                  % (self.safe_top + self.safe_bottom, VH))

        # зум-удар
        pu = _need(d, "style", "punch", (dict,))
        _check_keys(pu, "punch", _DEFAULT["punch"].keys())
        self.punch_scale = _num(pu, "punch", "scale", 1.0, 1.6)
        self.punch_ramp = _num(pu, "punch", "ramp", 0, 60, integer=True)
        self.punch_hold = _num(pu, "punch", "hold", 0.2, 10.0)

    # ── кегль ──
    def sub_size_for(self, split_or_wrap):
        """Базовый кегль субтитра до автоподбора. R7: split и ×N-путь живут на
        своём кегле (74), остальные раскладки — на sub_size_base (100)."""
        return self.sub_size_split if split_or_wrap else self.sub_size_base

    # ── шрифты ──
    def make_font(self, weight, size):
        """PIL-шрифт гарнитуры стиля. weight — имя движка (Regular|SemiBold|
        Bold|Black), size — кегль. Вариативный файл: выставляем ось wght по
        карте weights (с зажимом в границы осей файла — у Manrope нет 900,
        Black честно падает в её максимум 800). Статический: свой файл на вес."""
        if weight not in self.font_weights:
            _fail("font_family.weights", "движок запросил вес %r" % weight)
        if self.font_static:
            path = self.font_static.get(weight)
            if not path:
                _fail("font_family.static", "нет файла для веса %r" % weight)
            return ImageFont.truetype(os.path.join(ROOT, path), size)
        ft = ImageFont.truetype(os.path.join(ROOT, self.font_variable), size)
        want = self.font_weights[weight]
        values = []
        for ax in ft.get_variation_axes():
            name = ax["name"]
            name = name.decode() if isinstance(name, bytes) else str(name)
            key = name.strip().lower()
            if key in ("weight", "wght"):
                values.append(max(ax["minimum"], min(ax["maximum"], want)))
                continue
            over = next((v for k, v in self.font_axes.items()
                         if k.strip().lower() == key), None)
            if over is not None:
                values.append(max(ax["minimum"], min(ax["maximum"], over)))
            else:
                values.append(ax["default"])
        if values:
            ft.set_variation_by_axes(values)
        return ft

    # ── сравнение/отладка ──
    def __eq__(self, other):
        return isinstance(other, Style) and self.raw == other.raw

    def __repr__(self):
        return "<Style %r: font=%s sub=%d/%s acc=%s zone=%s>" % (
            self.name, self.font_name, self.sub_size_base, self.sub_kind,
            self.accent, self.safe_profile)


def _platform_zone(name):
    """Профиль зоны по имени из styles/platform_zones.json (R33)."""
    path = os.path.join(ROOT, "styles", "platform_zones.json")
    if not os.path.exists(path):
        raise StyleError("safe_zone=%r: нет styles/platform_zones.json" % name)
    with open(path, encoding="utf-8") as f:
        zones = json.load(f).get("profiles", {})
    if name not in zones:
        raise StyleError("safe_zone=%r: нет такого профиля; есть: %s"
                         % (name, ", ".join(sorted(zones))))
    z = dict(zones[name])
    z.setdefault("profile", name)
    return z


def load(path=None):
    """Загрузить стиль. None → дефолт (= текущие константы движка байт-в-байт).
    path → json-пресет; частичный: заданные поля ложатся поверх дефолта."""
    if path is None:
        return Style(copy.deepcopy(_DEFAULT))
    if not os.path.exists(path):
        alt = os.path.join(ROOT, path)
        if os.path.exists(alt):
            path = alt
        else:
            raise StyleError("файл стиля не найден: %s" % path)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as ex:
        raise StyleError("битый json в %s: строка %d, колонка %d: %s"
                         % (path, ex.lineno, ex.colno, ex.msg))
    if not isinstance(data, dict):
        raise StyleError("%s: в корне должен быть объект {...}" % path)
    return Style(_merge(_DEFAULT, data))


def main():
    import sys
    st = load(sys.argv[1] if len(sys.argv) > 1 else None)
    print(st)
    print("default == styles/default.json:",
          load(None) == load(os.path.join(ROOT, "styles", "default.json")))


if __name__ == "__main__":
    raise SystemExit(main())
