# -*- coding: utf-8 -*-
"""
МУЗЫКА ДЛЯ REELS БЕЗ РУК — CC0 через Openverse API.

ЗАЧЕМ. Гибридная схема (движок готовит файл, Валерия публикует руками ради
трендового звука) стоит ей ручного шага на каждом ролике. Этот модуль закрывает
последнюю дырку: музыка подтягивается сама, и цепочка становится полностью
автоматической — вплоть до autopost.py.

ЧТО ПРОВЕРЕНО ЖИВЬЁМ 17.07, А НЕ ВЗЯТО ИЗ ДОКОВ:

- **У Pixabay музыки в API НЕТ.** Ключ рабочий (/api/ и /api/videos/ отдают 200),
  но /api/music/ → 404, /api/audio/ → 403. Музыка у них только на сайте, руками.
  Не тратить время повторно.
- **Jamendo напрямую НЕ БЕРЁМ:** их API бесплатен для некоммерческого
  использования, для коммерции нужна платная лицензия. У Валерии — коммерция.
- **Openverse работает, фильтр по лицензии настоящий:** `license=cc0` → в выдаче
  только cc0 (проверено по фактическому полю каждого трека, а не по счётчику).
  ВНИМАНИЕ: `result_count` там всегда 240 на любой непустой запрос — это ПОТОЛОК
  выдачи, а не число находок. На счётчик не смотреть, он обманет: контрольный
  запрос-бессмыслицей даёт 0, значит поле живое, но упирается в 240.
- Файл скачивается прямой ссылкой (проверено: 3.5 МБ, audio/mpeg).

ПОЧЕМУ CC-BY, А НЕ CC0 — ГЛАВНОЕ РЕШЕНИЕ ЭТОГО МОДУЛЯ.
Сначала целились в CC0 (там атрибуция не нужна вовсе). **Не вышло, и вот почему:**
в Openverse три источника аудио — jamendo (627k), wikimedia_audio (3.9M),
freesound (591k). МУЗЫКА живёт только в jamendo. А `license=cc0 + source=jamendo`
даёт РОВНО НОЛЬ: под CC0 там лежат звуковые эффекты Freesound (взрывы, сирены,
дроны) и произношения слов из Викисловаря. Фоном под Reels это не положить.
Настоящая музыка — «Upbeat Corporate», «Inspiring Corporate», «Chill Out» —
вся под CC-BY.

Значит цена автоматической музыки — ОДНА СТРОКА АТРИБУЦИИ в описании поста.
CC-BY разрешает коммерческое использование, требует лишь указать автора,
трек и лицензию: см. attribution(). Строка идёт в КОНЕЦ описания или в первый
комментарий, чтобы не мешать смыслу.

ВАЖНО ПРО JAMENDO: их СЕРВИС лицензирования платный для коммерции — но это про
их подписку, а не про сами треки. Лицензия CC-BY выдана автором и разрешает
коммерческое использование при указании авторства. Мы опираемся на неё.
"""
import json
import os
import random
import re
import subprocess
import tempfile

import requests
import imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()
API = "https://api.openverse.org/v1/audio/"
UA = {"User-Agent": "karusel/1.0 (content factory)"}

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music_cache")


def search(query, license="by", source="jamendo", min_sec=15, max_sec=600,
           limit=20):
    """Найти МУЗЫКУ. Возвращает список словарей с title/url/sec/license/author.

    source="jamendo" — не украшение, а суть: это единственный источник музыки
    в Openverse. Без него в выдачу лезут эффекты Freesound и произношения
    из Викисловаря (проверено: по запросу "corporate" приходит "En-ca-corporal").

    min_sec: трек короче ролика пришлось бы зацикливать — отсеиваем сразу.
    Однословный запрос надёжнее: "cinematic corporate" даёт 0 (условия по И).
    """
    p = {"q": query, "license": license, "page_size": limit}
    if source:
        p["source"] = source
    r = requests.get(API, params=p, headers=UA, timeout=40)
    r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        sec = (it.get("duration") or 0) / 1000.0
        if not (min_sec <= sec <= max_sec) or not it.get("url"):
            continue
        out.append(dict(title=it.get("title"), url=it["url"], sec=sec,
                        license=it.get("license"),
                        license_version=it.get("license_version"),
                        provider=it.get("provider"),
                        author=it.get("creator"),
                        page=it.get("foreign_landing_url"),
                        id=it.get("id")))
    return out


