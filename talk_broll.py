# -*- coding: utf-8 -*-
"""Стадии 4–5 движка №4: подбор b-roll под смысловые блоки со СТОКОВ ДЛЯ ВСЕХ.

Вход — blocks.json (смысловые блоки речи с визуальным запросом на каждый,
запрос формулирует Claude на стадии talk_segment — путь «Claude в цикле»).
Выход — список inserts для engine_talk.render(template="split"): на каждый блок
скачанный ролик со стока, привязанный по времени.

ИСТОЧНИК ПЕРЕКЛЮЧАЕТСЯ (как в stock.py): единый контракт BrollSource.search(
query, dur) -> [candidate]. Реализации:
  • PixabaySource — свободные видео Pixabay (ключ PIXABAY_API_KEY в .env, эндпоинт
    /api/videos/ подтверждён рабочим). Коммерческое использование без атрибуции.
  • PexelsSource — запасной (ключ PEXELS_API_KEY; голый Authorization, без Bearer).
  • LocalBankSource — свой банк video/ (приоритет), подключим когда Drive стабилен.

Стоки берутся из интернета, поэтому подбор НЕ зависит от Google Drive (на машине
Anton он выселяет медиа из junction-папок — см. память проекта).

Ключи и сеть — переиспользуем stock.py, чтобы не заводить второй загрузчик,
который завтра разъедется с первым.
"""
import os
import re
import urllib.parse
import urllib.request

import cv2
import numpy as np

import stock  # load_env, key_for, _get (кэш), UA — единый слой ключей и сети

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache_broll")   # скачанные ролики (в .gitignore)
UA = stock.UA

# ── CLIP-ПЕРЕРАНЖИРОВКА (P0 из docs/research/reels-quality-proposal.md) ───────
# Мастер-тумблер. True → стоковые кандидаты пересортировываются по семантической
# близости мидфрейма к visual_query (embed_text/embed_image из talk_broll_index),
# а не по «популярности» Pixabay. Если open_clip/torch недоступны или веса не
# грузятся — мягкий фолбэк на прежний порядок (см. _clip_mod). Ведущий может
# выключить одной строкой (RERANK_DEFAULT = False), не трогая вызовы.
RERANK_DEFAULT = True
# Порог отсева (косинус текст↔кадр, ViT-B-32 laion2b). Ниже — перебивку НЕ ставим,
# сцена остаётся говорящей головой (движок это умеет: блок без insert = спикер во
# весь кадр). Ориентир из разбора 0.22–0.25; держим параметром для калибровки.
CLIP_THRESHOLD = 0.22
# Сколько верхних (по популярности) кандидатов вообще скорить CLIP-ом. Ограничивает
# трафик: скорим по дешёвой thumbnail, целиком качаем только победителя (в
# broll_for_blocks). Разбор советует 12–15.
RERANK_TOPN = 12

# T7 (владелец, показ split/card/pip Валерии): не больше MAX_BROLL_INSERTS
# видео-вставок (b-roll, кадры без спикера) за один ролик — независимо от
# длины ролика/числа реплик/того, сколько блоков планировщик или ручной набор
# блоков реально хочет проиллюстрировать. Лишние сегменты не режутся на
# картинку — там остаётся спикер (см. _cap_inserts).
MAX_BROLL_INSERTS = 3
# 28.08.2026. Владелец пересмотрел T7: «перебивок не менее 4 на ролик».
# Прежний потолок 3 прямо этому противоречил. Число здесь остаётся 3 —
# это дефолт для ВСЕХ прежних форматов завода, их поведение не меняется;
# кодовые форматы СУБТИТРЫ и ПОЛОВИНКИ поднимают его своим пресетом
# (pace.max_broll в styles/*.json). Референс владельца держит ~10 врезок
# на 36 c — см. docs/ПРАВИЛА_ПЕРЕБИВОК.md, правило П1.


# ── ИСТОЧНИКИ (единый контракт search(query, dur) -> [candidate]) ─────────────
# Слова-маркеры графики/иконок/брендов и КРУПНЫХ ПЛАНОВ — такие ролики не годятся
# (рисованные иконки, анимация, логотипы соцсетей, макро/крупный план без
# контекста). Финальный отсев крупных планов — глазами по превью (Claude в цикле).
# ВНИМАНИЕ: этот список работает в ДЕФОЛТНОМ пути подбора (через _is_graphic в
# PixabaySource.search) — то есть во ВСЕХ старых форматах. Поэтому он обязан
# оставаться БАЙТ-В-БАЙТ как в main. Маркеры крупного плана ЧЕЛОВЕКА
# (portrait/selfie/headshot/face) сюда НЕ входят: они нужны только новому пути
# wide=True и вынесены в _BAN_FACE ниже — иначе старый split-Reels выбрал бы
# ДРУГОЙ b-roll и потерял бы байт-в-байт совместимость.
_BAN = ("logo", "icon", "animation", "animated", "cartoon", "3d", "render",
        "motion graphic", "typography", "abstract loop", "facebook", "instagram",
        "youtube", "tiktok", "twitter", "whatsapp",
        "close-up", "closeup", "close up", "macro", "extreme close")

# Маркеры КРУПНОГО ПЛАНА ЧЕЛОВЕКА — применяются ТОЛЬКО в пути wide=True (требование
# 1: за силуэтом спикера только дальние планы, фон не должен спорить лицом). Это
# ОСНОВЫ слов: матч идёт по ПРЕФИКСУ ТОКЕНА тега (см. _is_face_tag), чтобы ловить
# словоформы и множественное число (теги Pixabay почти всегда во мн.ч.:
# faces/portraits/selfies/headshots) и производные (facial). Префикс берём от
# НАЧАЛА токена, а НЕ подстрокой — поэтому 'surface'/'interface'/'preface' не
# ловятся (они не начинаются с основы). 'facial' — отдельная основа: 'facial' не
# начинается с 'face'.
_BAN_FACE = ("portrait", "selfie", "headshot", "face", "facial")
_WORD_RE = re.compile(r"[a-z0-9]+")

# ДАЛЬНИЙ ПЛАН (требование 1): термины, которыми расширяем поисковый запрос, чтобы
# сток отдавал общие/дальние/панорамные композиции, а не средние и крупные.
_WIDE_TERMS = "wide shot establishing aerial distant landscape"

# Порог «крупного субъекта» по площади лица в кадре перебивки (доля площади):
# фон с лицом крупнее этого — это средний/крупный план человека, за силуэтом
# спикера он мешает. Дальний план с людьми даёт лица кратно меньше.
_CLOSE_FACE_FRAC = 0.06


#: ЗАПРЕЩЁННОЕ СОДЕРЖИМОЕ ВРЕЗОК (решение владельца 28.08.2026). В кадр не
#: попадают алкоголь, секс, курение, религиозные и эзотерические мотивы. Список
#: по ТЕГАМ стока — на Pixabay/Pexels они английские, поэтому основы английские;
#: русские добавлены на случай локальных источников. Матч по ПРЕФИКСУ токена
#: (как в _is_face_tag), чтобы ловить словоформы: wine/wines, smoking/smoker.
_BAN_CONTENT = (
    # алкоголь
    "alcohol", "wine", "beer", "whisk", "vodka", "cocktail", "champagne",
    "drunk", "bar", "pub", "liquor", "brandy", "rum", "tequila", "toast",
    "алкогол", "вино", "пиво", "коктейл", "шампанск",
    # секс и обнажённость
    "sex", "erotic", "nude", "naked", "lingerie", "bikini", "seduc",
    "sensual", "intimate", "strip", "секс", "эротик", "обнаж",
    # курение
    "smok", "cigar", "tobacco", "vape", "nicotine", "hookah", "shisha",
    "курен", "сигарет", "кальян",
    # религия
    "church", "temple", "mosque", "synagog", "pray", "prayer", "cross",
    "crucifix", "bible", "quran", "icon", "religio", "monk", "priest",
    "buddha", "hindu", "chapel", "cathedral", "церк", "храм", "молитв",
    "икон", "религи",
    # эзотерика
    "tarot", "astrolog", "horoscope", "zodiac", "occult", "esoteric",
    "witch", "magic", "ritual", "crystal ball", "rune", "chakra",
    "meditat", "spiritual", "таро", "астролог", "гороскоп", "эзотер",
    "магия", "ритуал", "чакр",
)

#: ОБРЕЗАННЫЕ ЧАСТИ ТЕЛА (то же решение): людей показываем целиком, с головой.
#: Теги крупных планов и фрагментов — верный признак, что головы в кадре нет.
_BAN_CROPPED = (
    # ТОЛЬКО ОДНОЗНАЧНЫЕ маркеры кадра-фрагмента. 02.09.2026 здесь стояли ещё
    # hand/hands/leg/foot/torso — и фильтр выкосил пул: слово «hand» есть в
    # тегах у огромной доли нормальных клипов, где человек снят ЦЕЛИКОМ.
    # Результат: на первые 29 секунд ролика не нашлось ни одной врезки.
    # Обрезанность тела по тегам вообще определяется плохо; надёжнее ловить её
    # разрешением и планом (wide) и глазами по контактному листу.
    "closeup", "close-up", "macro", "extreme close",
    "крупный план",
)


def _is_banned_content(tags):
    """True — врезка с запрещённым содержимым (алкоголь/секс/курение/религия/
    эзотерика). Матч по префиксу токена тега, как в _is_face_tag."""
    t = (tags or "").lower()
    toks = _WORD_RE.findall(t)
    if any(tok.startswith(st) for tok in toks for st in _BAN_CONTENT
           if " " not in st):
        return True
    return any(st in t for st in _BAN_CONTENT if " " in st)


