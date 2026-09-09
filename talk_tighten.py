# -*- coding: utf-8 -*-
"""R13: TIGHTEN — вырезка пауз речи (jump-cut) ДО остального конвейера.

AUDIT фактор 4: ни одна стадия не выкидывает время — ролик равен сырому дублю,
спикер «думает/дышит» в кадре, темп ниже мировой практики. Тайминги пауз УЖЕ
лежат в transcript.json: зазор между `w.e` слова и `w.s` следующего.

    video + transcript.json → <name>_tight.mp4 + <name>_tight_transcript.json

МЕХАНИКА:
  1. Все слова транскрипта — в одну ленту; зазоры > `gap` (дефолт 0.55 c) идут
     в edit-list. Из каждого вырезаемого зазора ОСТАВЛЯЕМ `keep` секунд
     (пополам с обеих сторон): склейка без воздуха вовсе жуёт концы слов.
     Голова (тишина до первого слова) и хвост (после последнего) режутся тем
     же правилом.
  2. ffmpeg ОДНИМ проходом: trim/atrim по точным таймкодам + concat-фильтр +
     один libx264/aac. Звук режется до сэмпла, видео до кадра — стыки не
     квантуются в целые сегменты, шкала транскрипта не разъезжается с роликом.
  3. transcript пересчитывается в новую шкалу: каждый s/e/end сдвигается на
     сумму вырезанного ДО него. Формат тот же (список сегментов {end, words}).

ЗАПУСК:
    python talk_tighten.py --video video/IMG_3919.MOV \
        --transcript out_talk/IMG_3919_transcript.json \
        --out out_talk/IMG_3919_tight.mp4 [--gap 0.55] [--keep 0.25]

Правило завода «замер, не вывод»: печатает длительность до/после, число
вырезов и гистограмму пауз, а не «готово».

ПРЕДОХРАНИТЕЛЬ: blocks.json, собранный по СЫРОМУ транскрипту, к ужатому ролику
не подходит (шкала сместилась) — блоки строить по <name>_tight_transcript.json.

W1 (СЛОВА-ПАРАЗИТЫ, --fillers, ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНО):
второй источник кусков «на вырез» — не зазор, а само слово: «эээ», «ну», «вот»,
«как бы», «типа», «короче», «в общем», «значит». Механика та же (куски уезжают
в общий edit-list, один проход ffmpeg), меняется только то, ЧТО считать куском.
Правило отбора и словарь — в talk_fillers.json, см. filler_hits().

W2 (ТИШИНА ПО ЗВУКУ, ИСТОЧНИК ПАУЗ ПО УМОЛЧАНИЮ; выключается --no-silence):
третий источник — и он же ЧИНИТ пункт 1. Замер на живом материале: паузный
путь R13 не режет НИЧЕГО (макс. зазор в транскрипте 0.22-0.48 c при пороге
0.55), потому что форс-выравнивание whisperx растягивает границы слова до
соседнего и прячет паузу ВНУТРЬ слова. Прямой замер по звуку: тишины 40.0 c =
17.4% хронометража, и 96% её лежит внутри границ слов — по транскрипту она
невидима в принципе.

Поэтому режим не «ещё один порог», а другой ПОРЯДОК действий:
  1. speech_envelope() — огибающая громкости, ОДИН проход ffmpeg (astats);
  2. silence_threshold() — порог из перцентилей самой записи (уровни записей
     в выборке разошлись на 21 дБ, абсолютный порог негоден);
  3. silence_runs() — отрезки тишины длиннее min_sil;
  4. snap_words() — ПОДРЕЗКА границ слов по звуку: край слова возвращается к
     первому/последнему звучащему кадру внутри его же границ. После этого
     пауза лежит МЕЖДУ словами, где её и ждёт вся готовая механика R13, а
     dropped_words больше не может выбросить звучащее слово;
  5. silence_cuts() — куски на вырез с АСИММЕТРИЧНЫМ воздухом (приём
     auto-editor, Unlicense): перед следующим словом воздуха мало, после
     законченной мысли много.
Пороги — в talk_silence.json, там же обоснование каждого числа.
"""
import argparse
import json
import os
import re
import subprocess

import imageio_ffmpeg

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_GAP = 0.55     # зазор длиннее — пауза, режем (AUDIT: тишина >0.4-0.6 c)
DEFAULT_KEEP = 0.25    # сколько воздуха оставить от вырезанной паузы (всего)
MIN_SEG = 0.20         # сегмент короче — артефакт таймингов, не клеим

FILLERS_JSON = os.path.join(HERE, "talk_fillers.json")
SILENCE_JSON = os.path.join(HERE, "talk_silence.json")


def _run(args):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg упал:\n" + (r.stderr or "")[-2000:])
    return r