def attribution(track):
    """Строка атрибуции — цена бесплатной музыки под CC-BY.

    CC-BY требует назвать трек, автора и лицензию. Ставить её в КОНЕЦ описания
    или в первый комментарий: подпись несёт смысл, кредиты не должны лезть
    вперёд него.
    """
    lic = (track.get("license") or "by").upper()
    ver = track.get("license_version") or ""
    return "Музыка: «%s» — %s, CC %s %s" % (
        track.get("title") or "?", track.get("author") or "?", lic, ver)


def download(track):
    """Скачать трек в music_cache. Повторно не качаем — файл остаётся навсегда."""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "%s.mp3" % track["id"])
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    d = requests.get(track["url"], headers=UA, timeout=120)
    d.raise_for_status()
    with open(p, "wb") as f:
        f.write(d.content)
    return p


def _run(args):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg упал:\n" + (r.stderr or "")[-2000:])
    return r


# ── ГРОМКОСТЬ: EBU R128 ──────────────────────────────────────────────────────
# ЗАЧЕМ. Замер ленты 22.08 (10 готовых роликов из out_reels\): интегральная
# громкость гуляет от -33.5 до -6.1 LUFS — РАЗБРОС 27.4 LU. Зритель листает
# ленту с одной громкостью телефона: один ролик орёт, следующий не слышно, и
# он уходит. Пересборка звука тем же кодом на 8 роликах давала разброс 14.3 LU
# и истинный пик до +2.7 dBTP (это клиппинг, а не «громко»).
#
# ЦЕЛЬ -14 LUFS — не вкусовщина: примерно к этому уровню приводят звук сами
# Instagram/TikTok при воспроизведении. Придём туда сами — платформа ролик не
# тронет; придём громче — она прижмёт и звук станет плоским.
# TP -1.5 dBTP — запас на межсэмпловые пики, которые появляются уже ПОСЛЕ
# перекодирования в AAC на стороне соцсети.
LOUD_I = -14.0          # целевая интегральная громкость, LUFS
LOUD_TP = -1.5          # потолок истинного пика, dBTP
LOUD_LRA = 11.0         # допустимый разброс динамики, LU

# Плавное появление музыки. Резкий старт трека с середины такта слышен как
# щелчок и читается как «склеено на коленке».
FADE_IN = 0.6


def loudnorm_stats(path, af=None, ss=None, t=None):
    """Первый проход loudnorm: что за громкость у куска НА САМОМ ДЕЛЕ.

    Возвращает словарь с измеренными i/tp/lra/thresh/offset или None, если
    ffmpeg ничего не отдал (битый файл, дорожки нет). None — не авария:
    вызывающий просто откатится на однопроходную нормализацию.
    """
    args = [FF, "-hide_banner", "-nostats"]
    if ss:
        args += ["-ss", "%.3f" % ss]
    args += ["-i", str(path)]
    if t:
        args += ["-t", "%.3f" % t]
    chain = (af + "," if af else "") + (
        "loudnorm=I=%.1f:TP=%.1f:LRA=%.1f:print_format=json"
        % (LOUD_I, LOUD_TP, LOUD_LRA))
    args += ["-vn", "-af", chain, "-f", "null", "-"]
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    blocks = re.findall(r"\{[^{}]*input_i[^{}]*\}", r.stderr or "", re.S)
    if not blocks:
        return None
    try:
        d = json.loads(blocks[-1])
        return dict(i=float(d["input_i"]), tp=float(d["input_tp"]),
                    lra=float(d["input_lra"]),
                    thresh=float(d["input_thresh"]),
                    offset=float(d["target_offset"]))
    except (ValueError, KeyError):
        return None