def _is_cropped_person(tags):
    """True — на врезке человек показан фрагментом (руки, ноги, крупный план).
    Владелец: «всех людей показывать только полностью с головой»."""
    t = (tags or "").lower()
    toks = _WORD_RE.findall(t)
    if any(tok.startswith(st) for tok in toks for st in _BAN_CROPPED
           if " " not in st):
        return True
    return any(st in t for st in _BAN_CROPPED if " " in st)


def _is_graphic(tags):
    t = (tags or "").lower()
    return any(w in t for w in _BAN)


def _is_face_tag(tags):
    """True, если теги кандидата содержат маркер крупного плана человека
    (_BAN_FACE) в любой словоформе. ТОЛЬКО для пути wide=True. Матч по ПРЕФИКСУ
    ТОКЕНА тега (граница слова + словоформы): ловит и мн.ч. (faces/portraits/
    selfies/headshots), и производные (facial), но 'face' НЕ цепляет
    'surface'/'interface'/'preface' — те не начинаются с основы. Дефолтный путь
    этих терминов не видит вовсе (здесь не вызывается)."""
    toks = _WORD_RE.findall((tags or "").lower())
    return any(tok.startswith(stem) for tok in toks for stem in _BAN_FACE)


def _widen(q):
    """Дописать к запросу дальние/общие термины (требование 1: только дальние
    планы стока). Пустой запрос не трогаем."""
    q = (q or "").strip()
    return (q + " " + _WIDE_TERMS).strip() if q else q


# Ленивый детектор лица для отсева крупных планов человека на фоне-перебивке.
_FACEDET = None


def _facedet():
    global _FACEDET
    if _FACEDET is None:
        import mediapipe as mp
        _FACEDET = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5)
    return _FACEDET


def _max_face_frac(path):
    """Доля площади самого крупного лица в кадре превью (0..1). 0 — лиц нет/не
    прочли. Используется для отсева крупных/средних планов человека (требование 1:
    фон — только дальний план)."""
    if not path:
        return 0.0
    img = cv2.imread(path)
    if img is None:
        return 0.0
    try:
        res = _facedet().process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    except Exception:
        return 0.0
    best = 0.0
    for d in (res.detections or []):
        b = d.location_data.relative_bounding_box
        best = max(best, float(b.width) * float(b.height))
    return best


def _is_close_subject(cand):
    """True, если превью кандидата — крупный/средний план человека (лицо крупнее
    порога). Смотрим по дешёвому thumbnail; нет thumb — не режем (False)."""
    p = _thumb_path(cand)
    if not p:
        return False
    return _max_face_frac(p) > _CLOSE_FACE_FRAC


# ── ОТСЕВ НИЗКОГО РАЗРЕШЕНИЯ ПЕРЕБИВОК (серия «качество монтажа», задача 1) ─────
# Перебивку движок (engine_talk.prepare_broll) кадрирует под ОБЛАСТЬ раскладки
# методом cover-fit: если исходник ниже области — он РАСТЯГИВАЕТСЯ вверх, и в
# кадре получается мыло. Такие клипы отсекаем ДО выбора, порог НЕ опускаем.
#
# ЗАКОН РАЗРЕШЕНИЯ (решение ведущего, подтверждено эмпирикой): область = 1920 px
# для ВСЕХ раскладок, запас = 1.0 (жёсткий инвариант «апскейл ЗАПРЕЩЁН»).
#   Почему 1920 и для split. Замерено: engine_talk.prepare_broll нормализует b-roll
#   ЛЮБОЙ раскладки в ПОЛНЫЙ VW×VH=1080×1920 ещё ДО компоновки. Для split нижняя
#   панель — это _cover_face(этот 1080×1920 → 1080×960): чистый КРОП без доп.
#   масштаба. Значит источник рендерится в масштабе 1920, НЕ 960 (проверка: исходник
#   886×1422 → prepare_broll выдаёт 1080×1920, апскейл ×1.35). Порог 960 недозащищал
#   бы split; порог с запасом 1.3 (→2496 px) при этом гнал cutout в ~75% fallback,
#   хотя 4K-2160 при 1920 даёт даунскейл 0.89 (мыла нет). Убираем инфляцию 1.3:
#   ровно «высота ≥ 1920» = «апскейл ≤ 1.0», ни пикселя вверх.
#   pip/card (R10): b-roll в обоих тоже идёт полноэкранно через prepare_broll
#   (VW×VH=1080×1920 ДО компоновки, engine_talk:1217-1219 лишь кладёт спикера
#   поверх) — модель разрешения та же, порог тот же 1920.
_AREA_H = {"split": 1920, "cutout": 1920, "text-behind": 1920,
           "pip": 1920, "card": 1920, "lower-third": 1920}
_AREA_H_DEFAULT = 1920

# Запас над областью: 1.0 — жёсткий инвариант. Клип берём, только если его высота
# ≥ 1.0× области (≥1920): 2160→даунскейл 0.89 проходит, <1920 = апскейл = режем.
RES_MARGIN = 1.0


def area_height(template):
    """Высота области раскладки (px), куда ложится перебивка. Незнакомый/None шаблон
    — консервативно полный кадр (самый строгий разумный порог)."""
    return _AREA_H.get(template, _AREA_H_DEFAULT)


def upscale_factor(cand_h, area_h):
    """Во сколько раз cover-fit растянет исходник по ВЫСОТЕ под область (>1 = апскейл
    = мыло). Неизвестная/нулевая высота → None (вслепую не судим)."""
    h = int(cand_h or 0)
    if h <= 0:
        return None
    return area_h / float(h)


def _res_ok(cand, area_h, margin=RES_MARGIN):
    """True, если высота клипа покрывает область с запасом margin (движок не тянет
    исходник вверх). Неизвестная высота (0) → True: не выкидываем вслепую — финальный
    барьер по факту использованного клипа стоит в checks.verify_talk."""
    h = int(cand.get("h") or 0)
    if h <= 0:
        return True
    return h >= margin * area_h


# Разрешение ВЫБРАННЫХ клипов за прогон (cand_id -> (w,h)). Нужен checks.verify_talk:
# render_reels кладёт в meta['broll'] только id клипа (cand) без размеров, а движок
# трогать нельзя. Заполняется в broll_for_blocks при финальном выборе; verify_talk
# по cand достаёт сюда размер и считает апскейл под область раскладки.
USED_CLIP_RES = {}


def clip_res(cand_id):
    """(w,h) выбранного клипа по его cand-id или None (не проходил через подбор)."""
    return USED_CLIP_RES.get(cand_id)


# Счётчики отсева/фолбэка за ОДИН прогон broll_for_blocks — для замера «до/после»
# (замер, не вывод). reset_stats() зовётся в начале broll_for_blocks, stats() — после.
_STATS = {"cand_seen": 0, "cand_dropped_res": 0,
          "blocks": 0, "blocks_fallback": 0, "used_scales": [],
          "cand_dropped_face": 0}


def reset_stats():
    _STATS.update(cand_seen=0, cand_dropped_res=0, blocks=0,
                  blocks_fallback=0, used_scales=[], cand_dropped_face=0)


def stats():
    """Свод замера последнего прогона: доля отброшенных фильтром кандидатов, доля
    блоков, ушедших в fallback, средний коэффициент масштаба использованных клипов."""
    us = _STATS["used_scales"]
    return {
        "cand_seen": _STATS["cand_seen"],
        "cand_dropped_res": _STATS["cand_dropped_res"],
        "dropped_frac": (round(_STATS["cand_dropped_res"] / _STATS["cand_seen"], 4)
                         if _STATS["cand_seen"] else 0.0),
        "blocks": _STATS["blocks"],
        "blocks_fallback": _STATS["blocks_fallback"],
        "fallback_frac": (round(_STATS["blocks_fallback"] / _STATS["blocks"], 4)
                          if _STATS["blocks"] else 0.0),
        "used_clips": len(us),
        "avg_scale": (round(sum(us) / len(us), 4) if us else None),
        "max_scale": (round(max(us), 4) if us else None),
        "cand_dropped_face": _STATS["cand_dropped_face"],
    }


def _gather(srcs, queries, dur, exclude):
    """Собрать уникальный валидный пул кандидатов по срокам/запросам (search →
    dedup по id → отсев нулевых размеров). Общий кирпич для основного пула и для
    fallback-пересбора (расширённый запрос / локальный банк) в _res_filter_pool."""
    cands = []
    for s in srcs:
        for q in queries:
            try:
                cands += s.search(q, dur)
            except SystemExit:
                raise
            except Exception as ex:
                print("  ! %s упал на «%s»: %s" % (getattr(s, "name", "?"), q, ex))
    seen, uniq = set(), []
    for c in cands:
        if c["id"] in seen or c["id"] in exclude:
            continue
        seen.add(c["id"])
        uniq.append(c)
    return [c for c in uniq if c["w"] > 0 and c["h"] > 0] or uniq


def _res_filter_pool(valid, queries, dur, srcs, area_h, wide, exclude, providers):
    """Отсечь клипы ниже порога разрешения и, если пул опустел, НЕ опуская порог,
    подняться по лестнице fallback (требование 3):
      1) расширить запрос дальними/общими терминами (чаще отдают 4K/дрон) — если
         запрос ещё не был расширен (не wide);
      2) крайний случай — локальный банк своих клипов;
      3) всё ещё пусто → блок без перебивки (движок оставит спикера на весь кадр —
         менее точная перебивка лучше мыла, а мыла быть не должно вовсе).
    Обновляет счётчики замера. Возвращает отфильтрованный пул (возможно пустой)."""
    _STATS["blocks"] += 1
    seen_n = len(valid)
    kept = [c for c in valid if _res_ok(c, area_h)]
    _STATS["cand_seen"] += seen_n
    _STATS["cand_dropped_res"] += seen_n - len(kept)
    if kept:
        return kept
    _STATS["blocks_fallback"] += 1
    if not wide:                                  # (1) расширяем запрос
        wq = [_widen(q) for q in queries if q]
        kept = [c for c in _gather(srcs, wq, dur, exclude) if _res_ok(c, area_h)]
    if not kept and "local" not in providers:     # (2) локальный банк
        try:
            kept = [c for c in _gather([SOURCES["local"]()], queries, dur, exclude)
                    if _res_ok(c, area_h)]
        except SystemExit:
            pass                                  # нет индекса банка — просто без него
        except Exception as ex:
            print("  ! локальный fallback упал: %s" % ex)
    return kept                                    # (3) может остаться пустым — ок