def probe(video):
    """(длительность, есть_ли_звук) по ffmpeg -i (ffprobe на машине завода
    нет — правило HANDOFF: бинарь берём у imageio_ffmpeg)."""
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run([ff, "-i", video, "-hide_banner"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    dur, has_audio = None, False
    for line in (r.stderr or "").splitlines():
        if "Duration:" in line:
            hms = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = hms.split(":")
            dur = int(h) * 3600 + int(m) * 60 + float(s)
        if "Audio:" in line:
            has_audio = True
    if dur is None:
        raise RuntimeError("не смог прочитать длительность: %s" % video)
    return dur, has_audio


def probe_duration(video):
    """Длительность файла, сек (обёртка probe для замеров)."""
    return probe(video)[0]


def probe_fps(video):
    """Частота кадров, к/с (None — не разобрали). Нужна ровно для одного:
    выровнять стыки по сетке кадров, см. _snap_fps."""
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run([ff, "-i", video, "-hide_banner"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    for line in (r.stderr or "").splitlines():
        if "Video:" in line:
            m = re.search(r"([\d.]+)\s+fps", line)
            if m:
                try:
                    v = float(m.group(1))
                except ValueError:
                    return None
                return v if 1.0 <= v <= 480.0 else None
    return None


def _snap_fps(t, fps, mode="round"):
    """Таймкод → граница кадра (round / вверх «up» / вниз «down»).

    ЗАЧЕМ. `cut_video` режет видео фильтром trim, а звук — atrim. atrim
    сэмплово точен, а trim обязан отдать ЦЕЛЫЕ кадры и потому округляет край
    сегмента до сетки кадров. concat дальше выравнивает дорожки по видео, и в
    ролик возвращается кусочек звука, которого в нашей арифметике нет.

    На одном-двух вырезах это незаметно (R13 инертен, W1 делал 1-4 выреза), а
    на семнадцати — накапливается. ЗАМЕР: расхождение субтитра с голосом росло
    ЛИНЕЙНО по числу склеек, примерно -5 мс на склейку: t414 -92 мс после 18
    склеек, t381 -93 мс после 17, IMG_8138 -61 мс после 16 (при нуле на первом
    слове). Это не ошибка пересчёта шкалы — арифметика верна, врал сам монтаж.

    Если оба края выреза лежат НА сетке кадров, trim и atrim дают одинаковую
    длительность, и накопление исчезает по построению."""
    if not fps or fps <= 0:
        return t
    n = t * fps
    if mode == "up":                       # край выреза внутрь паузы
        n = -int(-n // 1)
    elif mode == "down":
        n = int(n // 1)
    else:
        n = round(n)
    return round(n / fps, 6)


def flat_words(transcript):
    """Транскрипт (список сегментов {end,words:[{w,s,e}]}) → одна лента слов."""
    words = [w for seg in transcript for w in seg.get("words", [])]
    words.sort(key=lambda w: w["s"])
    return words


def find_pauses(words, dur):
    """Все зазоры ленты: (t0, t1, kind), kind ∈ head/word/tail. Замер полный —
    гистограмма считается по ВСЕМ паузам, режутся только длинные."""
    pauses = []
    if words:
        if words[0]["s"] > 0:
            pauses.append((0.0, words[0]["s"], "head"))
        for a, b in zip(words, words[1:]):
            if b["s"] > a["e"]:
                pauses.append((a["e"], b["s"], "word"))
        if dur > words[-1]["e"]:
            pauses.append((words[-1]["e"], dur, "tail"))
    return pauses


# --------------------------------------------------------------------------
# W1: СЛОВА-ПАРАЗИТЫ. Второй источник кусков «на вырез» — само слово.
# --------------------------------------------------------------------------
# ПОЧЕМУ НЕ ПРОСТО СПИСОК СЛОВ. Русские паразиты поштучно совпадают со
# значимыми словами: «ВОТ такой результат», «НУ и что дальше», «это ЗНАЧИТ
# провал», «что-то ТИПА того». Словарь без контекста портит речь сильнее
# самих паразитов, поэтому решают ТРИ независимых сигнала:
#
#   1. ФОРМА (talk_fillers.json) — необходимое условие, но НЕ достаточное.
#   2. ОБОСОБЛЕННОСТЬ. Паразит — ПАРЕНТЕЗА: он не встроен в синтаксис фразы, а
#      «висит» на её краю. Два наблюдаемых источника:
#        · ПУНКТУАЦИЯ whisper (главный). Её ставит языковая модель по своим
#          соображениям, НЕЗАВИСИМО от наших таймингов, и она разделяет случаи
#          грамматически, а не статистически: указательное «вот» —
#          ПРОКЛИТИКА, прирастает к следующему слову, запятая между ними
#          невозможна («вот такой», «вот это», «вот здесь»); вводное «вот» —
#          парентеза, и правила требуют запятую («просто вот, захотел»).
#          Замер на живой речи (журнал W1-fillers, шаг 2): собственная запятая
#          у слова разделила 9 вхождений «вот» без единой ошибки.
#        · ПАУЗЫ (запасной путь). ВНИМАНИЕ: на нашем транскрипте они почти
#          мертвы — форс-выравнивание wav2vec2 тянет слово до соседа, замер дал
#          зазоры 0.02-0.08 c во ВСЕХ 11 вхождениях. Правило «слово, окружённое
#          паузами» само по себе не сработало бы ни разу. Держим для чужого
#          транскрипта/другого ASR, но опираться на него нельзя.
#   3. УВЕРЕННОСТЬ (поле sc, whisperx.align) — сигнал, независимый от словаря,
#      но НЕ основание для выреза: у указательного «вот» в «Сегодня вот такие»
#      sc=0.01 при 0.06 c, то есть формула «низкая уверенность + короткое =
#      запинка» на служебных словах даёт ложняк. Поэтому основная его роль —
#      ВЕТО: sc >= conf_veto_score при длительности >= conf_veto_dur = слово
#      тянули с нажимом, на нём смысл, вырез отменяется. Обратный ход
#      («промямлено» смягчает требования) оставлен, но только вместе с
#      контекстным признаком. Детектор запинок БЕЗ словаря — отдельный
#      выключатель --fillers-stutter, по умолчанию выключен.
#   4. ЧЁРНЫЙ СПИСОК СОСЕДА (block_next/block_prev) — грубый, но точный
#      предохранитель на заведомо значимые сочетания: «вот ТАКОЙ», «вот ЭТО»,
#      «ну И что», «ЭТО значит». Дешевле любого морфоанализа и не тянет
#      зависимостей (клиент — Windows без GPU, офлайн).
#
# Сколько ворот обязано сойтись — задаёт класс записи (sound/hedge/particle),
# см. "_classes" в talk_fillers.json. Ошибка в сторону «не вырезал» стоит
# ничего, ошибка «вырезал значимое» ломает смысл — пороги смещены в первую.

_PUNCT = " \t\n\r.,!?;:—–-«»\"'()[]…"
_PHRASE_END = ".!?…:;"
_SET_OFF_END = ",—–-…"


def _norm(token):
    """Токен транскрипта → форма для сравнения со словарём: нижний регистр,
    ё→е, без окаймляющей пунктуации, повтор одной буквы схлопнут до двух
    («эээээ»→«ээ», «мммм»→«мм»). Схлопывание нужно, чтобы словарь не хранил
    десять написаний одного мычания."""
    t = (token or "").strip().lower().replace("ё", "е").strip(_PUNCT)
    out = []
    for ch in t:
        if len(out) >= 2 and out[-1] == ch and out[-2] == ch:
            continue
        out.append(ch)
    return "".join(out)


def _tail(token):
    """Последний непробельный символ токена — сюда whisper вешает пунктуацию."""
    t = (token or "").strip()
    return t[-1] if t else ""


def load_fillers(path=None):
    """talk_fillers.json → (params, {форма: запись}). Отсутствие файла — не
    авария конвейера: механика паразитов опциональна, поэтому кидаем понятную
    ошибку только когда её ЯВНО попросили включить."""
    path = path or FILLERS_JSON
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    params = {k: v for k, v in (raw.get("params") or {}).items()
              if not k.startswith("_")}
    table = {}
    for e in raw.get("entries") or []:
        if not isinstance(e, dict) or all(k.startswith("_") for k in e):
            continue                      # запись-комментарий в файле данных
        form = _norm(e.get("f"))
        if not form or e.get("class") not in ("sound", "hedge", "particle"):
            raise ValueError("битая запись словаря паразитов: %r" % (e,))
        table[form] = {
            "class": e["class"],
            "block_prev": {_norm(x) for x in e.get("block_prev") or ()},
            "block_next": {_norm(x) for x in e.get("block_next") or ()},
            "span": len(form.split()),
        }
    if not table:
        raise ValueError("словарь паразитов пуст: %s" % path)
    return params, table


def _p(params, key, default):
    v = params.get(key, default)
    return float(v) if isinstance(v, (int, float)) else default


def filler_hits(transcript, dur, params, table, stutter=False):
    """Транскрипт → список кандидатов на вырез. Возвращает записи вида
    {i, n, form, cls, text, s, e, sc, reason, ctx} — ПОЛНЫЙ разбор решения,
    чтобы оператор мог посмотреть таблицу глазами, а не верить на слово.

    i — индекс первого слова кандидата в ленте flat_words, n — сколько слов
    съедает (у «как бы», «в общем» — два).

    stutter=True добавляет детектор запинок БЕЗ словаря: короткий обрывок с
    низкой уверенностью выравнивания. Отдельный выключатель — сигнал самый
    рискованный (низкий score бывает и у редкого термина).
    """
    words = flat_words(transcript)
    if not words:
        return []
    first_in_seg = set()
    for seg in transcript:
        ws = seg.get("words") or []
        if ws:
            first_in_seg.add(id(ws[0]))

    g_before = _p(params, "iso_gap_before", 0.18)
    g_after = _p(params, "iso_gap_after", 0.15)
    relax_sc = _p(params, "conf_relax_score", 0.45)
    relax_du = _p(params, "conf_relax_dur", 0.25)
    veto_sc = _p(params, "conf_veto_score", 0.92)
    veto_du = _p(params, "conf_veto_dur", 0.30)
    st_sc = _p(params, "stutter_score", 0.35)
    st_du = _p(params, "stutter_dur", 0.18)
    st_len = int(_p(params, "stutter_max_chars", 4))

    norms = [_norm(w["w"]) for w in words]
    hits, taken = [], set()

    def gap_before(i):
        return words[i]["s"] if i == 0 else words[i]["s"] - words[i - 1]["e"]

    def gap_after(j):
        return (dur if j + 1 >= len(words) else words[j + 1]["s"]) - words[j]["e"]

    # словарные кандидаты: сначала биграммы («как бы»), потом одиночные —
    # иначе «как» и «бы» разберутся поодиночке и биграмма не соберётся
    for span in (2, 1):
        for i in range(len(words) - span + 1):
            if any((i + k) in taken for k in range(span)):
                continue
            form = " ".join(norms[i:i + span])
            ent = table.get(form)
            if not ent or ent["span"] != span:
                continue
            j = i + span - 1
            prev_n = norms[i - 1] if i > 0 else ""
            next_n = norms[j + 1] if j + 1 < len(words) else ""
            if prev_n and prev_n in ent["block_prev"]:
                continue
            if next_n and next_n in ent["block_next"]:
                continue

            gb, ga = gap_before(i), gap_after(j)
            prev_tail = _tail(words[i - 1]["w"]) if i > 0 else ""
            own_tail = _tail(words[j]["w"])
            # ворота обособленности, от сильного к слабому
            own_comma = own_tail in _SET_OFF_END      # «вот,» — парентеза
            starts = (gb >= g_before or id(words[i]) in first_in_seg
                      or prev_tail in _PHRASE_END)    # начало фразы
            left_off = starts or prev_tail in _SET_OFF_END
            right_off = own_comma or ga >= g_after
            wdur = words[j]["e"] - words[i]["s"]
            sc = min([w["sc"] for w in words[i:i + span] if "sc" in w]) \
                if any("sc" in w for w in words[i:i + span]) else None
            mumbled = sc is not None and sc <= relax_sc and wdur <= relax_du
            pressed = sc is not None and sc >= veto_sc and wdur >= veto_du

            cls = ent["class"]
            named = ", ".join(x for x in (
                "начало фразы" if starts else
                ("запятая слева" if left_off else ""),
                "своя запятая" if own_comma else
                ("пауза справа %.2f c" % ga if ga >= g_after else ""),
                "промямлено" if mumbled else "") if x)
            if cls == "sound":
                ok, why = True, "мычание (в русском такого слова нет)"
            elif cls == "hedge":
                # вводное: хватает ОДНОГО признака обособленности — форма уже
                # редко бывает значимой, а block_prev/block_next снимают
                # оставшиеся сочетания («ЭТО значит», «что-то ТИПА того»)
                ok = left_off or right_off
                why = "вводное: " + named
            else:
                # particle («ну», «вот») — самый опасный класс, требуем ЯВНОГО
                # орфографического обособления (своя запятая/тире): именно оно
                # на замере разделило указательное и вводное «вот» без ошибок.
                # Просодический путь (начало фразы + слышимая пауза справа)
                # оставлен для транскриптов без пунктуации, но на нашем ASR
                # почти не срабатывает: зазоры между словами 0.02-0.08 c.
                ok = own_comma or (starts and ga >= g_after) \
                    or (left_off and right_off and mumbled)
                why = "частица: " + named
                if ok and pressed:                 # conf-veto, см. шапку блока
                    ok, why = False, ("veto: sc %.2f при %.2f c — сказано с "
                                      "нажимом, это смысл" % (sc, wdur))
            if not ok:
                continue
            for k in range(span):
                taken.add(i + k)
            hits.append({
                "i": i, "n": span, "form": form, "cls": cls,
                "text": " ".join(w["w"] for w in words[i:i + span]),
                "s": words[i]["s"], "e": words[j]["e"],
                "sc": sc, "gap_before": round(gb, 3), "gap_after": round(ga, 3),
                "reason": why,
                "ctx": "%s ⟦%s⟧ %s" % (
                    " ".join(w["w"] for w in words[max(0, i - 3):i]),
                    " ".join(w["w"] for w in words[i:i + span]),
                    " ".join(w["w"] for w in words[j + 1:j + 4])),
            })

    if stutter:
        for i, w in enumerate(words):
            if i in taken or "sc" not in w:
                continue
            wdur = w["e"] - w["s"]
            if w["sc"] > st_sc or wdur > st_du or len(_norm(w["w"])) > st_len:
                continue
            if not (gap_before(i) >= g_before or gap_after(i) >= g_after):
                continue
            taken.add(i)
            hits.append({
                "i": i, "n": 1, "form": _norm(w["w"]), "cls": "stutter",
                "text": w["w"], "s": w["s"], "e": w["e"], "sc": w["sc"],
                "gap_before": round(gap_before(i), 3),
                "gap_after": round(gap_after(i), 3),
                "reason": "запинка: уверенность %.2f, длительность %.02f c"
                          % (w["sc"], wdur),
                "ctx": "%s ⟦%s⟧ %s" % (
                    " ".join(x["w"] for x in words[max(0, i - 3):i]), w["w"],
                    " ".join(x["w"] for x in words[i + 1:i + 4])),
            })

    hits.sort(key=lambda h: h["i"])
    return hits


def filler_cuts(words, dur, hits, keep=DEFAULT_KEEP):
    """Кандидаты → куски исходника на вырез. Режем СЛОВО ВМЕСТЕ С ЗАПИНОЧНЫМ
    ВОЗДУХОМ вокруг него (иначе на месте «эээ» останется дырка тишины), но
    границу зажимаем соседними словами: оставляем keep/2 у каждого края и
    никогда не заходим на соседнее слово."""
    cuts = []
    for h in hits:
        i, j = h["i"], h["i"] + h["n"] - 1
        pe = words[i - 1]["e"] if i > 0 else 0.0
        ns = words[j + 1]["s"] if j + 1 < len(words) else dur
        c0 = min(words[i]["s"], pe + keep / 2.0)
        c1 = max(words[j]["e"], ns - keep / 2.0)
        if c1 - c0 > 1e-3:
            cuts.append((c0, c1))
    return cuts


# --------------------------------------------------------------------------
# W2: ТИШИНА ПО ЗВУКУ. Третий источник кусков — и починка первого.
# --------------------------------------------------------------------------
# ЗАМЕР, С КОТОРОГО ВСЁ НАЧАЛОСЬ (журнал W2-silence, шаги 1-2; 6 роликов,
# 229.4 c живой речи):
#
#   · тишины в ЗВУКЕ                          40.0 c = 17.4% хронометража
#   · из неё ВНЕ границ слов транскрипта       1.5 c =  0.7%
#   · макс. зазор МЕЖДУ словами в транскрипте  0.22-0.48 c при пороге 0.55
#
# То есть 96% тишины whisperx прячет ВНУТРЬ границ слова (форс-выравнивание
# wav2vec2 тянет слово до соседа), и паузный путь R13 видит ноль. Два
# «осторожных» решения тут не работают ОБА:
#   — резать только между словами → 0.7% вместо 17%;
#   — оставить правило dropped_words «слово, чья середина в вырезе, выброшено»
#     → на растянутом слове оно сработает ЛОЖНО: слово звучит, а субтитр его
#     выкинет. Это ровно риск «субтитры разъедутся с голосом».
#
# Поэтому корень чинится РАНЬШЕ вырезки — подрезкой границ слов по звуку
# (snap_words). После неё пауза лежит между словами, и дальше работает вся
# готовая, оттестированная механика R13 без единой поблажки.
#
# ПОЧЕМУ ASTATS, А НЕ SILENCEDETECT. silencedetect требует порог ЗАРАНЕЕ, а
# порога, годного для всех записей, не существует: в выборке пик громкости
# гуляет от -1.3 до -15.9 dBFS, средний уровень — от -16.4 до -37.1 дБ. Замер
# трёх якорей (пик / средний / EBU R128) показал разброс доли тишины 0-84% при
# одном и том же смещении. astats отдаёт ОГИБАЮЩУЮ за тот же один проход, и
# порог считается из самой записи.

_DB_FLOOR = -91.0      # тише этого мерить нечего (16 бит ≈ -96 dBFS)
_PCM_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-inf)")


def load_silence(path=None):
    """talk_silence.json → params. Файл данных, а не код: пороги правятся без
    правки движка и подменяются ключом --silence-config."""
    path = path or SILENCE_JSON
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    params = {k: v for k, v in (raw.get("params") or {}).items()
              if not k.startswith("_")}
    if not params:
        raise ValueError("в файле порогов тишины нет params: %s" % path)
    return params


def speech_envelope(video, win=0.02, sr=16000):
    """Огибающая громкости: RMS каждого окна `win` секунд, в дБ. ОДИН проход
    ffmpeg, ничего кроме ffmpeg не нужно.

    Цепочка: aresample к моно 16 кГц (замер не должен зависеть от того, в чём
    снимали) → asetnsamples режет поток на кадры РОВНО по win → astats считает
    каждый кадр отдельно (reset=1) → ametadata печатает RMS_level в stdout.

    Полная тишина приходит как «-inf» — подменяем на _DB_FLOOR, иначе любая
    арифметика по перцентилям взрывается."""
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    n = max(1, int(round(sr * win)))
    af = ("aresample=%d,aformat=sample_fmts=fltp:channel_layouts=mono,"
          "asetnsamples=n=%d:p=0,astats=metadata=1:reset=1,"
          "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
          % (sr, n))
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-i", video,
                        "-map", "0:a:0", "-af", af, "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg не снял огибающую громкости:\n"
                           + (r.stderr or "")[-2000:])
    out = []
    for m in _PCM_RE.finditer(r.stdout or ""):
        t = m.group(1)
        out.append(_DB_FLOOR if t == "-inf" else max(_DB_FLOOR, float(t)))
    if not out:
        raise RuntimeError("огибающая пуста — в %s нет звуковой дорожки?"
                           % video)
    return out


def _pct(sorted_vals, q):
    """Перцентиль по уже отсортированному списку (без numpy: движок считает
    это на клиентской Windows-машине, лишних зависимостей туда не вводим)."""
    if not sorted_vals:
        return _DB_FLOOR
    i = int(q * (len(sorted_vals) - 1))
    return sorted_vals[max(0, min(len(sorted_vals) - 1, i))]


def silence_threshold(levels, params):
    """Огибающая → (порог, уровень речи, пол шума) в дБ.

        речь  = p90 огибающей        пол = p05 огибающей
        порог = min(речь + cap, max(пол + margin, речь + rel))

    Три числа, и каждое закрывает свой отказ (замер, журнал шаг 1):
      · «речь + rel» (-22 дБ) — рабочий случай, привязка к ГОЛОСУ, а не к
        записи: уровни записей разошлись на 21 дБ, голос относительно себя —
        нет;
      · «пол + margin» (+9 дБ) — шумная комната: у IMG_1883 пол -44.9 дБ, и
        «речь-22» = -43 дБ ушло бы ПОД шум, детектор не нашёл бы ничего;
      · «речь + cap» (-12 дБ) — потолок: не даёт порогу залезть в голос на
        тихой записи в шумной комнате.
    p90/p05, а не максимум/минимум: максимум ловит одиночный стук по микрофону
    (у IMG_1883 пик -1.3 дБ при среднем -26.4 — один выброс на 25 дБ)."""
    s = sorted(levels)
    speech = _pct(s, _p(params, "speech_pct", 0.90))
    floor = _pct(s, _p(params, "floor_pct", 0.05))
    thr = max(floor + _p(params, "floor_margin_db", 9.0),
              speech + _p(params, "rel_speech_db", -22.0))
    thr = min(thr, speech + _p(params, "cap_speech_db", -12.0))
    return thr, speech, floor


def silence_runs(levels, win, thr, min_sil, bridge=0.08, dur=None):
    """Огибающая → отрезки тишины [(t0, t1), ...] длиннее min_sil.

    Порог САМ ПО СЕБЕ речь от паузы не отделяет: распределение уровней сплошное,
    внутри слова полно тихих кадров (смычки п/т/к держатся 40-80 мс). Отделяет
    порог ВМЕСТЕ с min_sil — поэтому оба параметра обязаны настраиваться парой.

    bridge: всплеск громкости короче него не разрывает паузу. Иначе вдох или
    щелчок губами посреди паузы делит её надвое, и вместо одного выреза выходят
    два коротких, каждый со своим воздухом. Порог bridge держим заведомо меньше
    самого короткого слога — потерянное слово так не проскочит, а если громкий
    кусок ДЛИННЕЕ bridge, пауза честно делится и этот кусок остаётся в ролике."""
    out, i, n = [], 0, len(levels)
    nb = max(0, int(round(bridge / win)))
    while i < n:
        if levels[i] >= thr:
            i += 1
            continue
        j = i
        while j < n:
            if levels[j] < thr:
                j += 1
                continue
            k = j
            while k < n and levels[k] >= thr:
                k += 1
            if k < n and (k - j) <= nb:
                j = k                     # короткий всплеск — паузу не рвёт
                continue
            break
        if (j - i) * win >= min_sil:
            t0, t1 = i * win, j * win
            if dur is not None:
                t0, t1 = min(t0, dur), min(t1, dur)
            if t1 - t0 > 1e-6:
                out.append((round(t0, 3), round(t1, 3)))
        i = max(j, i + 1)
    return out


def snap_words(transcript, runs, min_word=0.04, tol=0.05):
    """ПОДРЕЗКА границ слов по звуку. Возвращает (новый транскрипт, замер).

    Двигаются ТОЛЬКО КРАЯ и только внутрь: если начало слова накрыто тишиной —
    `s` уезжает к концу этой тишины; если накрыт конец — `e` уезжает к её
    началу. Тишина СТРОГО ВНУТРИ слова (звук есть и до неё, и после) не
    трогается и потом не режется никогда: так подрезка физически не может
    выбросить звучащий слог, а вырезка — залезть в середину слова.

    ЗАЧЕМ ДОПУСК `tol`. Первый замер дал 1.5 c вырезки вместо ожидаемых 4-5:
    оказалось, что граница слова у whisperx перелезает за конец тишины на
    10-20 мс («предпринимателя» 9.69-11.33 при тишине 10.16-11.32), и без
    допуска такая пауза считается «строго внутри слова» и не режется — 4.09 c
    из 10.41 уходили в никуда именно так. Допуск 0.05 c — это 2.5 окна замера,
    и он заведомо КОРОЧЕ самого короткого русского слога (0.09-0.12 c): принять
    звучащий слог за подрезаемый припуск на таком допуске нельзя.

    Слово, целиком накрытое тишиной (whisperx выровнял в пустоту либо слово
    сказано тише порога), остаётся КАК БЫЛО и считается отдельно: двигать его
    некуда, а молча портить тайминги субтитра нельзя. Заодно оно ЗАЩИЩАЕТ своё
    место — silence_cuts клампится по словам и вокруг такого слова не режет.
    Порядок слов и их число не меняются: транскрипт остаётся тем же документом,
    у него только честнее тайминги."""
    stat = {"snapped": 0, "moved_s": 0.0, "all_silent": 0, "words": 0}
    out = []
    for seg in transcript:
        words = []
        for w in seg.get("words", []):
            s0, e0 = float(w["s"]), float(w["e"])
            stat["words"] += 1
            s, e = s0, e0
            for t0, t1 in runs:
                if t1 <= s + tol or t0 >= e - tol:
                    continue                          # паузы у краёв слова нет
                if t0 <= s0 + tol and t1 >= e0 - tol:
                    s, e = s0, e0                     # слово в тишине целиком
                    stat["all_silent"] += 1
                    break
                if t0 <= s + tol:                     # тишина накрыла начало
                    s = min(max(s, t1), e)
                if t1 >= e - tol:                     # тишина накрыла конец
                    e = max(min(e, t0), s)
            if e - s < min_word:                      # не ужимаем в ноль
                s, e = s0, e0
            item = dict(w)
            if abs(s - s0) > 1e-3 or abs(e - e0) > 1e-3:
                stat["snapped"] += 1
                stat["moved_s"] += (s - s0) + (e0 - e)
            item["s"], item["e"] = round(s, 3), round(e, 3)
            words.append(item)
        if words:
            out.append({"end": float(seg["end"]), "words": words})
    stat["moved_s"] = round(stat["moved_s"], 2)
    return out, stat


def silence_cuts(words, dur, runs, params, fps=None):
    """Отрезки тишины → куски исходника на вырез, с АСИММЕТРИЧНЫМ воздухом.

    АСИММЕТРИЯ (приём auto-editor, Unlicense — общественное достояние). Раньше
    `keep` был симметричен: половина воздуха до склейки, половина после. На слух
    это неверно, и вот почему. ПЕРЕД следующим словом воздух нужен только чтобы
    не срезать атаку — слушатель уже настроен, ждать ему нечего. А ПОСЛЕ речи
    хвост в русском длинный (безударный слог, оглушение согласного), и склейка
    сразу за ним слышна как проглоченное окончание. Поэтому air_tail > air_lead.
    Отдельно — конец МЫСЛИ: если предыдущее слово кончилось точкой (пунктуация
    whisper — сигнал, независимый от таймингов; на него же опёрся W1), паузу
    после него слушатель ждёт ДОЛЬШЕ, и воздуха оставляется ещё больше
    (air_tail_phrase). Обрежешь — фраза звучит оборванной.

    Куски КЛАМПЯТСЯ по границам слов: вырез не заходит на слово ни на кадр.
    После snap_words это почти всегда пустая формальность, но инвариант должен
    держаться и на неподрезанном транскрипте — иначе вырез съест речь."""
    air_lead = _p(params, "air_lead", 0.10)
    air_tail = _p(params, "air_tail", 0.22)
    air_ph = _p(params, "air_tail_phrase", 0.34)
    min_cut = _p(params, "min_cut", 0.12)
    cuts = []
    for t0, t1 in runs:
        # 1. клампим по словам: берём самый длинный кусок паузы, свободный
        #    от слов (после подрезки границ он обычно и есть вся пауза)
        a, b, best = t0, t1, (t0, t0)
        for w in words:
            ws, we = float(w["s"]), float(w["e"])
            if we <= a or ws >= b:
                continue
            if ws - a > best[1] - best[0]:
                best = (a, ws)
            a = max(a, we)
        if b - a > best[1] - best[0]:
            best = (a, b)
        a, b = best
        if b - a <= 1e-6:
            continue
        # 2. воздух: слева — сколько ждёт ухо после сказанного, справа —
        #    сколько нужно, чтобы не срезать атаку следующего слова
        head = a <= 1e-3                      # тишина в самом начале файла
        tail = b >= dur - 1e-3                # …и в самом конце
        kt = 0.0 if head else air_tail
        if not head:
            prev = [w for w in words if float(w["e"]) <= a + 1e-6]
            if prev and _tail(prev[-1]["w"]) in _PHRASE_END:
                kt = air_ph
        c0 = a + kt
        c1 = b - (0.0 if tail else air_lead)
        # 3. на сетку кадров — иначе стык уносит ~5 мс на склейку, и на
        #    семнадцати склейках субтитр уезжает от голоса на 90 мс (см. _snap_fps)
        if fps:
            # округление к БЛИЖАЙШЕМУ кадру, а не внутрь: внутрь — это до
            # кадра с каждой стороны, на 80 склейках замер потерял 3.9 c из
            # 24.7 (10.8% -> 9.0%) на ровном месте. К ближайшему смещение не
            # больше полкадра (17 мс при 30 к/с), а воздуха у нас минимум
            # 50 мс — то есть вырез гарантированно остаётся внутри паузы.
            # Страховка на случай нестандартной частоты кадров — явный зажим.
            c0 = min(max(_snap_fps(c0, fps), a), b)
            c1 = max(min(_snap_fps(c1, fps), b), a)
        if c1 - c0 >= min_cut:
            cuts.append((round(c0, 6), round(c1, 6)))
    return cuts


def _merge_cuts(cuts):
    """Куски из РАЗНЫХ источников (паузы + паразиты) — в один непересекающийся
    отсортированный список. Без этого партиция [0,dur] в edit_list получит
    нахлёст и шкала транскрипта разъедется с роликом."""
    out = []
    for c0, c1 in sorted((tuple(c) for c in cuts), key=lambda c: c[0]):
        if out and c0 <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], c1)
        else:
            out.append([c0, c1])
    return [(a, b) for a, b in out]


def edit_list(words, dur, gap=DEFAULT_GAP, keep=DEFAULT_KEEP, extra_cuts=(),
              gaps=True):
    """Лента слов → (keeps, cuts). keeps — сегменты исходника, которые остаются
    (склейка встык); cuts — что вырезано. Из зазора > gap оставляем keep секунд
    воздуха (по keep/2 у каждого края; голове/хвосту — keep/2 с одной стороны).

    extra_cuts — куски от ДРУГОГО источника (W1: слова-паразиты, filler_cuts;
    W2: тишина по звуку, silence_cuts). Они вливаются в общий список ДО
    партиции, поэтому все инварианты стадии (keeps+cuts покрывают [0,dur] без
    дыр, микро-сегменты поглощаются вырезом) держатся сами собой, а ffmpeg
    по-прежнему делает ОДИН проход.

    gaps=False ОТКЛЮЧАЕТ паузный путь по зазорам транскрипта. Нужно ровно в
    режиме W2: после подрезки границ слов зазор транскрипта и отрезок тишины —
    это ОДНО И ТО ЖЕ место, но зазорный путь оставляет воздух СИММЕТРИЧНО
    (keep/2 с каждой стороны), а звуковой — асимметрично. Слитые вместе, они
    дают объединение, то есть более агрессивный край с каждой стороны, и вся
    асимметрия пропадает. Поэтому в W2 источник ЗАМЕЩАЕТСЯ, а не добавляется."""
    if gap <= 0:
        raise ValueError("--gap должен быть > 0")
    if keep < 0 or keep >= gap:
        raise ValueError("--keep должен быть в [0, gap)")
    cuts = []
    for t0, t1, kind in (find_pauses(words, dur) if gaps else ()):
        d = t1 - t0
        if d <= gap:
            continue
        if kind == "head":
            c0, c1 = t0, t1 - keep / 2.0          # воздух только перед словом
        elif kind == "tail":
            c0, c1 = t0 + keep / 2.0, t1          # воздух только после слова
        else:
            c0, c1 = t0 + keep / 2.0, t1 - keep / 2.0
        if c1 - c0 > 1e-3:
            cuts.append((c0, c1))
    cuts = _merge_cuts(list(cuts) + [tuple(c) for c in extra_cuts])

    # keeps и итоговые cuts строим ВМЕСТЕ: сегмент короче MIN_SEG не клеим, а
    # ПОГЛОЩАЕМ соседним вырезом — иначе видео его теряет, а shift_transcript
    # о нём не знает, и шкала ужатого транскрипта уезжает на MIN_SEG за стык.
    keeps, real, pos = [], [], 0.0
    for c0, c1 in cuts:
        if c0 - pos > MIN_SEG:
            keeps.append((pos, c0))
            real.append([c0, c1])
        elif real:
            real[-1][1] = c1                  # микро-сегмент — в прошлый вырез
        else:
            real.append([pos, c1])            # микро-голова — в первый вырез
        pos = c1
    if dur - pos > MIN_SEG:
        keeps.append((pos, dur))
    elif dur > pos and real:
        real[-1][1] = dur                     # микро-хвост — в последний вырез
    if not keeps:
        raise RuntimeError("edit-list пуст — порог %.2f вырезает всё" % gap)
    return keeps, [tuple(c) for c in real]


def dropped_words(transcript, cuts):
    """Слова, чья СЕРЕДИНА попала внутрь выреза, — их в ролике больше нет.

    Раньше таких не было по построению (резали только зазоры МЕЖДУ словами).
    Теперь их два источника: (а) W1 — вырезанный паразит; (б) микро-сегмент
    короче MIN_SEG, поглощённый соседним вырезом в edit_list, — эта дырка была
    и до W1, просто слово молча оставалось в транскрипте и разъезжалось с
    голосом. Считаем по середине, а не по краю: у слова на границе выреза
    съеден только хвост призвука, само слово в ролике осталось."""
    out = set()
    for seg in transcript:
        for w in seg.get("words", []):
            mid = (float(w["s"]) + float(w["e"])) / 2.0
            for c0, c1 in cuts:
                if c0 < mid < c1:
                    out.add(id(w))
                    break
                if c0 >= mid:
                    break
    return out


def shift_transcript(transcript, cuts):
    """Пересчёт транскрипта в шкалу ужатого ролика: каждый таймкод сдвигается
    на сумму вырезанного ДО него. Слова, которых в ролике больше нет
    (dropped_words), выбрасываются — иначе субтитр покажет вырезанное слово и
    караоке уедет от голоса. Остальные слова лежат вне вырезов, для них сдвиг —
    чистое вычитание. Поле sc (уверенность) переносится как есть, если было:
    транскрипт ужатого ролика должен нести те же признаки, что и сырой."""
    def remap(t):
        removed = 0.0
        for c0, c1 in cuts:
            if c1 <= t:
                removed += c1 - c0
            elif c0 < t:                 # таймкод внутри выреза (край keep-зоны)
                removed += t - c0
            else:
                break
        return round(t - removed, 3)

    drop = dropped_words(transcript, cuts)
    out = []
    for seg in transcript:
        words = []
        for w in seg.get("words", []):
            if id(w) in drop:
                continue
            item = {"w": w["w"], "s": remap(w["s"]), "e": remap(w["e"])}
            if "sc" in w:
                item["sc"] = w["sc"]
            words.append(item)
        if words:
            out.append({"end": remap(float(seg["end"])), "words": words})
    return out


def cut_video(video, keeps, out):
    """Сегменты исходника → один mp4 ОДНИМ проходом: trim/atrim по точным
    таймкодам + concat-фильтр + один общий libx264/aac. Почему не «нарезать
    сегменты и склеить конкат-демиксером»: там каждый сегмент квантуется до
    целых кадров и AAC-фреймов, стык уносит ~60 мс, и на 10+ вырезах шкала
    транскрипта разъезжается с роликом (караоке мимо губ). Здесь звук режется
    с точностью до сэмпла, видео — до кадра; временных файлов нет."""
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    dur, has_audio = probe(video)
    parts, pads = [], []
    for i, (s, e) in enumerate(keeps):
        parts.append("[0:v]trim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[v%d]"
                     % (s, e, i))
        if has_audio:
            parts.append("[0:a]atrim=start=%.3f:end=%.3f,"
                         "asetpts=PTS-STARTPTS[a%d]" % (s, e, i))
            pads.append("[v%d][a%d]" % (i, i))
        else:
            pads.append("[v%d]" % i)
    parts.append("%sconcat=n=%d:v=1:a=%d[v]%s"
                 % ("".join(pads), len(keeps), 1 if has_audio else 0,
                    "[a]" if has_audio else ""))
    args = [ff, "-y", "-loglevel", "error", "-i", video,
            "-filter_complex", ";".join(parts), "-map", "[v]"]
    if has_audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    args += ["-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
             "-crf", "18", "-preset", "medium",
             "-movflags", "+faststart", out]
    _run(args)
    return out


def pause_histogram(pauses, edges=(0.2, 0.4, 0.55, 0.8, 1.2, 2.0)):
    """Гистограмма длительностей пауз по корзинам (замер, не вывод)."""
    lens = [t1 - t0 for t0, t1, _ in pauses]
    bins = []
    lo = 0.0
    for hi in edges:
        bins.append(("%.2f–%.2f" % (lo, hi),
                     sum(1 for d in lens if lo < d <= hi)))
        lo = hi
    bins.append((">%.2f" % lo, sum(1 for d in lens if d > lo)))
    return bins


def plan_cuts(transcript, dur, gap=DEFAULT_GAP, keep=DEFAULT_KEEP,
              fillers=False, fillers_config=None, fillers_stutter=False,
              runs=None, silence_params=None, fps=None):
    """Транскрипт + длительность → (keeps, cuts, hits, warn). Чистая логика без
    ffmpeg — отдельно от tighten(), чтобы отбор можно было ПОСМОТРЕТЬ
    (--fillers-preview / --silence-preview) и протестировать без видео.

    runs — отрезки тишины, УЖЕ снятые со звука (W2). Их меряет tighten(), сюда
    они приходят готовым списком: так вся логика отбора остаётся чистой и
    тестируется без ffmpeg, ровно как отбор паразитов. runs=None — режим W2
    выключен, поведение прежнее.

    ВАЖНО: transcript здесь ожидается УЖЕ ПОДРЕЗАННЫЙ (snap_words), если runs
    заданы. Подрезка — не деталь вырезки, а починка таймингов транскрипта, её
    результат обязан попасть и в выходной файл субтитров, поэтому она живёт
    в tighten(), а не прячется внутри планировщика."""
    words = flat_words(transcript)
    hits, warn = [], []
    extra = []
    if runs is not None:
        sp = silence_params or load_silence()
        extra += silence_cuts(words, dur, runs, sp, fps=fps)
        share = sum(c1 - c0 for c0, c1 in extra) / max(dur, 1e-6)
        lim = _p(sp, "warn_share", 0.35)
        if share > lim:
            warn.append("тишиной помечено %.0f%% хронометража (порог %.0f%%) — "
                        "послушай результат: либо порог распущен, либо в ролике "
                        "много пауз по монтажу/музыки"
                        % (share * 100, lim * 100))
    if fillers:
        params, table = load_fillers(fillers_config)
        if not any("sc" in w for w in words):
            warn.append("в транскрипте нет поля sc (уверенность) — сигнал "
                        "запинки не работает, решает только контекст. "
                        "Перетранскрибируй talk_transcribe.py.")
        hits = filler_hits(transcript, dur, params, table,
                           stutter=fillers_stutter)
        extra += filler_cuts(words, dur, hits, keep=keep)
        share = len(hits) / float(len(words) or 1)
        lim = _p(params, "warn_share", 0.12)
        if share > lim:
            warn.append("паразитами помечено %.0f%% слов (порог %.0f%%) — "
                        "проверь таблицу глазами, пороги скорее всего "
                        "распущены" % (share * 100, lim * 100))
    keeps, cuts = edit_list(words, dur, gap=gap, keep=keep, extra_cuts=extra,
                            gaps=(runs is None))
    if fillers:
        # честный замер побочки: слово, которого лишился ролик, хотя мы его
        # паразитом НЕ помечали (поглощение микро-сегмента в edit_list)
        planned = {id(w) for h in hits
                   for w in words[h["i"]:h["i"] + h["n"]]}
        collateral = [w["w"] for seg in transcript for w in seg.get("words", [])
                      if id(w) in dropped_words(transcript, cuts)
                      and id(w) not in planned]
        if collateral:
            warn.append("вырез задел слова, которые паразитами не помечались "
                        "(поглощение микро-сегмента): %s"
                        % ", ".join(collateral[:10]))
    return keeps, cuts, hits, warn


def measure_silence(video, params=None, dur=None):
    """Видео → (отрезки тишины, замер). ЕДИНСТВЕННОЕ место W2, которое трогает
    ffmpeg: дальше вся логика чистая и тестируется без видео."""
    params = params or load_silence()
    win = _p(params, "win", 0.02)
    levels = speech_envelope(video, win=win)
    thr, speech, floor = silence_threshold(levels, params)
    runs = silence_runs(levels, win, thr, _p(params, "min_sil", 0.30),
                        bridge=_p(params, "bridge", 0.08), dur=dur)
    total = sum(t1 - t0 for t0, t1 in runs)
    return runs, {
        "thr_db": round(thr, 1), "speech_db": round(speech, 1),
        "floor_db": round(floor, 1), "win": win,
        "frames": len(levels), "runs": len(runs),
        "silence_s": round(total, 2),
        "silence_share": round(100.0 * total / max(len(levels) * win, 1e-6), 1),
    }


def tighten(video, transcript_path, out, gap=DEFAULT_GAP, keep=DEFAULT_KEEP,
            fillers=False, fillers_config=None, fillers_stutter=False,
            silence=None, silence_config=None):
    """Полная стадия: video + transcript → ужатый mp4 + пересчитанный
    transcript. Возвращает dict с замерами (длительности, вырезы, гистограмма).

    fillers=False — ВЫКЛЮЧАТЕЛЬ механики слов-паразитов (W1). По умолчанию
    выключена: стадия работает байт-в-байт как R13, пока оператор не попросит
    иначе. Включённая — добавляет в edit-list куски со словами и печатает
    таблицу вырезанного, чтобы решение можно было проверить глазами.

    silence — механика тишины по ЗВУКУ (W2). Она меняет ПОРЯДОК стадии: сперва
    меряется звук, потом по нему подрезаются границы слов транскрипта, и только
    потом планируется вырезка. Подрезанный транскрипт — не побочный эффект, а
    часть результата: именно его тайминги уезжают в субтитры, и они честнее
    исходных (whisperx растягивает слово до соседнего).

    ТРИ СОСТОЯНИЯ, и по умолчанию None — «АВТО: по звуку, если звук есть».
    E4-54: раньше здесь стоял False, то есть на живом материале стадия молча
    шла зазорным путём и не резала НИЧЕГО. Это не догадка, а замер на этой же
    машине (IMG_8138, 56.97 c, транскрипт whisperx):

        зазорный путь  — вырезано 0.00 c = 0.0%,  0 склеек;
        путь по звуку  — тишины 10.41 c = 18.3%, вырезано 5.60 c = 9.8%,
                         16 склеек, из транскрипта не выброшено ни слова.

    Ноль вырезов — не «осторожная настройка», а неработающий детектор: паузу
    прячет внутрь границ слова само форс-выравнивание, и никакой порог gap её
    оттуда не достанет. Поэтому рабочим по умолчанию стал путь, который на
    замере что-то находит, а зазорный остался явным выбором (silence=False).

    silence=True  — заставить путь по звуку (без звуковой дорожки — ошибка);
    silence=False — заставить старый зазорный путь R13 (замер: ~0% вырезки);
    silence=None  — АВТО (умолчание): по звуку, если дорожка есть; если её
                    нет — зазорный путь, потому что мерить нечего."""
    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)
    words = flat_words(transcript)
    if not words:
        raise RuntimeError("в транскрипте нет слов — резать нечего")

    dur, has_audio = probe(video)
    runs, sil_stat, snap_stat, sil_params, fps = None, {}, {}, None, None
    auto = silence is None
    if auto:                       # АВТО: по звуку, если есть что мерить
        silence = has_audio
    if silence:
        if not has_audio:
            raise RuntimeError("--silence: в ролике нет звуковой дорожки, "
                               "мерить тишину не по чему: %s" % video)
        sil_params = load_silence(silence_config)
        if _p(sil_params, "frame_snap", 1.0) >= 0.5:
            fps = probe_fps(video)
        runs, sil_stat = measure_silence(video, sil_params, dur=dur)
        sil_stat["fps"] = fps
        transcript, snap_stat = snap_words(
            transcript, runs, min_word=_p(sil_params, "snap_min_word", 0.04),
            tol=_p(sil_params, "snap_tol", 0.05))
        words = flat_words(transcript)

    pauses = find_pauses(words, dur)
    keeps, cuts, hits, warn = plan_cuts(
        transcript, dur, gap=gap, keep=keep, fillers=fillers,
        fillers_config=fillers_config, fillers_stutter=fillers_stutter,
        runs=runs, silence_params=sil_params, fps=fps)

    if not silence and not cuts:
        # ровно симптом E4-54: зазорный путь отработал и не нашёл ни одной
        # паузы. Молчать тут нельзя — оператор увидит «стадия прошла» и решит,
        # что пауз в ролике нет, хотя на слух они есть.
        warn.append("зазорный путь не нашёл НИ ОДНОЙ паузы (порог %.2f c): на "
                    "транскрипте whisperx это норма — форс-выравнивание прячет "
                    "паузу внутрь границ слова. Мерь тишину по звуку "
                    "(--silence / silence=True)" % gap)

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    cut_video(video, keeps, out)

    new_tr = shift_transcript(transcript, cuts)
    tr_out = os.path.splitext(out)[0] + "_transcript.json"
    with open(tr_out, "w", encoding="utf-8") as f:
        json.dump(new_tr, f, ensure_ascii=False, indent=2)

    removed = sum(c1 - c0 for c0, c1 in cuts)
    new_dur = probe_duration(out)
    stats = {
        "out": out, "transcript": tr_out,
        "dur_before": round(dur, 2), "dur_after": round(new_dur, 2),
        "removed_s": round(removed, 2), "cuts": len(cuts),
        "removed_pct": round(100.0 * removed / max(dur, 1e-6), 1),
        "gap": gap, "keep": keep,
        "silence": silence, "silence_auto": auto,
        "silence_stat": sil_stat, "snap_stat": snap_stat,
        "dropped_words": len(dropped_words(transcript, cuts)),
        "pauses_total": len(pauses),
        "pause_hist": pause_histogram(pauses),
        "silence_over_04": round(sum((t1 - t0) for t0, t1, _ in pauses
                                     if t1 - t0 > 0.4), 2),
        "fillers": fillers,
        "filler_hits": hits,
        "filler_words": sum(h["n"] for h in hits),
        "filler_s": round(sum(c1 - c0 for c0, c1 in
                              filler_cuts(words, dur, hits, keep=keep)), 2),
        "words_total": len(words),
        "warnings": warn,
    }
    return stats


def filler_table(hits, words_total=None):
    """Таблица вырезанных паразитов — СЛОВО, КОНТЕКСТ, ОБОСНОВАНИЕ. Печатается
    всегда, когда механика включена: правило контекстное и вероятностное, его
    обязан просматривать человек, а не «доверять цифре»."""
    lines = []
    if not hits:
        lines.append("  паразитов не найдено")
        return lines
    tot = sum(h["n"] for h in hits)
    lines.append("  ПАРАЗИТЫ: %d шт. (%d слов%s)"
                 % (len(hits), tot,
                    ", %.1f%% ленты" % (100.0 * tot / words_total)
                    if words_total else ""))
    lines.append("    %-7s %-12s %-9s %-6s %s" %
                 ("t, c", "слово", "класс", "sc", "контекст / обоснование"))
    for h in hits:
        lines.append("    %-7.2f %-12s %-9s %-6s %s"
                     % (h["s"], h["text"][:12], h["cls"],
                        ("%.2f" % h["sc"]) if h["sc"] is not None else "—",
                        h["ctx"]))
        lines.append("    %-7s %-12s %-9s %-6s → %s" % ("", "", "", "",
                                                        h["reason"]))
    return lines


def silence_table(sil, snap):
    """Замер детектора тишины — ПОРОГ И ОТ ЧЕГО ОН ОТСЧИТАН. Порог адаптивный,
    и оператор обязан видеть, какой именно он получился на этой записи: одна
    строка отвечает на вопрос «почему на этом ролике вырезалось столько»."""
    lines = ["  ТИШИНА ПО ЗВУКУ: %.2f c (%.1f%% хронометража), %d отрез(ков)"
             % (sil.get("silence_s", 0.0), sil.get("silence_share", 0.0),
                sil.get("runs", 0))]
    lines.append("    порог %.1f дБ  (речь %.1f дБ, пол шума %.1f дБ, "
                 "окно %.0f мс, кадров %d)"
                 % (sil.get("thr_db", 0.0), sil.get("speech_db", 0.0),
                    sil.get("floor_db", 0.0), 1000 * sil.get("win", 0.02),
                    sil.get("frames", 0)))
    if snap:
        lines.append("    подрезка границ слов: %d из %d (сдвинуто %.2f c"
                     "%s)"
                     % (snap.get("snapped", 0), snap.get("words", 0),
                        snap.get("moved_s", 0.0),
                        ", целиком в тишине %d — оставлены как были"
                        % snap["all_silent"] if snap.get("all_silent") else ""))
    return lines


def report(st):
    """Печать замера — числа, а не «успех»."""
    print("TIGHTEN: %.2f c → %.2f c (вырезано %.2f c = %.1f%%, %d вырез(ов), "
          "порог %.2f c, воздух %.2f c)"
          % (st["dur_before"], st["dur_after"], st["removed_s"],
             st.get("removed_pct", 0.0), st["cuts"], st["gap"], st["keep"]))
    print("  тишина >0.4 c суммарно в исходнике: %.2f c" % st["silence_over_04"])
    print("  гистограмма пауз (%d шт.):" % st["pauses_total"])
    for label, n in st["pause_hist"]:
        print("    %-10s %s %d" % (label + " c", "█" * n, n))
    if st.get("silence"):
        print("  источник пауз: ЗВУК%s"
              % (" (авто: в ролике есть звуковая дорожка)"
                 if st.get("silence_auto") else " (--silence, явно)"))
        for line in silence_table(st.get("silence_stat") or {},
                                  st.get("snap_stat") or {}):
            print(line)
        print("    слов выброшено из транскрипта: %d" % st.get("dropped_words", 0))
    else:
        print("  источник пауз: ЗАЗОРЫ ТРАНСКРИПТА%s — на материале whisperx "
              "это ~0%% вырезки, см. --silence"
              % (" (авто: звуковой дорожки нет, мерить нечего)"
                 if st.get("silence_auto") else " (--no-silence, явно)"))
    if st.get("fillers"):
        print("  паразиты: %d слов, %.2f c (%.1f%% исходника)"
              % (st["filler_words"], st["filler_s"],
                 100.0 * st["filler_s"] / max(st["dur_before"], 1e-6)))
        for line in filler_table(st["filler_hits"], st.get("words_total")):
            print(line)
    else:
        print("  паразиты: механика ВЫКЛЮЧЕНА (--fillers)")
    for w in st.get("warnings") or ():
        print("  ! %s" % w)
    print("  mp4:        %s" % st["out"])
    print("  transcript: %s" % st["transcript"])


def _add_filler_args(ap):
    """Выключатель механики W1. Флаг, а не дефолт: правило контекстное и пока
    не обкатано, поэтому стадия обязана уметь работать ровно как R13."""
    ap.add_argument("--fillers", action="store_true",
                    help="ВКЛЮЧИТЬ вырезку слов-паразитов (эээ/ну/вот/как бы/"
                         "типа/короче/в общем/значит). По умолчанию ВЫКЛЮЧЕНО")
    ap.add_argument("--fillers-config", default=None,
                    help="свой словарь и пороги вместо talk_fillers.json")
    ap.add_argument("--fillers-stutter", action="store_true",
                    help="плюс детектор запинок БЕЗ словаря: короткий обрывок "
                         "с низкой уверенностью распознавания (рискованнее "
                         "словаря — отдельный выключатель)")


def _add_silence_args(ap):
    """Переключатель источника пауз (W2). E4-54: по умолчанию — АВТО, то есть
    ПО ЗВУКУ, когда в ролике есть звуковая дорожка. Раньше умолчанием был
    зазорный путь, и стадия на живом материале не резала ничего (замер:
    0.00 c из 56.97 c против 5.60 c = 9.8% по звуку на том же ролике)."""
    ap.add_argument("--silence", dest="silence", action="store_true",
                    default=None,
                    help="ЗАСТАВИТЬ искать паузы по ЗВУКУ (+ подрезка границ "
                         "слов). Это и так умолчание, когда в ролике есть звук")
    ap.add_argument("--no-silence", dest="silence", action="store_false",
                    help="вернуть старый зазорный путь R13 (паузы по "
                         "transcript.json). ВНИМАНИЕ: на транскрипте whisperx "
                         "он не режет практически ничего")
    ap.add_argument("--silence-config", default=None,
                    help="свои пороги вместо talk_silence.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="R13: вырезка пауз речи (jump-cut) по transcript.json; "
                    "W1: плюс слова-паразиты по --fillers; "
                    "W2: паузы по ЗВУКУ — источник ПО УМОЛЧАНИЮ, старый "
                    "зазорный путь возвращается ключом --no-silence")
    ap.add_argument("--video", required=True, help="исходный клип со спикером")
    ap.add_argument("--transcript", required=True,
                    help="transcript.json СЫРОГО клипа (стадия 2)")
    ap.add_argument("--out", required=True,
                    help="куда писать ужатый mp4 (<name>_tight.mp4); рядом "
                         "ляжет <name>_tight_transcript.json")
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP,
                    help="порог паузы, сек (длиннее — вырезается; "
                         "по умолчанию %.2f)" % DEFAULT_GAP)
    ap.add_argument("--keep", type=float, default=DEFAULT_KEEP,
                    help="сколько воздуха оставить от каждой вырезанной паузы, "
                         "сек (по умолчанию %.2f)" % DEFAULT_KEEP)
    _add_filler_args(ap)
    _add_silence_args(ap)
    ap.add_argument("--fillers-preview", action="store_true",
                    help="только показать таблицу отбора и выйти, ничего не "
                         "резать и не кодировать (сухой прогон)")
    ap.add_argument("--preview", action="store_true",
                    help="сухой прогон: посчитать и напечатать, сколько бы "
                         "вырезалось, ничего не кодируя (работает и с "
                         "--silence, и с --fillers)")
    a = ap.parse_args()

    if a.fillers_preview or a.preview:
        with open(a.transcript, encoding="utf-8") as f:
            _tr = json.load(f)
        _use_fill = a.fillers or a.fillers_preview
        _dur, _has_audio = probe(a.video)
        _sil_on = _has_audio if a.silence is None else a.silence
        _runs, _sil, _snap, _sp = None, {}, {}, None
        _fps = None
        if _sil_on:
            _sp = load_silence(a.silence_config)
            if _p(_sp, "frame_snap", 1.0) >= 0.5:
                _fps = probe_fps(a.video)
            _runs, _sil = measure_silence(a.video, _sp, dur=_dur)
            _tr, _snap = snap_words(_tr, _runs,
                                    min_word=_p(_sp, "snap_min_word", 0.04),
                                    tol=_p(_sp, "snap_tol", 0.05))
        _keeps, _cuts, _hits, _warn = plan_cuts(
            _tr, _dur, gap=a.gap, keep=a.keep, fillers=_use_fill,
            fillers_config=a.fillers_config, fillers_stutter=a.fillers_stutter,
            runs=_runs, silence_params=_sp, fps=_fps)
        _cut_s = sum(c1 - c0 for c0, c1 in _cuts)
        print("PREVIEW: %.2f c → %.2f c (вырезано бы %.2f c, %.1f%%, %d склеек)"
              % (_dur, _dur - _cut_s, _cut_s,
                 100.0 * _cut_s / max(_dur, 1e-6), len(_cuts)))
        if _sil_on:
            for _l in silence_table(_sil, _snap):
                print(_l)
            print("    слов выброшено из транскрипта: %d"
                  % len(dropped_words(_tr, _cuts)))
        if _use_fill:
            for _l in filler_table(_hits, len(flat_words(_tr))):
                print(_l)
        for _w in _warn:
            print("  ! %s" % _w)
        raise SystemExit(0)

    report(tighten(a.video, a.transcript, a.out, gap=a.gap, keep=a.keep,
                   fillers=a.fillers, fillers_config=a.fillers_config,
                   fillers_stutter=a.fillers_stutter,
                   silence=a.silence, silence_config=a.silence_config))