def _loudnorm_af(af, path, ss=None, t=None):
    """Дописать к цепочке фильтров нормализацию по EBU R128.

    ДВА ПРОХОДА, А НЕ ОДИН, И ЭТО ВАЖНО. Однопроходный loudnorm работает
    динамически: он не знает, что будет дальше, и подтягивает громкость по ходу
    — тихое вступление задирается, кульминация прижимается, музыка «дышит».
    Замерив кусок заранее, во втором проходе мы применяем ОДНУ константу
    (linear=true) — уровень выравнивается между роликами, а динамика внутри
    ролика остаётся авторской.

    aresample на конце обязателен: loudnorm в линейном режиме отдаёт 192 кГц,
    и без пересэмплирования это утекает в AAC-дорожку готового ролика.
    """
    m = loudnorm_stats(path, af=af, ss=ss, t=t)
    base = "loudnorm=I=%.1f:TP=%.1f:LRA=%.1f" % (LOUD_I, LOUD_TP, LOUD_LRA)
    if m:
        base += (":measured_I=%.2f:measured_TP=%.2f:measured_LRA=%.2f"
                 ":measured_thresh=%.2f:offset=%.2f:linear=true"
                 % (m["i"], m["tp"], m["lra"], m["thresh"], m["offset"]))
    return (af + "," if af else "") + base + ",aresample=48000"


def add_music(video_in, track_path, out, start=0.0, fade=1.0, gain=1.0,
              fade_in=FADE_IN, loudnorm=True):
    """Положить музыку на ролик, ЗАМЕНИВ оригинальный звук.

    Заменяем, а не подмешиваем, и это не лень: кадры лайфстайл-банка сняты на
    телефон в кафе, на катере, в примерочной — там ветер, гул и чужие голоса.
    Подмешивать это под музыку незачем, звук ролика смысла не несёт (говорящей
    головы в банке нет).

    fade    — затухание в конце: обрыв музыки на полуслове слышен и дёшев на вид.
    fade_in — появление в начале, см. FADE_IN.
    gain    — ВНИМАНИЕ: при loudnorm=True (по умолчанию) он на итоговый уровень
              НЕ влияет, нормализация его съедает. Тише/громче делается целью
              LOUD_I, а не этим множителем; gain оставлен для loudnorm=False.
    """
    dur = _probe_dur(video_in)
    parts = []
    if gain != 1.0:
        parts.append("volume=%.2f" % gain)
    if fade_in and fade_in > 0:
        parts.append("afade=t=in:st=0:d=%.2f" % fade_in)
    if fade and fade > 0:
        parts.append("afade=t=out:st=%.2f:d=%.2f" % (max(0.0, dur - fade), fade))
    af = ",".join(parts)
    if loudnorm:
        # Замеряем ИМЕННО тот кусок и с теми фильтрами, что пойдут в файл, —
        # иначе измеренное не совпадёт с записанным.
        af = _loudnorm_af(af, track_path, ss=start, t=dur)
    _run([FF, "-y", "-loglevel", "error",
          "-i", video_in,
          "-ss", "%.2f" % start, "-i", track_path,
          "-map", "0:v:0", "-map", "1:a:0",
          "-af", af,
          "-t", "%.3f" % dur,
          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
          "-movflags", "+faststart", out])
    return out