class PixabaySource:
    name = "pixabay"

    def search(self, query, dur, n=30):
        url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode({
            "key": stock.key_for("pixabay"),
            "q": query,
            "video_type": "film",          # только живая съёмка, без анимации/иконок
            "per_page": max(3, min(n, 200)),
            "safesearch": "true",
            "order": "popular",
        })
        out = []
        for h in stock._get(url).get("hits", []):
            v = h.get("videos", {})
            pick = v.get("large") or v.get("medium") or v.get("small") or v.get("tiny")
            if not pick or not pick.get("url"):
                continue
            _tg = h.get("tags", "")
            if _is_banned_content(_tg):          # запрет владельца 28.08.2026
                continue
            if _is_cropped_person(_tg):          # человек только целиком, с головой
                continue
            if _is_graphic(_tg):                 # отсеять иконки/логотипы/анимацию
                continue
            out.append({
                "source": "pixabay",
                "id": "pixabay_%s" % h["id"],
                "url": pick["url"],
                "thumb": pick.get("thumbnail", ""),
                "w": pick.get("width", 0),
                "h": pick.get("height", 0),
                "duration": h.get("duration", 0),
                "tags": h.get("tags", ""),
            })
        return out


class PexelsSource:
    name = "pexels"

    def search(self, query, dur, n=20):
        url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode({
            "query": query, "per_page": max(3, min(n, 80)), "orientation": "portrait",
        })
        # ГОЛЫЙ ключ в Authorization — с "Bearer " будет 401 (как в stock.py).
        data = stock._get(url, {"Authorization": stock.key_for("pexels")})
        out = []
        for p in data.get("videos", []):
            files = sorted(p.get("video_files", []),
                           key=lambda f: (f.get("height", 0)), reverse=True)
            if not files:
                continue
            f = files[0]
            out.append({
                "source": "pexels",
                "id": "pexels_%s" % p["id"],
                "url": f["link"],
                "thumb": p.get("image", ""),   # превью-кадр для дешёвого CLIP-скоринга
                "w": f.get("width", 0),
                "h": f.get("height", 0),
                "duration": p.get("duration", 0),
                "tags": " ".join(t for t in [p.get("url", "")] if t),
            })
        return out


def _local_source(*a, **k):
    """Ленивая фабрика локального банка (E4-10): torch/open_clip грузятся ТОЛЬКО
    когда реально зовут источник 'local', стоковый путь остаётся лёгким.
    Реализация — talk_broll_index.LocalBankSource."""
    import talk_broll_index
    return talk_broll_index.LocalBankSource(*a, **k)


# ── ГЕНЕРАЦИЯ КАК ИСТОЧНИК ПЕРЕБИВКИ (fal) ───────────────────────────────────
# Отдельный источник: он не ИЩЕТ готовый клип на стоке, а ЗАКАЗЫВАЕТ генерацию
# под конкретный visual_query. Устроен тонкой обёрткой над проверенным стендом
# bench_generate — тот уже умеет ходить в очередь fal, считать деньги сквозным
# потолком (Budget по журналу out_bench/journal.jsonl) и не платить дважды за
# один запрос (кэш .cache_bench). Здесь этот стек ПЕРЕИСПОЛЬЗУЕТСЯ, а не
# переписываются вызовы fal заново в коде, который едет клиентам.
#
# ДВА РЕЖИМА, по умолчанию БЕЗ ТРАТ:
#   • кэш-онли (по умолчанию): источник отдаёт только клипы, которые УЖЕ
#     сгенерированы и лежат в .cache_bench. Ни одного платного запроса. Рендер
#     с --providers fal на неоплаченном запросе просто не найдёт кандидата и
#     уйдёт в общий fallback — как будто источник пуст;
#   • с тратой (env KARUSEL_FAL_SPEND=<потолок $>): источник ДОГЕНЕРИРУЕТ
#     недостающие клипы через bench_generate.run_jobs с этим потолком; весь
#     учёт денег, кэш и проверка ключа — на стороне стенда.
#
# ПРЕДОХРАНИТЕЛЬ ТЕСТОВ. tests_guard чистит ОКРУЖЕНИЕ, но FAL_KEY стенд читает
# из .env мимо него (та же лазейка, что закрыта в bench_generate.main). Поэтому
# оборону держим здесь сами: под меткой песочницы с ЖИВОЙ сетью не тратим
# никогда, даже если KARUSEL_FAL_SPEND задан (образец bench_generate: гейт
# «под_тестами and сеть_живая» в main).

_FAL_SPEND_ENV = "KARUSEL_FAL_SPEND"     # потолок трат в $; пусто/0 → только кэш
_FAL_MODEL_ENV = "KARUSEL_FAL_MODEL"     # модель стенда; дефолт — самая дешёвая
_FAL_MODEL_DEFAULT = "ltx-video"         # $0.02/клип, цена за клип целиком


def _load_bench():
    """Стенд генерации bench_generate или None, если его нет рядом.

    bench_generate — ДЕВ-инструмент и в клиентскую коробку он НЕ едет (это
    стережёт build_box.box_import_gaps: коробочный модуль не смеет статически
    импортировать то, чего в коробке нет). Поэтому зависимость здесь честно
    ОПЦИОНАЛЬНАЯ: на машине разработки стенд есть и источник fal работает; в
    клиентской коробке его нет — importlib вернёт None, FalSource окажется пуст,
    и рендер с --providers fal просто уйдёт в общий fallback, как будто
    перебивок не нашлось. Через importlib намеренно: статический `import
    bench_generate` в этом (коробочном) модуле уронил бы аудит поставки, а
    инвариант коробки «не звать отсутствующего» тут соблюдён возвратом None."""
    import importlib
    try:
        return importlib.import_module("bench_generate")
    except Exception:
        return None


def _fal_spend_forbidden():
    """Запрещены ли ВООБЩЕ траты сейчас (True → работаем только из кэша).

    Логика та же, что в bench_generate.main: под предохранителем тестов с
    НЕподменённой сетью реальный платный запрос ушёл бы за деньги владельца —
    не тратим. Если сеть подменена растяжкой (тест платного пути), запрет
    снимается сам. Не под тестами — тратить можно (решает KARUSEL_FAL_SPEND)."""
    if os.environ.get("KARUSEL_TESTS_SANDBOX") != "1":
        return False
    bg = _load_bench()
    if bg is None:                         # стенда нет — платить всё равно нечем
        return True
    try:
        return bg._request is bg._НАСТОЯЩИЙ_ЗАПРОС
    except Exception:                      # не смогли проверить под тестами —
        return True                        # на всякий случай не тратим


