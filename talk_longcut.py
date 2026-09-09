# -*- coding: utf-8 -*-
"""ДЛИННОЕ ВИДЕО -> N КОРОТКИХ ЭПИЗОДОВ (сырьё под рилсы).

ЗАЧЕМ. У завода есть движок, который собирает рилс из ОДНОГО короткого дубля,
который клиент ещё должен снять. А у эксперта уже лежат эфиры, вебинары и
подкасты по часу — и достать из них рилсы было нечем: стадии «длинное видео ->
интересные куски» в конвейере не существовало (ни одного аудио-сигнала, ни
ASR-стратегии для часового файла). Клиент говорит «нарежь рилсы из моего эфира»,
называет ЧИСЛО роликов — и получает нарезанные куски, из которых дальше
собираются рилсы обычным путём (render_reels.py), без единой правки движка.

ШЕСТЬ СТАДИЙ.
  A. СКАН — whisperx `small` БЕЗ форс-выравнивания на весь файл. Замер
     28.08.2026 (10 мин русской речи, CPU, int8): распознавание 0.15×
     реального времени, выравнивание — ЕЩЁ 0.18× сверху и от размера модели
     почти не зависит (109 с на small против 104 с на medium). На выбор
     эпизодов пословные границы не нужны — хватает посегментных, поэтому
     выравнивание тут не делается вовсе: час эфира ≈ 9 минут вместо 20.
  B. ГРАНИЦЫ — где вообще можно резать, не разорвав мысль: концы сегментов
     распознавания + паузы по звуку (talk_tighten.speech_envelope ->
     silence_threshold -> silence_runs, всё готовое, один проход ffmpeg).
  C. ВЫБОР ПО СМЫСЛУ — модель. Тот же двухрежимный приём, что в talk_segment:
     есть ANTHROPIC_API_KEY -> Messages API; ключа нет -> build_prompt() отдаёт
     промпт, отвечает текущая сессия Claude, --from-answer ответ валидирует.
     У клиентов ключа НЕТ (вопросы про него убраны из анкеты 26.08.2026), и
     рабочий путь для них — именно второй, бесплатный.
     Почему не эвристика: громкость и темп речи инсайт не различают
     (RESEARCH_montage.md §7 говорит это прямым текстом). Эвристика здесь —
     второй сигнал для сортировки, а не отбор.
  D. СНАП — границы эпизода подтягиваются к ближайшей границе слова и паузе:
     кусок не начинается с середины слова и не обрывается на полуфразе.
  E. НАРЕЗКА — по вызову ffmpeg на эпизод, с `-ss` ДО `-i` (быстрый поиск:
     декодируется ~минута вместо всего часа). Намеренно НЕ talk_tighten.
     cut_video(): там вход читается с нуля (trim-фильтр), и десять эпизодов
     из часового файла дали бы десять полных проходов декодера.
  F. ТОЧНЫЙ ТРАНСКРИПТ КАЖДОГО КУСКА — `medium` + выравнивание, но ТОЛЬКО на
     выбранных эпизодах (8 роликов по минуте = 8 минут звука вместо 60).
     Рядом с каждым mp4 ложится <имя>_transcript.json в СОБСТВЕННОЙ шкале
     куска. Это и делает нарезку сырьём: render_reels берёт клип с готовым
     транскриптом и не тратит время на распознавание заново.

ПРЕДОХРАНИТЕЛИ.
  * Ничего не публикует и ничего не удаляет. Только читает исходник и пишет
    новые файлы в свой каталог.
  * Исходник не трогает и не переименовывает.
  * Веса грузятся ОФЛАЙН, если они уже в кэше (HF_HUB_OFFLINE=1). Замер
    28.08.2026: при живой сети whisperx идёт в huggingface обновлять снапшот,
    запрос отвалился по таймауту, и загрузка модели упала «Cannot load the
    vocabulary from the model directory» — при полных весах на диске.
    Двадцатиминутную работу нельзя ронять на первой минуте из-за чужого
    сервера. Отключается флагом --online.

ЗАПУСК (клиенту эти строки не показываем — он говорит словами):
    python talk_longcut.py --video "<эфир>" --count 8 --scan
    python talk_longcut.py --video "<эфир>" --count 8 --print-prompt
    python talk_longcut.py --video "<эфир>" --count 8 --from-answer ans.json
    python talk_longcut.py --video "<эфир>" --count 8            # если есть ключ

Коды выхода: 0 — сделано, 1 — не с чего начать (нет файла, нечитаем), 2 —
сделано не всё (часть эпизодов не нарезалась).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Симлинки HF-кэша на Windows требуют прав, которых у обычного пользователя нет.
# Ставим ДО любого импорта huggingface_hub: он читает эти переменные один раз,
# при импорте, и позже менять их поздно. Поймано живьём 28.08.2026 — прогон
# упал `WinError 1314: клиент не обладает требуемыми правами` на попытке
# сделать симлинк vocabulary.txt.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

#: Коридор длины эпизода. Короче 20 с — мысль не успевает развернуться,
#: длиннее 90 с — это уже не рилс. Совпадает с тем, что площадки берут в ленту.
MIN_LEN = 20.0
MAX_LEN = 90.0

#: Модель скана и модель точного разбора. Скан — маленькая и без выравнивания
#: (стадия A), точный разбор — та же, что у остального завода (talk_transcribe
#: QUALITY_PRESETS["draft"]), но только на выбранных кусках.
SCAN_MODEL = "small"
FINAL_MODEL = "medium"

#: Имя ключа модели — то же, что у talk_segment (один ключ на завод).
API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 8000


class NotReadable(RuntimeError):
    """Исходник не читается — работать не с чем.

    Отдельный класс, потому что причина у этого почти всегда одна и лечится
    руками клиента: файл на Google Диске лежит «только в облаке» и физически
    на диске его нет (OSError Errno 22). Ровно эта грабля 27.08.2026 роняла
    рендер в talk_broll.save_lock. Проверяем ДО работы, а не на двадцатой
    минуте прогона."""


# ── служебное ────────────────────────────────────────────────────────────────
def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg: %s" % p.stderr.decode("utf-8", "ignore")[-800:])
    return p


def probe_duration(video):
    """Длительность исходника в секундах (через talk_tighten — не дублируем)."""
    import talk_tighten
    return talk_tighten.probe_duration(video)


def check_readable(video):
    """Файл на месте и ЧИТАЕТСЯ? Иначе NotReadable с человеческим текстом.

    Мало os.path.exists: у файла с Google Диска «только в облаке» запись в
    каталоге есть, а байтов нет — открытие рвётся OSError. Читаем первый
    килобайт, этого достаточно, чтобы отличить одно от другого."""
    if not os.path.isfile(video):
        raise NotReadable("файла нет: %s" % video)
    try:
        with open(video, "rb") as f:
            f.read(1024)
    except OSError as e:
        raise NotReadable(
            "файл есть в списке, но не читается (%s). Так ведёт себя видео, "
            "которое Google Диск держит «только в облаке»: скачайте его на "
            "компьютер (правая кнопка → «Оставить на этом устройстве») и "
            "повторите." % e.__class__.__name__)


def cached_sizes():
    """Какие размеры whisper уже лежат в кэше проекта (в сеть не ходим)."""
    cache = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.path.join(
        HERE, ".cache_models", "hub")
    out = []
    try:
        for name in os.listdir(cache):
            if name.startswith("models--Systran--faster-whisper-"):
                out.append(name.rsplit("-", 1)[-1])
    except OSError:
        pass
    return out


def scan_model(preferred=SCAN_MODEL):
    """Какой моделью сканировать, чтобы НЕ качать полгигабайта молча.

    Установщик привозит клиенту одну модель — `medium` (install_models.py,
    DEFAULT_MODEL). Маленькой `small`, которой скан быстрее вдвое, у него может
    не быть вовсе, и просить её с huggingface посреди работы нельзя: это 0,5 ГБ
    без спроса, а на отрезанной сети — просто отказ.

    Поэтому: есть `small` — берём её; нет, но есть что-то другое — сканируем
    тем, что есть, и говорим вслух, что будет дольше. Молчать тут нельзя:
    разница между 9 и 22 минутами на час записи — это то, что человек ждёт."""
    have = cached_sizes()
    if preferred in have:
        return preferred, None
    for fallback in (FINAL_MODEL, "large-v3", "base", "tiny"):
        if fallback in have:
            return fallback, (
                "модели «%s» на этом компьютере нет — сканирую «%s». Это тот же "
                "результат, но примерно вдвое дольше; качать что-то из "
                "интернета я без спроса не буду." % (preferred, fallback))
    return preferred, None          # кэш пуст — пусть whisperx решает сам


def _offline_if_cached(model_size):
    """Веса уже в кэше -> запретить поход в сеть за обновлением снапшота.

    См. предохранители в шапке: живая сеть роняет load_model на таймауте HF,
    хотя веса на диске полные."""
    cache = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.path.join(
        HERE, ".cache_models", "hub")
    if os.path.isdir(os.path.join(cache, "models--Systran--faster-whisper-%s"
                                  % model_size)):
        os.environ["HF_HUB_OFFLINE"] = "1"
        return True
    return False


def _newer(what, than):
    """Файл `what` свежее файла `than`? Нет одного из них — считаем, что нет."""
    try:
        return os.path.getmtime(what) >= os.path.getmtime(than)
    except OSError:
        return False


def _slug(text, maxlen=40):
    """Тема эпизода -> кусок имени файла: русские буквы оставляем, мусор убираем.

    Имя файла видит клиент в проводнике и на телефоне, поэтому оно должно быть
    русским и читаемым, а не хешем."""
    s = re.sub(r"[^\w\s-]", "", str(text), flags=re.UNICODE).strip()
    s = re.sub(r"\s+", "_", s)
    return s[:maxlen].strip("_") or "эпизод"


def human_time(sec):
    """Секунды -> «7 мин 12 с» для отчёта человеку."""
    sec = int(round(sec))
    return "%d мин %02d с" % (sec // 60, sec % 60) if sec >= 60 else "%d с" % sec


# ── A. СКАН ──────────────────────────────────────────────────────────────────
def scan(video, out_path=None, model_size=SCAN_MODEL, language="ru",
         online=False):
    """Весь файл -> черновой транскрипт ПОСЕГМЕНТНО (без форс-выравнивания).

    Возвращает (путь, объект скана). Формат СВОЙ, не engine_talk: там нужен
    массив сегментов со СЛОВАМИ, а тут слов нет намеренно — они стоят дороже
    самого распознавания и на выбор эпизодов не влияют.
        {"src", "dur", "model", "segments":[{"s","e","text"}, ...]}
    """
    check_readable(video)
    if not online:
        _offline_if_cached(model_size)
    import talk_transcribe as tt          # ставит HF_HOME/кэш на .cache_models
    import whisperx

    tt.neutralize_speechbrain_lazy_imports()
    audio = tt._load_audio(video)
    with tt._torch_trusted_load():
        model = whisperx.load_model(model_size, "cpu", compute_type="int8",
                                    language=language)
    raw = model.transcribe(audio, batch_size=8, language=language)

    segs = []
    for s in raw.get("segments", []):
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        segs.append({"s": round(float(s["start"]), 2),
                     "e": round(float(s["end"]), 2), "text": text})
    obj = {"src": os.path.basename(video), "dur": round(probe_duration(video), 2),
           "model": model_size, "segments": segs}
    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    return out_path, obj


def load_scan(path):
    with open(path, encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict) or "segments" not in obj:
        raise ValueError("это не файл скана (нет поля segments): %s" % path)
    return obj


# ── B. ГРАНИЦЫ, ГДЕ МОЖНО РЕЗАТЬ ─────────────────────────────────────────────
def pause_boundaries(video, dur=None):
    """Середины пауз по ЗВУКУ — самые честные точки склейки.

    Готовый путь talk_tighten (W2): огибающая громкости одним проходом ffmpeg,
    порог из перцентилей САМОЙ записи (уровни разных записей расходятся на
    21 дБ, абсолютный порог негоден), отрезки тишины длиннее min_sil.
    Сеть не трогает, моделей не грузит. Ошибка звука — не поломка стадии:
    остаются границы сегментов распознавания."""
    import talk_tighten
    params = talk_tighten.load_silence()
    win = talk_tighten._p(params, "win", 0.02)
    try:
        levels = talk_tighten.speech_envelope(video, win=win)
    except (RuntimeError, OSError) as e:
        # Молча возвращать пустой список нельзя: «пауз не нашлось» и «замер не
        # состоялся» — разные вещи, а решение о границах принимается по ним
        # обоим. Говорим вслух и работаем на границах сегментов речи.
        print("  ⚠ паузы по звуку не померились (%s) — границы беру только по "
              "речи" % e.__class__.__name__)
        return []
    thr, _speech, _floor = talk_tighten.silence_threshold(levels, params)
    runs = talk_tighten.silence_runs(
        levels, win, thr, talk_tighten._p(params, "min_sil", 0.30), dur=dur)
    return [round((a + b) / 2.0, 2) for a, b in runs]


def cut_points(scan_obj, video=None):
    """Все допустимые точки реза: концы сегментов речи + середины пауз."""
    pts = {0.0, float(scan_obj["dur"])}
    for s in scan_obj["segments"]:
        pts.add(float(s["s"]))
        pts.add(float(s["e"]))
    if video:
        pts.update(pause_boundaries(video, dur=float(scan_obj["dur"])))
    return sorted(pts)


def snap(value, points, limit=2.5):
    """Подтянуть таймкод к ближайшей допустимой точке реза.

    limit — насколько далеко можно тянуть. Дальше не тянем: значит модель
    показала на место, где границы речи нет, и лучше оставить как есть, чем
    увести эпизод на чужую мысль."""
    if not points:
        return round(float(value), 2)
    best = min(points, key=lambda p: abs(p - value))
    return round(best if abs(best - value) <= limit else float(value), 2)


# ── C. ПРОМПТ ────────────────────────────────────────────────────────────────
def timecoded_text(scan_obj):
    """Транскрипт с таймкодами — вход для смыслового отбора."""
    return "\n".join("[%.1f–%.1f] %s" % (s["s"], s["e"], s["text"])
                     for s in scan_obj["segments"])


def build_prompt(scan_obj, count, min_len=MIN_LEN, max_len=MAX_LEN):
    """Промпт стадии C. Просим РОВНО count эпизодов и запрещаем додумывать.

    Главное требование к формулировке — самодостаточность куска: зритель
    открывает рилс без контекста эфира, и «как я уже говорил» в первой секунде
    убивает ролик вернее любой ошибки монтажа."""
    dur = float(scan_obj["dur"])
    return (
        "Ты — монтажёр вертикальных рилсов. Ниже транскрипт длинного видео "
        "(эфир/подкаст/вебинар) с таймкодами в секундах.\n\n"
        "ЗАДАЧА: выбрать РОВНО %d самых интересных эпизодов, из которых выйдут "
        "самостоятельные короткие ролики.\n\n"
        "ЧТО СЧИТАЕТСЯ ИНТЕРЕСНЫМ (в порядке ценности):\n"
        "  1. контринтуитивная мысль, с которой хочется спорить;\n"
        "  2. личная история с поворотом;\n"
        "  3. конкретный разбор «как делать» с цифрой или примером;\n"
        "  4. откровение, признание, сильная эмоция;\n"
        "  5. чёткий ответ на вопрос, который люди правда задают.\n"
        "НЕ берём: приветствия, оргмоменты, «как меня слышно», переходы между "
        "темами, ответы, непонятные без предыдущих десяти минут.\n\n"
        "ЖЁСТКИЕ ТРЕБОВАНИЯ К КАЖДОМУ ЭПИЗОДУ:\n"
        "  * САМОДОСТАТОЧНОСТЬ. Зритель включает его без контекста эфира. "
        "Эпизод не начинается с «и вот», «как я уже сказал», «поэтому» и не "
        "обрывается на середине мысли.\n"
        "  * ДЛИНА %g–%g секунд. Начало — там, где мысль начинается, конец — "
        "там, где она закончена, а не по будильнику.\n"
        "  * НЕ ПЕРЕСЕКАЮТСЯ между собой, все внутри [0, %.1f].\n"
        "  * Разнесены по всему видео, а не сгрудились в одном месте.\n\n"
        "На каждый эпизод верни поля:\n"
        "  t0, t1  — границы в секундах (числа);\n"
        "  тема    — о чём кусок, 2–5 слов по-русски (пойдёт в имя файла);\n"
        "  хук     — первая фраза эпизода дословно из транскрипта;\n"
        "  почему  — одним предложением, чем это зацепит зрителя;\n"
        "  сила    — насколько уверен, что зайдёт: 0.0–1.0.\n\n"
        "Верни СТРОГО JSON-массив, без пояснений и без markdown-заборов:\n"
        '[{"t0":123.4,"t1":178.9,"тема":"...","хук":"...","почему":"...",'
        '"сила":0.8}, ...]\n\n'
        "ТРАНСКРИПТ (%d сегментов, длительность %.1f сек):\n%s\n"
        % (count, min_len, max_len, dur, len(scan_obj["segments"]), dur,
           timecoded_text(scan_obj))
    )


def api_key():
    """Ключ модели: окружение, затем <корень>/.env. Пусто — ключа нет.

    Повторяет talk_segment.api_key(), но через импорт: один источник правды."""
    try:
        import talk_segment
        return talk_segment.api_key()
    except Exception:
        return (os.environ.get(API_KEY_ENV) or "").strip()


def choose_via_api(scan_obj, count, model=DEFAULT_MODEL, min_len=MIN_LEN,
                   max_len=MAX_LEN):
    """Сырой ответ модели на промпт стадии C (путь с ключом, платный)."""
    key = api_key()
    if not key:
        raise RuntimeError("нет %s — иди in-session режимом" % API_KEY_ENV)
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model, max_tokens=MAX_TOKENS,
        messages=[{"role": "user",
                   "content": build_prompt(scan_obj, count, min_len, max_len)}])
    return "".join(b.text for b in msg.content
                   if getattr(b, "type", "") == "text")


# ── D. ВАЛИДАЦИЯ И СНАП ──────────────────────────────────────────────────────
def parse_answer(raw):
    """Достать JSON-массив из ответа (снять ```-заборы, найти [ ... ])."""
    if isinstance(raw, list):
        return raw
    s = str(raw).strip()
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()
    m = re.search(r"\[.*\]", s, re.S)
    data = json.loads(m.group(0) if m else s)
    if isinstance(data, dict):
        data = data.get("эпизоды") or data.get("episodes") or []
    if not isinstance(data, list) or not data:
        raise ValueError("ожидался непустой JSON-массив эпизодов")
    return data


def validate(episodes, dur, min_len=MIN_LEN, max_len=MAX_LEN, points=None):
    """Проверить контракт эпизодов, подтянуть границы, вернуть (эпизоды, замечания).

    Роняем только на том, чего чинить нельзя (нет границ, t1<=t0, вне файла).
    Пересечения и выход за коридор длины — замечание и правка, а не падение:
    ответ модели целиком выбрасывать из-за одного кривого куска дороже, чем
    подрезать его и сказать об этом вслух."""
    warn = []
    out = []
    for i, e in enumerate(episodes):
        if "t0" not in e or "t1" not in e:
            raise ValueError("эпизод %d: нет границ t0/t1" % (i + 1))
        t0, t1 = float(e["t0"]), float(e["t1"])
        if t1 <= t0:
            raise ValueError("эпизод %d: t1<=t0 (%.1f<=%.1f)" % (i + 1, t1, t0))
        if t0 < 0 or t1 > dur + 0.5:
            raise ValueError("эпизод %d: выходит за файл (%.1f–%.1f при %.1f)"
                             % (i + 1, t0, t1, dur))
        if points:
            t0, t1 = snap(t0, points), snap(t1, points)
            if t1 <= t0:                       # снап схлопнул — берём как было
                t0, t1 = float(e["t0"]), float(e["t1"])
        if t1 - t0 < min_len:
            warn.append("эпизод %d короче %g с (%.1f) — оставлен, посмотрите глазами"
                        % (i + 1, min_len, t1 - t0))
        if t1 - t0 > max_len:
            warn.append("эпизод %d длиннее %g с (%.1f) — обрезан по %g"
                        % (i + 1, max_len, t1 - t0, max_len))
            t1 = round(t0 + max_len, 2)
        out.append({"t0": round(t0, 2), "t1": round(t1, 2),
                    "тема": str(e.get("тема") or e.get("topic") or "").strip(),
                    "хук": str(e.get("хук") or e.get("hook") or "").strip(),
                    "почему": str(e.get("почему") or e.get("why") or "").strip(),
                    "сила": round(float(e.get("сила", e.get("score", 0.5))), 2)})

    out.sort(key=lambda x: x["t0"])
    for i in range(len(out) - 1):
        if out[i]["t1"] > out[i + 1]["t0"]:
            warn.append("эпизоды %d и %d пересекаются — второй сдвинут"
                        % (i + 1, i + 2))
            out[i + 1]["t0"] = out[i]["t1"]
    out = [e for e in out if e["t1"] - e["t0"] >= 3.0]
    return out, warn


# ── E. НАРЕЗКА ───────────────────────────────────────────────────────────────
def cut_one(video, t0, t1, out_path):
    """Один эпизод -> mp4. `-ss` ДО `-i` — быстрый поиск по ключевым кадрам.

    Почему не talk_tighten.cut_video(): там вход подаётся целиком и режется
    trim-фильтром, то есть декодер проходит файл с нуля. На часовом исходнике
    десять эпизодов — десять полных проходов. Здесь ffmpeg прыгает к нужной
    минуте сразу и декодирует только сам эпизод.
    Точность при этом не страдает: `-ss` перед `-i` с последующим
    перекодированием доводит до кадра от ближайшего ключевого.

    ПОЧЕМУ ДВА ПРОХОДА (E4-62). Проход 2 — СТРАХОВКА, а не починка живого бага.
    Обоснование переписано 02.09 по замеру: прежний текст утверждал, что ffmpeg
    при записи копирует флаг поворота, и это ОКАЗАЛОСЬ НЕВЕРНО для нашего случая.

    Замерено дважды и независимо (здесь на настоящем айфонном файле
    `.cache_src/standart_src.mp4` и на маке на фикстуре с матрицей, вписанной
    прямо в бокс tkhd), на ffmpeg 7.1, углы 90/180/270:
        ПЕРЕСЖАТИЕ    -> флаг НЕ переносится, результат выходит правильным;
        КОПИЯ ПОТОКА  -> флаг остаётся.
    Проход 1 здесь именно пересжимает, поэтому одного прохода уже достаточно:
    старый однопроходный код на этой связке давал ровно тот же результат.

    Посылка «ffmpeg всегда копирует флаг» верна для конвейеров с filter_complex
    (`engine_video.py`, см. шапку модуля) — оттуда она и была перенесена сюда по
    аналогии, без проверки. Так делать нельзя, и этот абзац стоит здесь как
    напоминание.

    Зачем проход 2 всё-таки оставлен: он закрывает случай `-c copy` и старые
    сборки ffmpeg, которые флаг переносят, а стоит одну перепаковку без
    пересжатия — замер на куске 60 с: 21.76 -> 21.88 с, +0.6%, размер и качество
    не меняются. Дёшево и снимает целый класс различий между сборками.

    ⚠️ И ГЛАВНОЕ, ЧТОБЫ НЕ ИСКАТЬ ЗДЕСЬ ЧУЖОЙ БАГ. Жалоба клиентки («три клипа
    выходят на боку») этим кодом НЕ объясняется: проверены все пути обработки, ни
    один перекоса не даёт. Настоящая причина — исходник СНЯТ или ВЫГРУЖЕН на боку
    и флага не несёт вовсе; метаданных, по которым это видно, тогда не
    существует. Ловит это только содержимое — наклон линии глаз, см. `checks.py`
    и `test_rotation_contract.py`."""
    # Временный файл кладём РЯДОМ с целевым: перепаковка проходом 2 должна идти
    # по тому же диску, а не через системный temp.
    tmp = out_path + ".rot.tmp.mp4"
    try:
        # ПРОХОД 1 — обрезка и кодирование. Единственный, который кодирует.
        _run([_ffmpeg(), "-y", "-loglevel", "error",
              "-ss", "%.3f" % t0, "-i", video, "-t", "%.3f" % (t1 - t0),
              "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
              "-crf", "20", "-preset", "veryfast",
              "-c:a", "aac", "-b:a", "160k",
              "-movflags", "+faststart", tmp])
        # ПРОХОД 2 — снять скопированный флаг поворота, перепаковка без пересжатия.
        _run([_ffmpeg(), "-y", "-loglevel", "error",
              "-display_rotation:v:0", "0", "-i", tmp,
              "-c", "copy", "-movflags", "+faststart", out_path])
    finally:
        # Убираем черновик и при отказе тоже — иначе рядом с кусками клиента
        # останется мусор (cut_episodes ловит исключение и идёт дальше).
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return out_path


def cut_episodes(video, episodes, out_dir, prefix=""):
    """Все эпизоды -> файлы. Возвращает список (эпизод, путь | None).

    Отказ на одном эпизоде не роняет остальные: девять готовых кусков лучше,
    чем ноль из-за одного битого места в исходнике."""
    os.makedirs(out_dir, exist_ok=True)
    done = []
    for i, e in enumerate(episodes, 1):
        name = "%02d_%s.mp4" % (i, _slug(e.get("тема") or "эпизод"))
        path = os.path.join(out_dir, (prefix + name) if prefix else name)
        try:
            cut_one(video, e["t0"], e["t1"], path)
            done.append((e, path))
        except Exception as ex:
            print("  ! эпизод %d не нарезался: %s" % (i, ex), flush=True)
            done.append((e, None))
    return done


def write_card(episode, video_path, out_path):
    """Человекочитаемая карточка рядом с куском — её видит клиент в проводнике."""
    lines = ["ТЕМА: %s" % (episode.get("тема") or "—"),
             "",
             "ПЕРВАЯ ФРАЗА: %s" % (episode.get("хук") or "—"),
             "ЧЕМ ЗАЦЕПИТ: %s" % (episode.get("почему") or "—"),
             "",
             "Откуда: %s, с %s по %s (%.0f сек)."
             % (os.path.basename(video_path),
                human_time(episode["t0"]), human_time(episode["t1"]),
                episode["t1"] - episode["t0"]),
             "",
             "Это СЫРЬЁ, а не готовый рилс: скажите заводу «собери рилс из "
             "этого куска» — он смонтирует."]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


# ── F. ТОЧНЫЙ ТРАНСКРИПТ КУСКА ───────────────────────────────────────────────
def transcribe_cuts(paths, model_size=FINAL_MODEL, language="ru", online=False):
    """У каждого куска — свой transcript.json в ЕГО шкале (0 = начало куска).

    Так нарезка становится сырьём: render_reels.py получает клип с готовым
    транскриптом и не тратит время на распознавание заново. Считаем ПОСЛЕ
    нарезки, по самим кускам, а не пересчитываем шкалу большого транскрипта:
    скан делался маленькой моделью и без выравнивания, а в субтитры идёт
    текст — он должен быть точным."""
    if not online:
        _offline_if_cached(model_size)
    import talk_transcribe as tt
    out = []
    for p in paths:
        if not p:
            continue
        ready = os.path.splitext(p)[0] + "_transcript.json"
        # Готовый транскрипт берём, только если он СВЕЖЕЕ самого куска. Стадия
        # дорогая (минута звука ≈ полминуты счёта), и обрыв на одном куске —
        # дело житейское: повторный запуск должен доделывать, а не считать всё
        # заново. Но если эпизод перерезали с другими границами, старый
        # транскрипт к нему больше не подходит — субтитры уедут от голоса, и
        # молча взять его нельзя. Сравнение времён и отличает одно от другого.
        if os.path.exists(ready) and _newer(ready, p):
            try:
                with open(ready, encoding="utf-8") as fh:
                    segs = json.load(fh)
                out.append((p, ready, sum(len(s["words"]) for s in segs)))
                continue
            except (OSError, ValueError):
                pass                      # битый — пересчитаем ниже
        try:
            dst, segs = tt.transcribe(p, None, model_size=model_size,
                                      language=language)
            out.append((p, dst, sum(len(s["words"]) for s in segs)))
        except Exception as ex:
            print("  ! транскрипт для %s не сделан: %s"
                  % (os.path.basename(p), ex), flush=True)
            out.append((p, None, 0))
    return out


# ── отчёт ────────────────────────────────────────────────────────────────────
def report(video, scan_obj, episodes, cuts, transcripts, timings):
    """Замер, не вывод: что взято, какой длины, сколько заняло."""
    dur = float(scan_obj["dur"])
    covered = sum(e["t1"] - e["t0"] for e in episodes)
    print("")
    print("ИСХОДНИК: %s — %s, речи распознано %d сегментов"
          % (os.path.basename(video), human_time(dur), len(scan_obj["segments"])))
    print("ВЗЯТО: %d эпизодов, суммарно %s (%.1f%% хронометража)"
          % (len(episodes), human_time(covered), 100.0 * covered / max(dur, 1)))
    print("")
    tr_by_path = {p: (d, n) for p, d, n in transcripts}
    for i, (e, path) in enumerate(cuts, 1):
        mark = "✔" if path else "✘"
        tr = tr_by_path.get(path, (None, 0))
        print("  %s %2d) %-38s %s–%s  %4.0f с  сила %.2f%s"
              % (mark, i, (e.get("тема") or "—")[:38],
                 human_time(e["t0"]), human_time(e["t1"]),
                 e["t1"] - e["t0"], e.get("сила", 0),
                 "  слов %d" % tr[1] if tr[0] else "  БЕЗ ТРАНСКРИПТА"))
    print("")
    for name, sec in timings:
        print("  %-28s %s" % (name, human_time(sec)))


# ── CLI ──────────────────────────────────────────────────────────────────────
def work_paths(video, out_root=None):
    """Куда складываем: out_longcut/<имя исходника>/."""
    base = os.path.splitext(os.path.basename(video))[0]
    root = out_root or os.path.join(HERE, "out_longcut")
    return os.path.join(root, _slug(base, 60)), base


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Длинное видео -> N коротких эпизодов (сырьё под рилсы)")
    ap.add_argument("--video", required=True, help="длинное видео (эфир/подкаст)")
    ap.add_argument("--count", type=int, default=8,
                    help="сколько эпизодов нарезать (спрашивается у клиента)")
    ap.add_argument("--out", default=None, help="корень для результатов")
    ap.add_argument("--min", dest="min_len", type=float, default=MIN_LEN)
    ap.add_argument("--max", dest="max_len", type=float, default=MAX_LEN)
    ap.add_argument("--scan", action="store_true",
                    help="только стадия A: распознать и сохранить скан")
    ap.add_argument("--print-prompt", action="store_true",
                    help="напечатать промпт отбора (in-session режим)")
    ap.add_argument("--prompt-out", default=None,
                    help="записать промпт в файл вместо печати")
    ap.add_argument("--from-answer", default=None,
                    help="in-session: файл с JSON-ответом на промпт отбора")
    ap.add_argument("--no-transcribe", action="store_true",
                    help="не делать точные транскрипты кусков (стадия F)")
    ap.add_argument("--online", action="store_true",
                    help="разрешить whisperx ходить в сеть за обновлением весов "
                         "(по умолчанию офлайн, если веса уже в кэше)")
    args = ap.parse_args(argv)

    video = args.video
    work, base = work_paths(video, args.out)
    os.makedirs(work, exist_ok=True)
    scan_path = os.path.join(work, base + "_scan.json")
    timings = []

    try:
        check_readable(video)
    except NotReadable as e:
        print("Не с чего начать: %s" % e)
        return 1

    # A. скан (переиспользуем готовый, если он уже есть — час речи стоит дорого)
    if os.path.exists(scan_path) and not args.scan:
        scan_obj = load_scan(scan_path)
        print("скан взят готовый: %s (%d сегментов)"
              % (scan_path, len(scan_obj["segments"])))
    else:
        if os.path.exists(scan_path) and args.scan:
            print("скан уже есть, пересчитываю: %s" % scan_path)
        size, note = scan_model()
        if note:
            print("  ⚠ %s" % note)
        print("сканирую %s (%s) моделью %s без выравнивания…"
              % (os.path.basename(video), human_time(probe_duration(video)),
                 size), flush=True)
        t = time.time()
        scan_path, scan_obj = scan(video, scan_path, model_size=size,
                                   online=args.online)
        timings.append(("скан (стадия A)", time.time() - t))
        print("готово: %s (%d сегментов, %s)"
              % (scan_path, len(scan_obj["segments"]), human_time(timings[-1][1])))
        if args.scan:
            return 0

    # C. промпт / ответ
    if args.print_prompt or args.prompt_out:
        text = build_prompt(scan_obj, args.count, args.min_len, args.max_len)
        if args.prompt_out:
            with open(args.prompt_out, "w", encoding="utf-8") as f:
                f.write(text)
            print("промпт записан: %s (%d символов)" % (args.prompt_out, len(text)))
        else:
            print(text)
        return 0

    if args.from_answer:
        raw = open(args.from_answer, encoding="utf-8").read()
    elif api_key():
        print("ключ модели есть — отбираю эпизоды через API…", flush=True)
        t = time.time()
        raw = choose_via_api(scan_obj, args.count, min_len=args.min_len,
                             max_len=args.max_len)
        timings.append(("отбор по смыслу (стадия C)", time.time() - t))
    else:
        print("Ключа %s нет — режим in-session (это штатный путь у клиента).\n"
              "  1) python talk_longcut.py --video \"%s\" --count %d --prompt-out "
              "%s\n  2) отдай промпт Claude, сохрани JSON-ответ в файл;\n"
              "  3) python talk_longcut.py --video \"%s\" --count %d "
              "--from-answer <файл>"
              % (API_KEY_ENV, video, args.count,
                 os.path.join(work, "prompt.txt"), video, args.count))
        return 0

    # D. валидация и снап
    t = time.time()
    points = cut_points(scan_obj, video=video)
    episodes, warn = validate(parse_answer(raw), float(scan_obj["dur"]),
                              args.min_len, args.max_len, points)
    timings.append(("границы и проверка (B+D)", time.time() - t))
    for w in warn:
        print("  ⚠ %s" % w)
    if not episodes:
        print("Не с чего начать: годных эпизодов не осталось.")
        return 1
    with open(os.path.join(work, base + "_episodes.json"), "w",
              encoding="utf-8") as f:
        json.dump({"src": video, "dur": scan_obj["dur"], "эпизоды": episodes},
                  f, ensure_ascii=False, indent=2)

    # E. нарезка
    print("режу %d эпизодов…" % len(episodes), flush=True)
    t = time.time()
    cuts = cut_episodes(video, episodes, work)
    timings.append(("нарезка (стадия E)", time.time() - t))
    for e, path in cuts:
        if path:
            write_card(e, video, os.path.splitext(path)[0] + ".txt")

    # F. точные транскрипты
    transcripts = []
    if not args.no_transcribe:
        print("делаю точные транскрипты кусков моделью %s…" % FINAL_MODEL,
              flush=True)
        t = time.time()
        transcripts = transcribe_cuts([p for _, p in cuts], online=args.online)
        timings.append(("транскрипты кусков (F)", time.time() - t))

    report(video, scan_obj, episodes, cuts, transcripts, timings)
    print("\nВсё лежит в: %s" % work)
    return 0 if all(p for _, p in cuts) else 2


if __name__ == "__main__":
    raise SystemExit(main())