def has_audio(path):
    """Есть ли в файле звуковая дорожка (парсим stderr ffmpeg, ffprobe в
    комплект imageio-ffmpeg не входит)."""
    r = subprocess.run([FF, "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return bool(re.search(r"Stream #\d+:\d+.*: Audio:", r.stderr or ""))


def normalize_video(video_in, out, loudnorm=True):
    """Привести громкость ГОТОВОГО ролика к LOUD_I, видео не трогая (-c:v copy).

    Для путей, где музыку кладёт не этот модуль (свой звук исходника, говорящая
    голова): чтобы в ленте не было ролика вдвое тише соседнего. Если дорожки
    нет — честно возвращаем False, вызывающий решает сам.
    """
    if not has_audio(video_in):
        return False
    af = _loudnorm_af("", video_in) if loudnorm else "aresample=48000"
    _run([FF, "-y", "-loglevel", "error", "-i", str(video_in),
          "-map", "0:v:0", "-map", "0:a:0", "-af", af,
          "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
          "-movflags", "+faststart", str(out)])
    return True


def _probe_dur(path):
    import cv2
    c = cv2.VideoCapture(path)
    fps = c.get(cv2.CAP_PROP_FPS) or 0
    n = c.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    c.release()
    return n / fps if fps else 0.0


# ── СВОЙ БАНК МУЗЫКИ (решение Валерии 20.07) ─────────────────────────────────
# Валерия сама скачала треки с Pixabay и сложила в music\. Берём случайный, как
# фото из банка. Pixabay Content License разрешает коммерческое использование и
# НЕ требует атрибуции — поэтому first_comment со строкой «Музыка: ... CC BY»
# здесь НЕ нужен, в отличие от пути через Openverse/Jamendo.
#
# ЧТО ЭТО НЕ РЕШАЕТ, И ВАЛЕРИЯ ЭТО ПОДТВЕРДИЛА: вшитый трек не становится
# нативным звуком Instagram. Кликабельной страницы аудио не будет, притока с
# тренда — тоже. Это осознанный размен: Audio API застрял на верификации
# Facebook-разработчика (см. ЦЕЛЬ 5B), а ролики нужны сейчас.
BANK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")

AUDIO_EXT = (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac")


def bank_tracks(folder=BANK):
    """Все треки своего банка, отсортированные (порядок стабилен между запусками)."""
    if not os.path.isdir(folder):
        raise RuntimeError("нет папки с музыкой: %s" % folder)
    fs = sorted(f for f in os.listdir(folder)
                if f.lower().endswith(AUDIO_EXT))
    if not fs:
        raise RuntimeError(
            "в %s нет аудиофайлов (ищу %s)" % (folder, ", ".join(AUDIO_EXT)))
    return [os.path.join(folder, f) for f in fs]


# ── ЧТОБЫ В ЛЕНТЕ НЕ ПОВТОРЯЛСЯ ТРЕК ─────────────────────────────────────────
# ЗАЧЕМ. Выбор случайным был честным, но С ВОЗВРАЩЕНИЕМ: банк из 52 треков,
# и на партии из 10 роликов вероятность, что хотя бы два получат один трек, —
# около 58% (парадокс дней рождения). Это не теория: r33 и r35 в out_reels\
# имеют ПОБИТОВО ОДИНАКОВЫЙ первый звук (md5 первых 5 секунд совпадает) —
# один трек, и он же с той же секунды, потому что good_start детерминирован.
# Лечим двумя ходами сразу: помним последние выданные треки и не повторяем их,
# а стартовую секунду выбираем из нескольких подходящих, а не всегда первую.
RECENT = os.path.join(CACHE, "recent_tracks.json")
RECENT_KEEP = 12        # столько последних треков считаем «занятыми»


def recent_tracks(path=RECENT):
    """Имена треков последних роликов, свежие в конце. Нет файла — пусто."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            got = json.load(f)
        return [str(x) for x in got][-RECENT_KEEP * 4:]
    except (OSError, ValueError):
        return []


def remember_track(name, path=RECENT, keep=RECENT_KEEP):
    """Запомнить выданный трек. Не смогли записать — молчим: это подсказка для
    следующего ролика, а не данные, ради которых стоит ронять рендер."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        got = recent_tracks(path)
        got.append(os.path.basename(name))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(got[-keep:], f, ensure_ascii=False)
    except OSError:
        pass


def _choose(tracks, avoid, rnd):
    """Случайный трек мимо avoid. Если мимо не осталось — берём из всех:
    пустой банк хуже повтора."""
    avoid = {os.path.basename(a) for a in (avoid or ())}
    pool = [t for t in tracks if os.path.basename(t) not in avoid]
    return rnd.choice(pool or tracks)


def pick_bank(folder=BANK, seed=None, avoid=None):
    """Случайный трек из банка. seed — чтобы повторить тот же выбор при отладке.
    avoid — имена, которые сейчас брать не надо (недавно уже звучали)."""
    return _choose(bank_tracks(folder), avoid, random.Random(seed))


def mean_db(path, dur=None, start=0.0):
    """Средняя громкость куска в дБ (ffmpeg volumedetect). None — не измерилось."""
    args = [FF, "-hide_banner"]
    if start:
        args += ["-ss", "%.2f" % start]
    if dur:
        args += ["-t", "%.2f" % dur]
    args += ["-i", path, "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    return float(m.group(1)) if m else None


# Насколько кусок может быть тише среднего по треку, чтобы считаться «в полную
# силу». 1.5 дБ — запас на естественную неровность музыки.
LOUD_TOL = 1.5
SCAN_STEP = 5.0        # шаг поиска стартовой секунды
SCAN_LIMIT = 45.0      # дальше 45 секунд не лезем: у стоковых треков к этому
                       # моменту основная часть уже давно идёт


#: Сколько подходящих стартов набрать, прежде чем выбирать. Больше — дороже:
#: каждый кандидат стоит отдельного прохода ffmpeg.
SCAN_PICKS = 4


def good_start(path, dur, tol=LOUD_TOL, rnd=None):
    """С какой секунды брать трек, чтобы ролик не попал в тихое вступление.

    ЗАЧЕМ. Замер банка 20.07 (`music_check.py`): у 3 треков из 10 первые 14
    секунд тише среднего по треку на 5.5-6.7 дБ. Брать всегда с нуля значит с
    вероятностью примерно треть выпустить ролик, который звучит пусто. Слышно
    это не сразу, а замером видно однозначно — поэтому решает замер.

    КАК. Идём по треку с шагом SCAN_STEP и собираем куски длиной dur, которые не
    тише среднего по треку больше чем на tol. Дальше кульминации в середине не
    лезем (SCAN_LIMIT) — начинать ролик с неё незачем. Если подходящего нет
    вовсе — возвращаем самый громкий из виденных.

    ПОЧЕМУ НЕ ВСЕГДА ПЕРВЫЙ (изменено 22.08). Первый подходящий — это почти
    всегда одна и та же секунда для одного трека, и повторно выпавший трек
    звучал БУКВАЛЬНО тем же куском. Берём случайный из первых SCAN_PICKS
    подходящих: вступление по-прежнему не попадает, а фрагмент разный.
    rnd=None — прежнее поведение, ровно первый подходящий (нужно тестам и
    отладке с фиксированным seed).
    """
    full = mean_db(path)
    total = _probe_audio_dur(path)
    if full is None or not total:
        return 0.0
    limit = max(0.0, min(SCAN_LIMIT, total - dur))
    ok = []
    best, best_db = 0.0, None
    t = 0.0
    while t <= limit + 1e-6:
        db = mean_db(path, dur, t)
        if db is not None:
            if db >= full - tol:
                if rnd is None:
                    return t
                ok.append(t)
                if len(ok) >= SCAN_PICKS:
                    break
            if best_db is None or db > best_db:
                best, best_db = t, db
        t += SCAN_STEP
    return rnd.choice(ok) if ok else best


def _probe_audio_dur(path):
    """Длительность аудио. ffprobe в комплект imageio-ffmpeg не входит, поэтому
    парсим stderr самого ffmpeg — тот же приём, что в проекте уже используется."""
    r = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr or "")
    if not m:
        return 0.0
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def score_local(video_in, out, folder=BANK, seed=None, avoid=None,
                remember=True, **kw):
    """Взять трек из своей папки и вшить вместо звука исходника.

    Аналог score(), но без сети: ничего не ищем и не качаем.
    Возвращает тот же по форме словарь, чтобы вызывающий код не различал пути.
    Атрибуции нет намеренно — Pixabay её не требует (см. комментарий к BANK).

    ПАМЯТЬ О ПРЕДЫДУЩИХ РОЛИКАХ. По умолчанию трек выбирается мимо последних
    RECENT_KEEP выданных (файл RECENT) — иначе подряд идущие ролики регулярно
    получают один и тот же трек. Память общая для всех запусков, поэтому
    работает и когда каждый ролик собирается отдельным процессом (build_r*.py).

    seed задан — память НЕ трогаем ни на чтение, ни на запись: смысл seed в
    повторяемости, а история сделала бы результат зависящим от прошлых прогонов.
    """
    reproducible = seed is not None
    if avoid is None:
        avoid = () if reproducible else recent_tracks()
    rnd = random.Random(seed)
    p = _choose(bank_tracks(folder), avoid, rnd)
    # Стартовую секунду не берём нулём вслепую — ищем замером (см. good_start).
    # Если вызывающий задал start сам, уважаем его.
    if "start" not in kw:
        kw["start"] = good_start(p, _probe_dur(video_in), rnd=rnd)
    add_music(video_in, p, out, **kw)
    if remember and not reproducible:
        remember_track(p)
    return dict(file=p, out=out, title=os.path.basename(p), start=kw["start"],
                author=None, license="pixabay", license_version=None,
                attribution=None)


def score_choices(video_in, query, out_dir, n=3, **kw):
    """N ГОТОВЫХ вариантов ролика — каждый со своим треком Openverse — чтобы
    человек выбрал, какой публиковать, а не движок решал за него один раз.

    Дедуп по (название, автор): Openverse иногда отдаёт один трек дважды под
    разными id (разный битрейт/формат) — без дедупа человек увидел бы «выбор»
    из одинаковых треков.

    Возвращает список словарей той же формы, что score(), плюс "n" — номер
    варианта (1-based, соответствует имени файла) и "out" — готовый файл.
    Пустой список — не ошибка, вызывающий сам решает, что делать (например,
    предложить банк как запасной путь).
    """
    tracks = search(query, limit=max(n * 4, 20), **{
        k: v for k, v in kw.items() if k in ("license", "source", "min_sec", "max_sec")})
    seen = set()
    picked = []
    for t in tracks:
        key = (t.get("title"), t.get("author"))
        if key in seen:
            continue
        seen.add(key)
        picked.append(t)
        if len(picked) >= n:
            break
    if not picked:
        return []

    os.makedirs(out_dir, exist_ok=True)
    add_kw = {k: v for k, v in kw.items()
             if k in ("start", "fade", "gain")}
    results = []
    for i, t in enumerate(picked, 1):
        p = download(t)
        out = os.path.join(out_dir, "вариант_%d.mp4" % i)
        add_music(video_in, p, out, **add_kw)
        row = dict(t)
        row.update(n=i, file=p, out=out, attribution=attribution(t))
        results.append(row)
    return results


def score(video_in, query, out, pick=0, **kw):
    """Найти → скачать → положить. Возвращает трек + готовую строку атрибуции."""
    tracks = search(query)
    if not tracks:
        raise RuntimeError(
            "по запросу %r музыки не нашлось. Пробуй ОДНО слово: условия в "
            "Openverse идут по И, и 'cinematic corporate' даёт ноль." % query)
    t = tracks[pick]
    p = download(t)
    add_music(video_in, p, out, **kw)
    t["file"] = p
    t["out"] = out
    t["attribution"] = attribution(t)
    return t