class FalSource:
    """Генерация клипа-перебивки под запрос через fal (обёртка bench_generate).

    По умолчанию — только кэш .cache_bench, без трат. Траты включаются env
    KARUSEL_FAL_SPEND=<потолок $> и всё равно запрещены под тестами с живой
    сетью. Кандидат отдаётся в форме локального банка (готовый файл + path),
    поэтому дальше по конвейеру идёт БЕЗ скачивания, через ту же ветку, что
    'local' (download_clip)."""
    name = "fal"

    def __init__(self):
        self._model = os.environ.get(_FAL_MODEL_ENV) or _FAL_MODEL_DEFAULT

    def _budget(self):
        """Потолок трат из env. Пусто/битое/≤0 → 0.0 (кэш-онли)."""
        raw = (os.environ.get(_FAL_SPEND_ENV) or "").strip()
        if not raw:
            return 0.0
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 0.0

    def _normalize(self, path):
        """Привести клип к вертикали 1080×1920 (cover + центр-кроп) и вернуть путь.

        Зачем. Отборщик кандидатов держит ЖЁСТКИЙ порог разрешения (_res_ok:
        высота ≥ область = 1920, RES_MARGIN=1.0), потому что движок не тянет
        исходник вверх — апскейл со стока = мыло. Но у ГЕНЕРАЦИИ выше исходника
        нет: любая модель fal отдаёт ≤1280 по высоте, и сырой клип отсеялся бы
        ДО рендера — формат «с fal-вставками» остался бы без единой вставки.
        Для сгенерированного кадра выбор не «резкий сток или мыльный апскейл», а
        «апскейл или вообще ничего», поэтому здесь мы осознанно нормализуем клип
        под область: файл РЕАЛЬНО становится 1080×1920, и дальше идёт честно, как
        любой вертикальный кандидат. Результат кэшируется рядом с исходником —
        второй раз не пересчитываем.
        """
        import subprocess
        src = str(path)
        base, _ = os.path.splitext(src)
        dst = base + "_v1080x1920.mp4"
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            return dst
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            r = subprocess.run(
                [ff, "-y", "-loglevel", "error", "-i", src, "-vf",
                 "scale=1080:1920:force_original_aspect_ratio=increase,"
                 "crop=1080:1920", "-c:v", "libx264", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-an", dst],
                capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
                return dst
        except Exception:                  # ffmpeg недоступен/упал — отдаём сырой
            pass
        return src                         # честный fallback: пусть решает _res_ok

    def _candidate(self, query, path):
        """Готовый файл → кандидат формы локального банка (talk_broll_index)."""
        import cv2
        cap = cv2.VideoCapture(str(path))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        dur = (n / fps) if fps else 0.0
        tag = os.path.splitext(os.path.basename(str(path)))[0]
        return {
            "source": "fal",
            "id": "fal_%s" % tag,
            "url": None,
            "path": str(path),
            "w": w, "h": h,
            "duration": round(dur, 2),
            "tags": query,
        }

    def search(self, query, dur, n=1):
        bg = _load_bench()
        if bg is None:                     # клиентская коробка без стенда —
            return []                      # источник пуст, идём в общий fallback
        # 1) уже сгенерировано? отдаём без единого платного запроса.
        hit = bg.cache_lookup(self._model, query)
        if hit and os.path.exists(hit) and os.path.getsize(hit) > 0:
            return [self._candidate(query, self._normalize(hit))]
        # 2) тратить нельзя (не под spend-режимом или запрещено тестами) —
        #    кэш пуст → источник пуст, блок уйдёт в общий fallback.
        limit = self._budget()
        if limit <= 0 or _fal_spend_forbidden():
            return []
        # 3) догенерировать через стенд — весь учёт денег и потолок там.
        jobs = bg.plan_jobs([query], [self._model])
        records, _ = bg.run_jobs(jobs, max_usd=limit)
        for r in records:
            f = r.get("файл")
            if (r.get("статус") in ("ок", "кэш") and f
                    and os.path.exists(f) and os.path.getsize(f) > 0):
                return [self._candidate(query, self._normalize(f))]
        return []


SOURCES = {"pixabay": PixabaySource, "pexels": PexelsSource,
           "local": _local_source, "fal": FalSource}


# ── ВЫБОР КАНДИДАТА ──────────────────────────────────────────────────────────
def pick(cands, dur):
    """Лучший кандидат под блок. Сток уже вернул по релевантности запросу —
    её и уважаем (берём первого подходящего), только отсеиваем битые размеры
    (0×0 у некоторых hit) и предпочитаем ролик не короче нужного (без зацикла).
    Для нижней половины split кадр всё равно кадрируется по лицу — ориентация
    не критична."""
    if not cands:
        return None
    valid = [c for c in cands if c["w"] > 0 and c["h"] > 0] or cands
    pool = [c for c in valid if c["duration"] >= dur] or valid
    return pool[0]


# ── СКАЧИВАНИЕ ───────────────────────────────────────────────────────────────
def download_clip(cand):
    # локальный банк (E4-10) и генерация fal: файл уже на диске (в .cache_bench)
    # — качать нечего, отдаём путь. Обе ветки идут одним путём: source с готовым
    # path, без url.
    if cand.get("source") in ("local", "fal") and cand.get("path"):
        return cand["path"]
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, cand["id"] + ".mp4")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    req = urllib.request.Request(cand["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


def clip_frame(cand, frac=0.5):
    """Кадр на доле `frac` длительности СКАЧАННОГО клипа (не сток-обложка!).
    Урок: обложка врёт — сток отдаёт кадр зала, а внутри могут быть ноги на
    каблуках. Кэш: frac=0.5 остаётся в старом имени <id>_mid.jpg (совместимость
    с накопленным кэшем), прочие доли — <id>_fPP.jpg (R34/R35)."""
    try:
        path = download_clip(cand)
    except Exception:
        return None
    frac = min(max(float(frac), 0.0), 0.99)
    name = ("_mid.jpg" if abs(frac - 0.5) < 1e-9
            else "_f%02d.jpg" % int(round(frac * 100)))
    jpg = os.path.join(CACHE, cand["id"] + name)
    if os.path.exists(jpg) and os.path.getsize(jpg) > 0:
        return jpg
    c = cv2.VideoCapture(path)
    n = c.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    c.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(n * frac)))
    ok, fr = c.read()
    c.release()
    if not ok:
        return None
    cv2.imwrite(jpg, fr)
    return jpg


def clip_midframe(cand):
    """Превью из СЕРЕДИНЫ ролика — прежний контракт (render_reels.choice_sheet
    и _score_cheap для локального банка)."""
    return clip_frame(cand, 0.5)


# ── CLIP-ПЕРЕРАНЖИРОВКА ──────────────────────────────────────────────────────
# Переиспользуем embed_text/embed_image из talk_broll_index (там уже лежит модель
# и ленивый _load). Здесь — только «дешёвый» слой поверх стоковых кандидатов.
_CLIP_MOD = None
_CLIP_OFF = False


def _clip_mod():
    """talk_broll_index (с embed_text/embed_image) или None при мягком фолбэке.
    Импорт+модель тяжёлые — грузим лениво, один раз. Любой сбой (нет open_clip,
    нет весов, нет сети под веса) → печатаем причину и навсегда отключаем
    переранжировку в этом процессе, прежний порядок Pixabay остаётся рабочим."""
    global _CLIP_MOD, _CLIP_OFF
    if _CLIP_OFF:
        return None
    if _CLIP_MOD is None:
        try:
            import talk_broll_index as tbi
            tbi.embed_text("warmup")   # тянет модель/веса — если упадёт, узнаем сразу
            _CLIP_MOD = tbi
        except Exception as ex:
            print("  ! CLIP недоступен (%s) — переранжировка выключена, "
                  "прежний порядок Pixabay" % ex)
            _CLIP_OFF = True
            return None
    return _CLIP_MOD


def _embed_file(mod, path):
    """CLIP-эмбеддинг картинки с диска (jpg/png). None если файл не читается."""
    try:
        img = cv2.imread(path)
        if img is None:
            return None
        return mod.embed_image(img)
    except Exception:
        return None


def _thumb_path(cand):
    """Скачать дешёвую превью-картинку кандидата (thumbnail стока) в кэш.
    Экономия трафика РФ-сети: скорим по превью, целиком клип качаем только
    победителя. None — если у кандидата нет thumb или превью не скачалось."""
    url = cand.get("thumb")
    if not url:
        return None
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, cand["id"] + "_thumb.jpg")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(p, "wb") as f:
            f.write(r.read())
    except Exception:
        return None
    return p if os.path.getsize(p) > 0 else None


def _score_cheap(mod, cand, qv):
    """Дешёвый косинус кандидата к запросу: по thumbnail (без полной закачки).
    Локальный банк уже на диске — берём его честный мидфрейм. Стоки без thumb —
    падаем на полный мидфрейм (редко). None, если кадр не добыть."""
    if cand.get("source") == "local":
        jpg = clip_midframe(cand)
    else:
        jpg = _thumb_path(cand) or clip_midframe(cand)
    if not jpg:
        return None
    iv = _embed_file(mod, jpg)
    if iv is None:
        return None
    return float((qv * iv).sum())     # оба нормированы → dot = косинус


# R34: доли длительности для честного скора победителя. Один мидфрейм слеп к
# клипам, «уезжающим из темы» внутри сцены (интро с титром, смена плана) —
# смотрим три трети. Итог = min: клип обязан держать тему ВЕЗДЕ.
_FULL_FRACS = (0.25, 0.5, 0.75)


def _score_full(mod, cand, qv):
    """Честный косинус по ТРЁМ кадрам реального клипа (25/50/75% длительности),
    итог = min (R34). Полная закачка — потому зовётся ТОЛЬКО для победителя
    (verify_winner), не для всего top-N. Кадры, которые не добылись, просто
    выпадают; ни одного кадра → None (решает дешёвый скор, как раньше)."""
    scores = []
    for frac in _FULL_FRACS:
        jpg = clip_frame(cand, frac)
        if not jpg:
            continue
        iv = _embed_file(mod, jpg)
        if iv is None:
            continue
        scores.append(float((qv * iv).sum()))
    return min(scores) if scores else None


# R37: порог «скоры практически равны» для tie-break по высоте. Меньше шума
# CLIP-скора превью (thumb недооценивает клип сильнее), порядок «по смыслу»
# не ломает — переставляем только внутри групп почти-равных.
_SCORE_EPS = 0.01


def _prefer_taller(scored, eps=_SCORE_EPS):
    """R37: при |Δ clip_score| < eps выше клип с БОЛЬШЕЙ высотой — вместо чистой
    отсечки апскейла предпочитаем 4K: ниже used_scale и запас на кроп окна (R35).
    Группа = подряд идущие кандидаты, чей скор отличается от лидера группы
    меньше eps; внутри группы сортируем по h по убыванию, между группами
    порядок по скору сохраняется. Вход уже отсортирован по скору."""
    out, i = [], 0
    while i < len(scored):
        j = i
        while (j < len(scored)
               and scored[i]["clip_score"] - scored[j]["clip_score"] < eps):
            j += 1
        out += sorted(scored[i:j], key=lambda c: int(c.get("h") or 0),
                      reverse=True)
        i = j
    return out


def rank_by_clip(cands, query, threshold=CLIP_THRESHOLD, topn=RERANK_TOPN,
                 drop_below=True):
    """Пересортировать стоковых кандидатов по СМЫСЛУ, а не по популярности.

    Скорим верхние `topn` (по популярности) кандидатов косинусом их превью к
    embed_text(query), сортируем по убыванию. `drop_below` отсекает всё ниже
    (threshold - запас): очевидный мусор уходит уже на дешёвом этапе, а честную
    проверку победителя по реальному мидфрейму делает broll_for_blocks. Мягкий
    фолбэк: если CLIP недоступен — возвращаем вход как есть (прежний порядок)."""
    mod = _clip_mod()
    if mod is None or not cands or not query:
        return cands
    try:
        qv = mod.embed_text(query)
    except Exception as ex:
        print("  ! CLIP embed_text упал (%s) — прежний порядок" % ex)
        return cands
    scored = []
    for c in cands[:topn]:
        s = _score_cheap(mod, c, qv)
        if s is None:
            continue
        c = dict(c)
        c["clip_score"] = round(s, 4)
        c["clip_stage"] = "thumb"
        scored.append(c)
    if not scored:
        return cands            # ничего не удалось сосчитать → не режем вслепую
    scored.sort(key=lambda c: c["clip_score"], reverse=True)
    scored = _prefer_taller(scored)          # R37: почти равный смысл → выше h
    if drop_below:
        # На дешёвом этапе порог мягче на 0.03: превью недооценивает клип,
        # окончательно решает честный мидфрейм победителя в broll_for_blocks.
        lo = max(0.0, threshold - 0.03)
        scored = [c for c in scored if c["clip_score"] >= lo]
    return scored


def verify_winner(cand, query, threshold=CLIP_THRESHOLD):
    """Честная проверка выбранного клипа по СЕРЕДИНЕ реального ролика.
    Возвращает (ok, score): ok=False → перебивку не ставить (говорящая голова).
    Если у кандидата уже честный мидфрейм-скор (локальный банк) — не пересчитываем.
    При недоступном CLIP всегда (True, None) — не блокируем прежний путь."""
    mod = _clip_mod()
    if mod is None or not query:
        return True, cand.get("clip_score")
    if cand.get("clip_stage") == "mid" and cand.get("clip_score") is not None:
        return cand["clip_score"] >= threshold, cand["clip_score"]
    try:
        qv = mod.embed_text(query)
    except Exception:
        return True, cand.get("clip_score")
    s = _score_full(mod, cand, qv)
    if s is None:
        # мидфрейм не добыли — доверяем дешёвому скору, если он был
        prev = cand.get("clip_score")
        return (prev is None or prev >= threshold), prev
    return s >= threshold, round(s, 4)


# ── ВЫБОР ОКНА ПОКАЗА (R35) ──────────────────────────────────────────────────
# Сколько окон-кандидатов скорить внутри клипа-победителя. Нечётное → среднее
# окно совпадает с прежней «слепой серединой» (честная база сравнения).
_SEG_WINDOWS = 5


def _embed_frame_cached(mod, cand, frac):
    """CLIP-эмбеддинг кадра клипа на доле frac с ДИСКОВЫМ кэшем (<кадр>.npy
    рядом с jpg): повторный подбор/пересборка не декодирует видео и не гоняет
    модель заново. None — кадр не добыть/не посчитать."""
    jpg = clip_frame(cand, frac)
    if not jpg:
        return None
    npy = jpg[:-4] + ".npy"
    if os.path.exists(npy):
        try:
            return np.load(npy)
        except Exception:
            pass                              # битый кэш — пересчитаем
    iv = _embed_file(mod, jpg)
    if iv is not None:
        try:
            np.save(npy, iv)
        except Exception:
            pass                              # кэш — оптимизация, не обязанность
    return iv


#: ПОЛОСА ПОДПИСИ — доли высоты кадра, где стоит субтитр кодовых форматов
#: (sub_cy 871 из 1920 = 0.4536, плюс запас на высоту блока в две строки).
_SUB_BAND = (0.36, 0.56)
_BAND_FACE_PEN = 0.25      # лицо в полосе — тяжёлый штраф, важнее релевантности
_BAND_BRIGHT_PEN = 0.15    # светлая полоса — белый текст на ней не читается
_BAND_BRIGHT_LIM = 0.62    # выше этой средней яркости полоса считается светлой
_band_det = None


def _band_face_det():
    """Детектор лиц для отбора кусков клипа. Создаётся один раз и лениво:
    подбор без полосы (все прежние форматы) его не трогает вовсе."""
    global _band_det
    if _band_det is None:
        import mediapipe as mp
        _band_det = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5)
    return _band_det


def _band_penalty(cand, frac):
    """Насколько плох кусок клипа ПОД ПОДПИСЬЮ. 0.0 — полоса чистая и тёмная.

    ЗАЧЕМ. На полнокадровой вставке (раскладка card) подпись лежит поверх ЧУЖОГО
    видео. Если в полосе лицо — нарушается правило владельца «текст не должен
    закрывать лицо никогда»; если полоса светлая — белый текст даёт контраст
    ниже 2:1 и приёмка валит ролик. Оба замера 28.08.2026 на живой сборке.
    Уводить подпись с лица в этой раскладке часто некуда: кадр занят целиком.
    Дешевле не брать такой кусок вовсе, чем чинить последствия."""
    jpg = clip_frame(cand, frac)
    if not jpg:
        return 0.0
    img = cv2.imread(jpg)
    if img is None:
        return 0.0
    h, w = img.shape[:2]
    y0, y1 = int(h * _SUB_BAND[0]), int(h * _SUB_BAND[1])
    if y1 <= y0:
        return 0.0
    pen = 0.0
    bright = float(img[y0:y1].mean()) / 255.0
    if bright > _BAND_BRIGHT_LIM:
        pen += _BAND_BRIGHT_PEN * (bright - _BAND_BRIGHT_LIM) / (1.0 - _BAND_BRIGHT_LIM)
    try:
        res = _band_face_det().process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    except Exception:
        return pen                      # детектор не поднялся — только яркость
    for d in (res.detections or []):
        bb = d.location_data.relative_bounding_box
        fy0, fy1 = bb.ymin * h, (bb.ymin + bb.height) * h
        if fy1 > y0 and fy0 < y1:       # лицо пересекает полосу подписи
            pen += _BAND_FACE_PEN
            break
    return pen


#: ЗАПРЕТ ПО КАРТИНКЕ (владелец 28.08.2026). Фильтр по тегам дырявый: клип с
#: тегами «friends laughing together rooftop evening» показывал БОКАЛЫ С ВИНОМ —
#: теги описывают не всё, что в кадре. Поэтому кадр-кандидат дополнительно
#: сверяется по смыслу тем же CLIP, которым меряется релевантность.
_BAN_PROMPTS = (
    "a glass of wine, alcohol, beer, cocktail, drinking alcohol",
    "a person smoking a cigarette, tobacco, vape",
    "nudity, erotic scene, lingerie, sexual content",
    "church, prayer, religious icon, cross, temple",
    "tarot cards, astrology, horoscope, occult ritual, crystal ball",
)
#: КАЛИБРОВКА 28.08.2026 на реальных кадрах (замер, не на глаз):
#:   кадр с бокалами вина -> 0.234 «a glass of wine»   — верное срабатывание
#:   аллея (чисто)        -> 0.182 «nudity»            — шум
#:   спикер (чисто)       -> 0.191 «nudity»            — шум
#: Голый порог не годится: ложные срабатывания шли на 0.22 («курение» на клипе
#: с телефоном), а истинное попадание всего 0.234 — зазор слишком узкий.
#: Поэтому решает НЕ порог, а сравнение с релевантностью самого клипа: запретное
#: должно перевешивать то, ради чего клип взят.
#:   вино 0.234 > релевантность 0.227  -> отсекаем
#:   курение 0.220 < релевантность 0.255 -> оставляем
#: Порог ниже — только нижняя отсечка шума.
_BAN_CLIP_THRESHOLD = 0.22
_ban_vecs = None


def _ban_vectors(mod):
    """Эмбеддинги запретных тем. Считаются один раз на процесс."""
    global _ban_vecs
    if _ban_vecs is None:
        _ban_vecs = [mod.embed_text(q) for q in _BAN_PROMPTS]
    return _ban_vecs


#: ── ДВЕ ПРОВЕРКИ КАДРА-КАНДИДАТА (владелец 02.09.2026) ──────────────────────
#: 1) ДРОЖАНИЕ. Замер на готовом ролике: у клипа со стеллажом p90 сдвига 3.1-3.4
#:    px при 0.5-1.1 у соседних врезок, максимум 20 px. Это съёмка с рук —
#:    обработкой не лечится, такой клип надо просто не брать.
#: 2) ЧЕЛОВЕК ЦЕЛИКОМ. Требование «без обрезанных туловищ и голов» по ТЕГАМ не
#:    проверяется — теги пишут люди и пишут не то, что в кадре (проверено:
#:    запрет слов hand/leg выкосил пул и не помог). Проверяем скелетом.
VW, VH = 1080, 1920      # кадр рилса
_SHAKE_P90_MAX = 2.0        # px в масштабе 1080; выше — дрожащий клип
#: 02.09.2026: ставил 1.05 — перебор. Предфильтр отбраковывал клипы на
#: 1.81, из-за чего врезок не набиралось до порога владельца. Его дело —
#: отсеять явный мусор, а ГАРАНТИЮ даёт проверка готового ролика в
#: render_reels (порог 2.0 по p90 уже в кадре). Дублировать её строгостью
#: здесь незачем: цена ошибки предфильтра — потерянная врезка.
#: КАЛИБРОВКА 02.09.2026 (замер исходника против готового ролика):
#:   pixabay_140807  исходник 1.19 -> в ролике 3.66  (дрожит, отсеять)
#:   pixabay_43459   исходник 0.93 -> в ролике 1.06  (спокоен, оставить)
#: Замер ПО ИСХОДНИКУ занижает дрожание в 1.2-3 раза, и множитель зависит от
#: содержимого — надёжного порога из него не выходит. Поэтому это лишь дешёвый
#: предфильтр, а гарантию даёт проверка ГОТОВОГО ролика в render_reels.
_SHAKE_FRAMES = 24          # подряд идущих кадров для замера
_POSE_VIS = 0.5             # порог видимости точки скелета
_POSE_EDGE = 0.02           # ближе этой доли к краю — считаем обрезанным
_pose_det = None


def _pose():
    global _pose_det
    if _pose_det is None:
        import mediapipe as mp
        _pose_det = mp.solutions.pose.Pose(static_image_mode=True,
                                           model_complexity=1,
                                           min_detection_confidence=0.5)
    return _pose_det


#: ЧЁРНАЯ ВСПЫШКА ВНУТРИ ВРЕЗКИ (владелец 02.09.2026: «без резких переходов»).
#: Замер на готовом ролике: нижняя половина падала до яркости 5 из 255 при
#: 153-160 у соседних кадров — сток отдаёт клипы с затемнением на монтажной
#: склейке, и такая вспышка читается как брак. Ловим до попадания в кадр.
_BLACK_LUMA = 24.0          # ниже этой средней яркости кадр считаем чёрным
_BLACK_MIN_CTX = 70.0       # ...если остальное окно светлее этого


def _window_has_black(path, start, dur, n=12):
    """True — внутри окна показа есть провал в черноту (вспышка/затемнение)."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return False
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    lums = []
    for i in range(n):
        t = float(start) + (float(dur) * i / max(1, n - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, f = cap.read()
        if not ok:
            break
        lums.append(float(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).mean()))
    cap.release()
    if len(lums) < 4:
        return False
    lums_sorted = sorted(lums)
    med = lums_sorted[len(lums_sorted) // 2]
    return min(lums) < _BLACK_LUMA and med > _BLACK_MIN_CTX


def _clip_shake_worst(path, dur=None):
    """ХУДШИЙ p90 дрожания по нескольким местам клипа.

    Мерить только начало нельзя: у pixabay_140807 начало спокойное (p90 0.70),
    а кусок, попавший в ролик, дрожал втрое сильнее (p90 3.1-3.4). В ролик
    берётся ЛЮБОЕ окно клипа, поэтому проверяем несколько и берём худшее."""
    vals = []
    for frac in (0.05, 0.25, 0.45, 0.65, 0.85):
        t = (dur or 0.0) * frac
        v = _clip_shake_p90(path, start=t)
        if v is not None:
            vals.append(v)
    return max(vals) if vals else None


def _clip_shake_p90(path, start=0.0, n=_SHAKE_FRAMES, aspect=9.0 / 16.0):
    """p90 сдвига между подряд идущими кадрами клипа, в масштабе 1080. None —
    не удалось померить. Тот же приём, что в tools_fmt_shake.py."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(max(0.0, start) * fps))
    prev, sh = None, []
    for _ in range(n):
        ok, f = cap.read()
        if not ok:
            break
        # КРОП ПОД ВЕРТИКАЛЬ ДО ЗАМЕРА. Прежний прямой resize(270,480) сжимал
        # горизонтальный клип по ширине и ЗАНИЖАЛ дрожание втрое: у pixabay_140807
        # исходник давал p90 1.16, а в готовом ролике тот же кусок дрожал на 3.66.
        # Меряем то, что реально попадёт в кадр: центральный вертикальный кроп.
        _h, _w = f.shape[:2]
        # aspect — пропорция ОБЛАСТИ, куда врезка реально ляжет. В «половинках»
        # это нижняя половина 1080x960 (почти квадрат), а не 9:16: кроп под 9:16
        # брал узкую колонку и мерил не то движение.
        _cw = min(_w, int(round(_h * float(aspect))))
        _x0 = max(0, (_w - _cw) // 2)
        g = np.float32(cv2.cvtColor(
            cv2.resize(f[:, _x0:_x0 + _cw], (270, 480)), cv2.COLOR_BGR2GRAY))
        if prev is not None:
            (dx, dy), _ = cv2.phaseCorrelate(prev, g)
            d = float(np.hypot(dx, dy)) * 4.0
            if d < 25.0:                      # больше — склейка, не дрожание
                sh.append(d)
        prev = g
    cap.release()
    if len(sh) < 5:
        return None
    return float(np.percentile(sh, 90))


#: КЛЮЧЕВЫЕ ТОЧКИ СКЕЛЕТА MediaPipe Pose: нос, плечи, бёдра, колени, щиколотки.
#: Человек «целиком» — когда ВСЕ они найдены и лежат внутри кадра.
_POSE_KEY = (0, 11, 12, 23, 24, 25, 26, 27, 28)


def _person_cropped(img_bgr):
    """True — человек в кадре обрезан рамкой. False — людей нет ВООБЩЕ либо
    человек виден целиком.

    ЛОВУШКА, на которой я уже ошибся 02.09.2026: MediaPipe помечает точки ЗА
    границей кадра как невидимые (visibility < 0.5), а не выносит их за [0,1].
    Первая версия проверяла только ВИДИМЫЕ точки — и женщина, обрезанная по
    пояс, проходила: 21 точка из 33 внутри кадра, остальные двенадцать (ноги)
    просто «невидимы». Поэтому теперь требуется ПОЛНЫЙ набор ключевых точек:
    нет щиколоток — значит ног в кадре нет, значит человек обрезан."""
    try:
        res = _pose().process(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    except Exception:
        return False
    lm = getattr(res, "pose_landmarks", None)
    if lm is None:
        return False                          # людей не найдено — не наш случай
    pts = lm.landmark
    for i in _POSE_KEY:
        if i >= len(pts):
            return True
        pt = pts[i]
        if pt.visibility < _POSE_VIS:
            return True                       # точки нет в кадре — обрезан
        if not (_POSE_EDGE < pt.x < 1 - _POSE_EDGE
                and _POSE_EDGE < pt.y < 1 - _POSE_EDGE):
            return True                       # точка упирается в рамку
    return False


def _clip_person_cropped(cand, fracs=(0.2, 0.5, 0.8)):
    """True — хотя бы на одном из проверяемых кадров человек обрезан."""
    for frac in fracs:
        jpg = clip_frame(cand, frac)
        if not jpg:
            continue
        img = cv2.imread(jpg)
        if img is None:
            continue
        if _person_cropped(img):
            return True
    return False


#: ЯВНЫЙ СПИСОК ЗАПРЕЩЁННЫХ КЛИПОВ. Автоматика ненадёжна на МЕЛКИХ объектах:
#: CLIP смотрит кадр целиком, и бокал вина в углу даёт 0.223 — да ещё и как
#: «карты таро», а не как алкоголь (замер 02.09.2026 на pixabay_243647).
#: Порогом это не чинится: дело не в пороге, а в том, что метод не различает
#: мелкое. Поэтому рядом с автоматикой живёт список, который её не зависит.
#: Пополнять при каждом промахе — это дешевле, чем ловить одно и то же дважды.
BANNED_CLIP_IDS = {
    "pixabay_243647",   # бокалы с вином на столе (алкоголь), теги «friends
                        # laughing together rooftop evening» — по тегам не виден
}


def _is_banned_by_frame(cand, fracs=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9),
                        relevance=None):
    """True — в кадре видно запрещённое (алкоголь/курение/секс/религия/эзотерика).

    Смотрим три кадра клипа, а не один: запретное может появиться в любой момент.
    CLIP недоступен — возвращаем False (без него запрет не проверить; тег-фильтр
    остаётся первой линией)."""
    if str(cand.get("id")) in BANNED_CLIP_IDS:
        print("  ! запрет по списку: %s" % cand.get("id"))
        return True
    mod = _clip_mod()
    if mod is None:
        return False
    try:
        vecs = _ban_vectors(mod)
    except Exception:
        return False
    for frac in fracs:
        iv = _embed_frame_cached(mod, cand, frac)
        if iv is None:
            continue
        for q, bv in zip(_BAN_PROMPTS, vecs):
            sc = float((bv * iv).sum())
            if sc < _BAN_CLIP_THRESHOLD:
                continue
            # запретное должно ПЕРЕВЕШИВАТЬ то, ради чего клип взят
            if relevance is not None and sc <= float(relevance):
                continue
            print("  ! запрет по кадру: «%s» %.3f > релевантность %s — %s"
                  % (q.split(",")[0], sc,
                     ("%.3f" % relevance) if relevance is not None else "н/д",
                     cand.get("id")))
            return True
    return False


def _pick_seg(cand, dur, query, k=_SEG_WINDOWS, avoid_band=False):
    """R35: начало показа (seg) = лучшее из k окон клипа по CLIP, а не слепая
    середина. Скорим кадр из ЦЕНТРА каждого окна [seg, seg+dur] против
    visual_query — релевантность фактически показанного куска равна
    релевантности выбора. Фолбэк байт-в-байт в прежнюю середину: CLIP
    недоступен / пустой запрос / клип не длиннее блока / ни одно окно не
    посчиталось."""
    D = float(cand.get("duration", 0) or 0)
    mid = max(0.0, D / 2.0 - dur / 2.0)       # прежняя «слепая середина»

    def _mid_or_none():
        # ЖЁСТКИЙ ОТСЕВ (решение владельца 28.08.2026) действует и на фолбэках:
        # молча отдать середину светлого клипа значило бы обойти правило.
        if avoid_band and _band_penalty(cand, (mid + dur / 2.0) / D if D else 0.5) > 0.0:
            return None
        return mid

    if D <= 0 or D <= dur or not query:
        return _mid_or_none()
    mod = _clip_mod()
    if mod is None:
        return _mid_or_none()
    try:
        qv = mod.embed_text(query)
    except Exception:
        return mid
    span = D - dur
    best_seg, best_s = mid, None
    for i in range(k):
        seg = span * i / (k - 1) if k > 1 else mid
        frac = (seg + dur / 2.0) / D
        iv = _embed_frame_cached(mod, cand, frac)
        if iv is None:
            continue
        if avoid_band and _band_penalty(cand, frac) > 0.0:
            continue                      # полоса подписи занята лицом/светлая
        s = float((qv * iv).sum())
        if best_s is None or s > best_s:
            best_s, best_seg = s, seg
    if avoid_band and best_s is None:
        return None                       # чистого окна нет — клип не берём
    return best_seg


def _block_queries(b):
    """(основной_запрос, [все_запросы]) для блока. Мультизапрос (P1): блок может
    нести `visual_queries` — список из 2–3 альтернатив (буквальный/метафора/
    предметный). Пул кандидатов объединяем по всем, а CLIP-скорим против ОСНОВНОГО
    смысла (`visual_query`). Обратная совместимость: нет `visual_queries` —
    работаем по единственному `visual_query`, как раньше."""
    main = (b.get("visual_query") or "").strip()
    alts = b.get("visual_queries")
    if isinstance(alts, str):
        alts = [alts]
    alts = [q.strip() for q in (alts or []) if q and q.strip()]
    if not main and alts:
        main = alts[0]
    qs = [main] + [q for q in alts if q != main] if main else alts
    return main, (qs or ([main] if main else []))


def candidates_for_blocks(blocks, providers=("pixabay",), per=3,
                          rerank=RERANK_DEFAULT, clip_threshold=CLIP_THRESHOLD,
                          rerank_topn=RERANK_TOPN, exclude_ids=None, wide=False,
                          template=None, filter_res=None):
    """На каждый блок — до `per` кандидатов. По умолчанию (rerank=True) порядок —
    по CLIP-релевантности мидфрейма к visual_query, а не по популярности Pixabay;
    поддержан мультизапрос (`visual_queries`). rerank=False → прежнее поведение
    (первый популярный). Возвращает [{block, cands}].

    exclude_ids — id клипов, уже занятых другими роликами (требование 4:
    уникальный сток на каждый Reels) — такие кандидаты исключаются.
    wide=True (требование 1) — расширяем запрос дальними терминами и отсеиваем
    крупные/средние планы человека на фоне (по площади лица в превью).

    template (серия «качество монтажа», задача 1) — раскладка, куда ляжет перебивка
    (split/cutout/text-behind). Задаёт высоту области (area_height) для ОТСЕВА клипов
    ниже разрешения. filter_res: None → включён, если раскладка отсекаема (template
    в _AREA_H); False → отсев ВЫКЛЮЧЕН (замер «до» без фильтра). БЕЗ template отсев
    выключен и путь БАЙТ-В-БАЙТ прежний (старые форматы не трогаем)."""
    exclude = set(exclude_ids or [])
    srcs = [SOURCES[p]() for p in providers]
    area_h = area_height(template)
    if filter_res is None:
        filter_res = template in _AREA_H
    groups = []
    for b in blocks:
        dur = b["t1"] - b["t0"]
        main_q, queries = _block_queries(b)
        if wide:                              # дальний план: расширяем запросы
            queries = [_widen(q) for q in queries]
            main_q = _widen(main_q)
        cands = []
        for s in srcs:
            for q in queries:                 # мультизапрос: объединяем пул
                try:
                    cands += s.search(q, dur)
                except SystemExit:
                    raise
                except Exception as ex:
                    print("  ! %s упал на «%s»: %s" % (s.name, q, ex))
        seen, uniq = set(), []
        for c in cands:
            if c["id"] in seen or c["id"] in exclude:   # уникальность сток (треб.4)
                continue
            seen.add(c["id"])
            uniq.append(c)
        valid = [c for c in uniq if c["w"] > 0 and c["h"] > 0] or uniq
        if wide:
            # tag-уровневый отсев крупного плана ЧЕЛОВЕКА (треб.1): маркеры
            # _BAN_FACE в тегах кандидата. Дёшево (без сети) и СТРОГО в wide-пути —
            # дефолтный путь этих терминов не видит вовсе. Блок не опустошаем: если
            # отфильтровалось всё, оставляем исходный valid (лицо добьёт
            # _is_close_subject по превью + CLIP-порог).
            faced = [c for c in valid if not _is_face_tag(c.get("tags", ""))]
            valid = faced or valid
        if filter_res:
            # ОТСЕВ НИЗКОГО РАЗРЕШЕНИЯ (задача 1): клип ниже области (1920) → апскейл
            # → мыло. Пул опустел — не опускаем порог, а поднимаемся по лестнице
            # fallback (расширить запрос → локальный банк → блок без перебивки).
            valid = _res_filter_pool(valid, queries, dur, srcs, area_h, wide,
                                     exclude, providers)
        ranked = ([c for c in valid if c["duration"] >= dur]
                  + [c for c in valid if c["duration"] < dur])
        # Отсев крупных/средних планов человека по превью — ВО ВСЕХ путях (R4),
        # не только wide: крупное лицо перебивки спорит с лицом спикера и в
        # дефолтном split. Смотрим только верхние rerank_topn (по ним и так
        # качаются превью для CLIP), хвост оставляем как запас. Блок не
        # опустошаем: фильтрат пуст → исходный ranked (как faced выше) — лучше
        # кандидат с лицом, чем блок вовсе без перебивки.
        head = [c for c in ranked[:rerank_topn] if not _is_close_subject(c)]
        if head:
            _STATS["cand_dropped_face"] += len(ranked[:rerank_topn]) - len(head)
            ranked = head + ranked[rerank_topn:]
        if rerank:
            reranked = rank_by_clip(ranked, main_q, threshold=clip_threshold,
                                    topn=rerank_topn)
            groups.append({"block": b, "cands": reranked[:per]})
        else:
            groups.append({"block": b, "cands": ranked[:per]})
    return groups


# ── LOCKFILE ПОДБОРА (R38) ───────────────────────────────────────────────────
# Подбор со стока недетерминирован снаружи (выдача/рейтинг Pixabay меняются со
# временем) — лок замораживает результат: {блок → cand, seg, sha256 файла}.
# Повторная сборка того же ролика по локу идёт БЕЗ сети, из кэша, и байт-в-байт
# воспроизводит вставки (Часть 6 аудита, вопрос 3).

def lock_path_for(blocks_path):
    """Путь лока рядом с blocks.json: <name>_stock.lock.json (конвенция R38)."""
    return re.sub(r"\.json$", "", blocks_path) + "_stock.lock.json"


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save_lock(inserts, path):
    """Записать лок подбора. src в локе — ИМЯ файла, если он лежит в кэше
    (переносимо между машинами, абсолютных путей в артефакте нет); файлы вне
    кэша (локальный банк) — путь как есть."""
    import json
    ent = []
    for it in inserts:
        e = dict(it)
        # T-pip: клип-кандидат может не прочитаться целиком — большой 4K-файл
        # «только в облаке» на Google Drive отдаёт OSError (Errno 22) при чтении
        # всего объёма. Одна такая врезка НЕ должна ронять весь рендер (TAIL-06:
        # ролик уже собран, лок — лишь для детерминированной пересборки).
        # Пропускаем её из лока с предупреждением, а не падаем. Полезно и
        # клиентам: у них Google Drive так же держит файлы «только в облаке».
        try:
            e["sha256"] = _sha256(it["src"])
        except OSError as ex:
            print("  ! лок: клип не прочитался целиком (%s) — врезка не попадёт "
                  "в лок, но в ролике осталась: %s" % (ex, it.get("src")))
            continue
        if os.path.dirname(os.path.abspath(it["src"])) == os.path.abspath(CACHE):
            e["src"] = os.path.basename(it["src"])
        ent.append(e)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "inserts": ent}, f, ensure_ascii=False, indent=1)
    return path


def load_lock(path):
    """Восстановить inserts из лока БЕЗ сети. Файл ищем в кэше (имя) или по
    записанному пути (локальный банк); sha256 сверяем — подменённый/битый кэш
    не должен молча попасть в рендер. Отсутствие файла/расхождение хэша —
    SystemExit с внятным текстом (сборка по локу обязана быть точной, «почти
    тот» клип хуже честного отказа)."""
    import json
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    inserts = []
    for e in data.get("inserts", []):
        src = e["src"]
        p = src if os.path.isabs(src) or os.path.sep in src \
            else os.path.join(CACHE, src)
        if not os.path.exists(p):
            raise SystemExit("лок %s: файла %s нет в кэше — скачай онлайн-"
                             "прогоном или перенеси .cache_broll" % (path, src))
        want = e.get("sha256")
        if want and _sha256(p) != want:
            raise SystemExit("лок %s: %s не совпал по sha256 — кэш подменён/"
                             "битый, пересобери онлайн" % (path, src))
        it = {k: v for k, v in e.items() if k != "sha256"}
        it["src"] = p
        # реестр разрешений — те же побочные данные, что при живом подборе
        # (checks.verify_talk достаёт размер по cand)
        if it.get("cand") and it.get("w") is not None:
            USED_CLIP_RES[it["cand"]] = (it.get("w"), it.get("h"))
        inserts.append(it)
    return inserts


def _cap_inserts(inserts, limit=MAX_BROLL_INSERTS):
    """T7: жёсткий потолок — не больше `limit` b-roll-вставок за ролик, что бы
    ни насчитал планировщик/подбор. Лишние (по хронологии, ХВОСТ списка) просто
    не идут в inserts — engine_talk на этих сегментах покажет спикера на весь
    кадр (контент не режется и не выбрасывается, только не превращается в
    перебивку). Первые `limit` по времени сохраняются — порядок появления в
    речи предсказуемее любого скора и не зависит от провайдера/CLIP-ранга.
    Действует на ОБА пути `broll_for_blocks` — живой подбор и повтор из лока
    (`--from-lock`), поэтому старый лок с бОльшим числом вставок тоже не
    нарушит потолок при повторной сборке."""
    if len(inserts) <= limit:
        return inserts
    # РАВНОМЕРНО ПО ХРОНОМЕТРАЖУ (владелец 02.09.2026), а не «первые по времени».
    # Прежнее правило сдвигало все врезки в начало ролика; выбираем позиции,
    # разложенные по всей длине, чтобы зритель не оставался без картинки
    # длинными кусками.
    _idx = sorted({int(round(k * (len(inserts) - 1) / max(1, limit - 1)))
                   for k in range(limit)})
    keep = [inserts[i] for i in _idx][:limit]
    _kept = set(id(x) for x in keep)
    drop = [x for x in inserts if id(x) not in _kept]
    print("  ! потолок b-roll: %d вставок хотелось, оставляю %d (первые по "
          "времени) — %d сегмент(ов) остаются на спикере: %s"
          % (len(inserts), limit, len(drop),
             "; ".join("«%s» %.1f-%.1f" % (d.get("query", ""), d["t0"], d["t1"])
                       for d in drop)))
    return keep


# ── ГЛАВНОЕ: блоки → inserts ─────────────────────────────────────────────────
def broll_for_blocks(blocks, providers=("pixabay",), picks=None, per=3,
                     rerank=RERANK_DEFAULT, clip_threshold=CLIP_THRESHOLD,
                     rerank_topn=RERANK_TOPN, exclude_ids=None, wide=False,
                     template=None, filter_res=None,
                     lock_out=None, from_lock=None,
                     max_inserts=MAX_BROLL_INSERTS):
    """blocks[i] = {t0,t1,visual_query,...} → inserts для render(template=split).
    По умолчанию (rerank=True) кандидаты отсортированы по CLIP-релевантности, и
    вариант 1 = самый релевантный (а не самый популярный). picks = {индекс_блока:
    номер_варианта (1..per)} форсирует ручной выбор Валерии по листу. Блоки без
    кандидата ИЛИ где лучший клип ниже CLIP-порога пропускаются — там сплит
    покажет спикера на весь кадр (лучше чистая голова, чем сток не в тему).

    exclude_ids (требование 4) — клипы, занятые другими роликами: не переиспользуем.
    wide (требование 1) — только дальние/общие планы стока (расширение запроса +
    отсев крупных планов человека).
    template (серия «качество монтажа», задача 1) — раскладка перебивки: включает
    отсев клипов ниже разрешения области (см. candidates_for_blocks). filter_res=False
    — отсев выкл (замер «до»). БЕЗ template путь БАЙТ-В-БАЙТ прежний.

    R38: from_lock=путь — вернуть вставки ИЗ ЛОКА (без сети, из кэша, sha256
    сверяется; остальные параметры подбора игнорируются — подбор уже сделан).
    lock_out=путь — после подбора записать лок (конвенция имени —
    lock_path_for(blocks_path)). Оба None → путь байт-в-байт прежний.

    Побочно для замера/проверки: заполняет реестр USED_CLIP_RES (cand→(w,h)) —
    из него checks.verify_talk достаёт разрешение по cand из meta['broll'] — и копит
    stats() (доля отсева, доля fallback, средний масштаб использованных клипов)."""
    if from_lock:
        inserts = load_lock(from_lock)
        print("  lock: %d вставок из %s (без сети)" % (len(inserts), from_lock))
        return _cap_inserts(inserts, max_inserts)
    picks = picks or {}
    reset_stats()
    area_h = area_height(template)
    groups = candidates_for_blocks(blocks, providers, per=per, rerank=rerank,
                                   clip_threshold=clip_threshold,
                                   rerank_topn=rerank_topn,
                                   exclude_ids=exclude_ids, wide=wide,
                                   template=template, filter_res=filter_res)
    inserts = []
    for i, g in enumerate(groups):
        b, cands = g["block"], g["cands"]
        if not cands:
            print("  ! нет b-roll для «%s» (%.1f-%.1f)"
                  % (b["visual_query"], b["t0"], b["t1"]))
            continue
        forced = i in picks or str(i) in picks
        v = int(picks.get(i, picks.get(str(i), 1)))       # вариант (1-based)
        # ПЕРЕБОР ВАРИАНТОВ (владелец 02.09.2026). Раньше брался ровно
        # один кандидат, и ЛЮБОЙ отказ фильтра (обрезанный человек,
        # дрожание, чёрная вспышка, запрет) бросал блок целиком —
        # врезок не набиралось до порога. Теперь пробуем варианты по
        # очереди и берём первый, прошедший все проверки.
        _variants = ([cands[min(max(v, 1), len(cands)) - 1]] if forced
                     else list(cands))
        _placed = False
        for c in _variants:
            try:
                src = download_clip(c)
            except Exception as ex:
                print("  ! не скачался %s: %s" % (c["id"], ex))
                continue
            # Честная проверка ПОБЕДИТЕЛЯ по реальному мидфрейму (полный клип уже
            # скачан). Ручной выбор Валерии (picks) не режем — она видела кадр сама.
            if rerank and not forced:
                ok, sc = verify_winner(c, b.get("visual_query", ""),
                                       threshold=clip_threshold)
                if not ok:
                    print("  ! отсев по CLIP (%.3f < %.2f): «%s» (%.1f-%.1f) — "
                          "оставляю спикера"
                          % ((sc if sc is not None else -1.0), clip_threshold,
                             b.get("visual_query", ""), b["t0"], b["t1"]))
                    continue
                c = dict(c)
                c["clip_score"] = sc
            # ЧЕЛОВЕК ЦЕЛИКОМ (владелец 02.09.2026, «ни единого компромисса»):
            # обрезанное туловище или голова у края — клип не берём вовсе.
            if _clip_person_cropped(c):
                print("  ! отсев: человек обрезан рамкой — «%s» (%.1f-%.1f), "
                      "оставляю спикера" % (b.get("visual_query", ""), b["t0"], b["t1"]))
                continue
            if _is_banned_by_frame(c, relevance=c.get("clip_score")):
                print("  ! отсев по запрету содержимого: «%s» (%.1f-%.1f) — "
                      "оставляю спикера"
                      % (b.get("visual_query", ""), b["t0"], b["t1"]))
                continue
            # окно показа: лучшее из _SEG_WINDOWS окон по CLIP (R35); при
            # недоступном CLIP — прежняя середина (репрезентативнее интро/аутро).
            seg = _pick_seg(c, b["t1"] - b["t0"], b.get("visual_query", ""),
                            avoid_band=(template == "card"))
            # ДРОЖАНИЕ ИМЕННО ТОГО ОКНА, которое пойдёт в ролик. Замер по клипу
            # целиком врал в обе стороны: у одного клипа спокойное начало и тряский
            # конец (p90 0.70 при 3.1-3.4 в кадре), у другого наоборот. Окно выбрано
            # выше — меряем его.
            if seg is not None:
                try:
                    _asp = (VW / (VH / 2.0)) if template == "split" else (VW / float(VH))
                    _sp = _clip_shake_p90(download_clip(c), start=float(seg),
                                          aspect=_asp)
                except Exception:
                    _sp = None
                try:
                    _blk = _window_has_black(download_clip(c), float(seg),
                                             b["t1"] - b["t0"])
                except Exception:
                    _blk = False
                if _blk:
                    print("  ! отсев: чёрная вспышка внутри окна — «%s» (%.1f-%.1f), "
                          "оставляю спикера"
                          % (b.get("visual_query", ""), b["t0"], b["t1"]))
                    continue
                if _sp is not None and _sp > _SHAKE_P90_MAX:
                    print("  ! отсев: дрожащее окно клипа (p90 %.2f px > %.1f) — "
                          "«%s» (%.1f-%.1f), оставляю спикера"
                          % (_sp, _SHAKE_P90_MAX, b.get("visual_query", ""),
                             b["t0"], b["t1"]))
                    continue
            if seg is None:
                # Полнокадровая вставка: во всём клипе нет куска, где полоса под
                # подписью тёмная и без лица. Белый текст на белом кадре не читается
                # (замер 28.08: контраст 1.1:1 при минимуме 2:1), а увести подпись с
                # лица здесь некуда — кадр занят целиком. Лучше показать спикера.
                print("  ! отсев по полосе подписи: «%s» (%.1f-%.1f) — в клипе нет "
                      "тёмного куска без лица, оставляю спикера"
                      % (b.get("visual_query", ""), b["t0"], b["t1"]))
                continue
            cw, ch = int(c.get("w") or 0), int(c.get("h") or 0)
            # реестр разрешений выбранных клипов — для checks.verify_talk (по cand); w/h
            # кладём и в insert, чтобы meta['broll'] мог нести их напрямую (задача 1).
            USED_CLIP_RES[c["id"]] = (cw, ch)
            if template is not None:
                us = upscale_factor(ch, area_h)
                if us is not None:
                    _STATS["used_scales"].append(us)
            inserts.append({"t0": b["t0"], "t1": b["t1"], "src": src, "seg": seg,
                            "query": b["visual_query"], "cand": c["id"],
                            "clip_score": c.get("clip_score"), "w": cw, "h": ch})
            _placed = True
            break
        if not _placed:
            continue
        print("  + [%4.1f-%4.1f] в%d «%s» -> %s (%ds, %dx%d)%s"
              % (b["t0"], b["t1"], v, b["visual_query"], c["id"],
                 c["duration"], c["w"], c["h"],
                 ("  clip=%.3f" % c["clip_score"]) if c.get("clip_score") is not None else ""))
    inserts = _cap_inserts(inserts, max_inserts)   # потолок из стиля (T7)
    if lock_out and inserts:
        save_lock(inserts, lock_out)
        print("  lock: подбор заморожен в %s" % lock_out)
    return inserts


def main():
    # быстрый тест адаптера: один запрос → кандидаты
    import sys, json
    q = sys.argv[1] if len(sys.argv) > 1 else "business conference stage"
    src = PixabaySource()
    res = src.search(q, dur=5.0)
    print("по «%s» найдено %d кандидатов:" % (q, len(res)))
    for c in res[:8]:
        print("  %s  %ds  %dx%d  теги: %s"
              % (c["id"], c["duration"], c["w"], c["h"], c["tags"][:60]))
    if res:
        c = pick(res, 5.0)
        print("\nвыбран:", c["id"], "-> качаю...")
        print("скачан:", download_clip(c))


if __name__ == "__main__":
    raise SystemExit(main())
