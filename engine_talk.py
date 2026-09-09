# -*- coding: utf-8 -*-
"""
ДВИЖОК ГОВОРЯЩЕГО ВИДЕО (третий поток контент-завода).

Отличие от engine_video.py: там крючок висит одним статичным слоем всю длину.
Здесь речь распознана (faster-whisper, transcript.json с таймингами по словам),
и на её основе:
  • СУБТИТРЫ по 2–3 слова (R7+R8, разрыв по знакам препинания), синхронно с
    речью (сердце формата);
  • СМЫСЛОВЫЕ ВСТАВКИ по времени: типографская плашка (v1) или b-roll (v2);
  • ДВА ФОРМАТА композиции, чередуются между роликами:
      A — говорящая голова маленькой в углу (PIP), вставка на весь экран;
      B — голова крупно на весь кадр, вставка всплывает снизу.

Рендер покадровый (cv2 читает → PIL рисует → cv2 пишет), потом ffmpeg
подмешивает исходный звук и жмёт в h264/yuv420p под Instagram. Полный контроль
над каждым пикселем — субтитры точь-в-точь в стиле каруселей (Onest + акцент).

ЗАМЕР, НЕ ВЫВОД: перед показом Валерии из готового файла вынимаются кадры и
смотрятся глазами (checks/контактный лист) — код рапортует успех даже когда
текст лёг на лицо или уехал за кадр.

v1: вставки только типографские (не требуют внешних файлов). B-roll — следующий слой.
"""
import os
import subprocess
import tempfile
import time
import json

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import imageio_ffmpeg

import engine4 as e
import style as style_mod          # R30: слой визуального стиля клиента

FF = imageio_ffmpeg.get_ffmpeg_exe()
HERE = os.path.dirname(os.path.abspath(__file__))

#: Пресет x264 для ВСЕХ энкодов движка (P2). Был "medium". Замер на живом
#: ролике 1080×1920 / 57 сек (M1, тот же crf 18): medium 26.1 сек / 36.9 МБ,
#: veryfast 10.0 сек / 32.0 МБ — то есть veryfast и в 2.6 раза быстрее, И даёт
#: файл МЕНЬШЕ. ultrafast быстрее ещё втрое (4.0 сек), но файл раздувается до
#: 81 МБ — для площадок это хуже, поэтому взят veryfast, а не он.
#: Переопределяется переменной окружения на случай, если понадобится вернуть
#: прежнее поведение без правки кода.
ENC_PRESET = os.environ.get("KARUSEL_X264_PRESET", "veryfast")

#: Качество финального энкода (crf x264): меньше — лучше и тяжелее. 18 —
#: прежнее зашитое значение, вынесено в константу, чтобы можно было снять
#: эталон без потерь (crf 0) и честно сравнить пути кодирования замером.
ENC_CRF = os.environ.get("KARUSEL_X264_CRF", "18")


def _src_rotation(path):
    """Угол поворота из метаданных видео (флаг iPhone), 0/90/180/270.

    ⚠️ ПОЙМАНО НА ОБКАТКЕ 28.07: телефон снимает вертикально, но хранит кадр
    как 1920×1080 с флагом rotation=-90. B-roll этого не касается: он идёт
    через ffmpeg (`prepare_broll`), а тот поворот применяет сам. Поэтому чиним
    только поток спикера, читаемый cv2 напрямую.

    ⚠️ ЭТА ФУНКЦИЯ ГОВОРИТ, ЧТО НАПИСАНО В ФАЙЛЕ, А НЕ СКОЛЬКО ДОВОРАЧИВАТЬ.
    Раньше здесь стояло «`cv2.VideoCapture` этот флаг ИГНОРИРУЕТ»: для OpenCV
    4.11 правда, для 5.0.0 — нет, она разворачивает кадр сама, и на этой
    устаревшей посылке движок разворачивал второй раз (E4-63). Угол к доворо́ту
    считает `_open_src` — он же и открывает поток. Вызывать `_src_rotation`
    напрямую перед `_apply_rotation` НЕЛЬЗЯ.
    """
    import re
    import shutil
    # ffprobe надёжнее, но imageio даёт только ffmpeg, а на Windows-машинах
    # ffprobe может не стоять — поэтому он лишь ПЕРВАЯ попытка, а основной путь —
    # разбор вывода самого ffmpeg (-i), который есть у всех через imageio.
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        for ent in ("stream_side_data=rotation", "stream_tags=rotate"):
            try:
                out = subprocess.run(
                    [ffprobe, "-v", "0", "-select_streams", "v:0",
                     "-show_entries", ent, "-of", "default=nw=1:nk=1", path],
                    capture_output=True, text=True, timeout=30
                ).stdout.strip().split("\n")[0].strip()
                if out:
                    return int(round(float(out))) % 360
            except Exception:
                pass
    # запасной путь: ffmpeg -i печатает в stderr «displaymatrix: rotation of -90.00
    # degrees» и/или «rotate : 270»
    try:
        err = subprocess.run([FF, "-i", path], capture_output=True, text=True,
                             timeout=30).stderr
        m = re.search(r"rotation of ([\-\d.]+) degrees", err)
        if m:
            return int(round(float(m.group(1)))) % 360
        m = re.search(r"rotate\s*:\s*(\d+)", err)
        if m:
            return int(m.group(1)) % 360
    except Exception:
        pass
    return 0


def _apply_rotation(frame, rot):
    """Повернуть кадр по флагу так, как его показал бы плеер.

    Направление проверено кадром на обкатке 28.07: для iPhone-флага
    rotation=-90 (нормализуется в 270) правильный разворот — ПО ЧАСОВОЙ;
    против часовой давал спикера вверх ногами.
    """
    if rot in (270, -90):
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if rot in (90, -270):
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if rot in (180, -180):
        return cv2.rotate(frame, cv2.ROTATE_180)
    return frame


def _src_coded_size(path):
    """Размер кадра КАК ЗАПИСАН в файле — без учёта флага поворота.

    Нужен, чтобы поймать ФАКТ: если cv2 отдал кадр с перевёрнутыми сторонами
    относительно записанных, значит эта сборка библиотеки применила флаг сама.
    Путь тот же, что у `_src_rotation`: сначала ffprobe, потом разбор вывода
    ffmpeg -i, потому что на Windows-машинах ffprobe может не стоять.
    """
    import re
    import shutil
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "0", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=s=x:p=0", path],
                capture_output=True, text=True, timeout=30
            ).stdout.strip().split("\n")[0].strip()
            m = re.match(r"^(\d+)x(\d+)$", out)
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
    try:
        err = subprocess.run([FF, "-i", path], capture_output=True, text=True,
                             timeout=30).stderr
        m = re.search(r"Video:.*?[,\s](\d{2,5})x(\d{2,5})", err)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None


def _no_auto_orient(cap):
    """Попросить cv2 НЕ разворачивать кадр самостоятельно.

    `CAP_PROP_ORIENTATION_AUTO` появился в OpenCV 4.5; на сборках без него
    вызов просто не имеет эффекта — поэтому это лишь первая линия, а решает
    проверка фактом в `_cv_already_rotated`.
    """
    auto = getattr(cv2, "CAP_PROP_ORIENTATION_AUTO", None)
    if auto is None:
        return cap
    try:
        cap.set(auto, 0)
    except Exception:
        pass
    return cap


def _cv_already_rotated(path):
    """Отдаёт ли эта сборка cv2 кадр УЖЕ развёрнутым по флагу поворота.

    Смотрим на форму настоящего кадра, а не на свойства капчера: свойства
    backend считает по-своему, а `frame.shape` не врёт. Пробуем отдельным
    капчером, чтобы не тратить первый кадр рабочего потока.
    """
    coded = _src_coded_size(path)
    if not coded or coded[0] == coded[1]:
        return False                      # без записанных сторон судить нечем
    probe = _no_auto_orient(cv2.VideoCapture(path))
    try:
        ok, fr = probe.read()
    finally:
        probe.release()
    if not ok or fr is None:
        return False
    return (fr.shape[1], fr.shape[0]) == (coded[1], coded[0])


def _open_src(path):
    """Открыть видео спикера: вернуть (cap, rot) — угол, который ОСТАЛОСЬ довернуть.

    ⚠️ E4-63, ПОЙМАНО 02.09.2026. Ручной доворот держался на том, что cv2 флаг
    поворота ИГНОРИРУЕТ — так вела себя OpenCV 4.11, и на этой посылке написан
    весь путь `_src_rotation`/`_apply_rotation`. На OpenCV 5.0.0 кадр приходит
    уже развёрнутым: движок разворачивал его ВТОРОЙ раз и молча укладывал
    спикера на бок — без единой ошибки в логе.

    Поэтому здесь два шага, и важен именно второй: сначала просим библиотеку не
    разворачивать, а затем ПРОВЕРЯЕМ ФАКТОМ, послушалась ли она. Проверка по
    факту, а не по номеру версии, остаётся верной на любой будущей сборке —
    ровно этого требует сторож `test_rotation_contract.py`.
    """
    cap = _no_auto_orient(cv2.VideoCapture(path))
    rot = _src_rotation(path)
    if rot and _cv_already_rotated(path):
        rot = 0                           # библиотека развернула сама
    return cap, rot

VW, VH = 1080, 1920                 # Reels 9:16

# Безопасная зона Instagram — те же значения, что в engine_video.py: снизу ник,
# подпись, музыка, кнопки; сверху шапка; справа колонка действий.
SAFE_TOP = 170
SAFE_BOTTOM = 430
SAFE_RIGHT = 150
M = 72

# A4 (позиция субтитра ↔ лицо спикера): зазор между низом субтитра и макушкой
# лица при подъёме блока вверх. Доля высоты силуэта, считаемая «головой+лицом»,
# когда точной рамки лица нет (fallback по альфе talk_matte). Тумблер для
# само-baseline: KARUSEL_SUB_AVOID=0 → прежнее поведение байт в байт (для «до»).
SUB_FACE_GAP = 24
SUB_HEAD_FRAC = 0.33
SUB_FACE_INFLATE = 0.18            # запас рамки лица на движение головы в реплике
# ЗАМЕР 28.08.2026: пробовал 0.35 под правило «никогда не на лице» — стало
# ХУЖЕ (8 реплик из 45 против 6). Больший запас чаще запускает уклонение, а на
# полнокадровой вставке чистой полосы часто нет вовсе, и блок уезжает в ещё
# худшее место. Оставлено 0.18. Настоящее лекарство — не пускать в
# ПОЛНОКАДРОВУЮ вставку клипы с лицами (см. talk_broll, отбор под card).

# ПОЗИЦИЯ СУБТИТРА: АНКОР К НИЗУ БЕЗОПАСНОЙ ЗОНЫ, А НЕ ФИКСИРОВАННЫЙ ЦЕНТР.
#
# Замер (канонический клип регрессии, раскладка card): рамка субтитра 842..1490
# при высоте кадра 1920. Разбор чисел:
#   • style.sub_cy_overrides["default"] = 1200 — это ЦЕНТР строки, 62.5% высоты.
#     Именно эти «62%» и ходят по плану перестройки;
#   • но центр — не то, что видит зритель. Видно РАССТОЯНИЕ ДО ИНТЕРФЕЙСА:
#     низ блока против границы 1490 = VH-SAFE_BOTTOM = 77.6% высоты. Ниже неё
#     ник, подпись, музыка и кнопки Instagram (обоснование самой границы — в
#     engine_video.py, раздел «БЕЗОПАСНАЯ ЗОНА REELS»);
#   • при фиксированном центре это расстояние ПЛАВАЕТ вместе с кеглем, который
#     завод подбирает сам под длину реплики. Замеры на том же стиле: блок
#     высотой 131 px (кегль 53) даёт низ на 1265 — 225 px пустоты до интерфейса;
#     519 px (кегль 97) — низ 1459; 648 px (кегль 146) — низ 1524, то есть УЖЕ
#     за границей, и его спасала аварийная подрезка. Один и тот же «62%» на
#     практике означал что угодно от 66% до 79%.
#
# Поэтому фиксируем не центр, а НИЗ блока: он встаёт на границу безопасной зоны
# с отбивкой SUB_BOTTOM_GAP и растёт вверх. Субтитр всегда стоит настолько
# низко, насколько Reels вообще позволяет — и ни пикселем ниже.
#
# ЦИФРА ИЗ ПЛАНА «78-82%» НЕДОСТИЖИМА КАК ЦЕНТР и брать её буквально нельзя:
# центр на 0.80×1920 = 1536 утащил бы даже однострочный блок низом на ~1650 —
# на 160 px ПОД ник и кнопки, где текст не читают вовсе. Достижимый максимум по
# низу блока — 1470 (76.6%), он и берётся.
#
# Отбивка 20 px — не новое число: ровно её уже держит ×N-путь demo/compare
# ("VH - SAFE_BOTTOM - 20" ниже по коду), теперь у обоих путей она общая.
SUB_BOTTOM_GAP = 20                # отбивка низа субтитра от границы зоны

#: Раскладки, где под субтитром НИЧЕГО нет (полный кадр) — их и прижимаем к низу.
#: Остальные не трогаем, у каждой своя причина стоять именно там, где стоит:
#: fmt_A (pip) — окно спикера в правом нижнем углу, есть явная проверка
#: пересечения; split — у шва, в половине спикера; lower-third — над плашкой-
#: лентой LT_Y=1235; compare — под швом стопки клипов; demo/compare идут ×N-путём
#: и УЖЕ прижаты к низу. `None` — это card и обычный формат B (template
#: обнуляется выше при разборе имени раскладки).
SUB_BOTTOM_ANCHORED = (None, "cutout", "text-behind")

INK = e.INK                        # единый «чёрный» завода
# E4-49: ЕДИНЫЙ белый субтитра. До этого белых было два и выбирал их путь
# отрисовки, а не решение: караоке рисовало неактивные слова чистым 255
# (style.karaoke.fill), split — хардкодом 255 (:1583), остальные раскладки без
# караоке — 245 (style.sub_style.fill). Замер: караоке включено по умолчанию у
# всех шести говорящих раскладок (render_reels.KARAOKE_DEFAULT_TEMPLATES), плюс
# split хардкодит 255 — то есть 255 и был фактическим белым завода, а 245
# доставался только demo/compare и прогонам с --no-karaoke. Сводим на 255:
# контраст субтитра при этом РАСТЁТ (замер — docs/journals/E4-49-style.md §7),
# а меняются только те два формата, а не все караоке-ролики.
WHITE = (255, 255, 255)

# Плашка-лента формата 5 (lower-third): спикер живой на весь кадр, снизу поверх
# него — аккуратная плашка с числом/тезисом («136 точек», «+500 000 ₽»). НЕ
# полноэкранная перебивка. Разнесена по вертикали с субтитром, ширина держит
# правый край ЛЕВЕЕ колонки кнопок Instagram (SAFE_RIGHT), низ выше SAFE_BOTTOM.
# Сменяет прежнюю мёртвую карточку CARDB_* формата B (в рендер-цикле не рисовалась).
#
# ЗАМЕР 2x (владелец, 2026-08-16): при старом LT_W=760/LT_H=160 автоподбор
# _lower_third упирался в стартовый size=104 на обоих канонических примерах
# («136 точек», «+500 000 ₽») — то есть 104 и был фактическим итоговым кеглем.
# Честный ЦЕНТРИРОВАННЫЙ рост плашки внутри безопасной зоны (правый край < 930)
# физически даёт максимум LT_W≈772 — это лишь ~1.2-1.45× кегля, а не 2×: ширина
# упирается в SAFE_RIGHT, а не в произвольный лимит. Левее плашки безопасная
# зона ничем не ограничена (SAFE_RIGHT — только про правую колонку кнопок), а
# правый край здесь НЕ двигаем (x1 остаётся 920, тот же, что и раньше) — поэтому
# лишний запас взят СЛЕВА, от общего поля разметки M=72 (та же константа, что у
# прогресс-бара :1406), а не центрированием. Итог — LT_W=848 (x=72..920, как и
# раньше <930), плюс уменьшенные внутренние поля/ослабленное условие высоты в
# _lower_third (см. её докстрок) добирают кегль до ~1.3-1.6× от 104 — это потолок,
# дальше упирается либо в SAFE_RIGHT (ширина), либо в субтитр/SAFE_BOTTOM (высота).
LT_W, LT_H = 848, 210
LT_X, LT_Y = M, 1235                     # x=72..920 (<930), y=1235..1445 (<1490)

# Раскладка "music" (P4, 16.08.2026): статичная подпись хук+призыв поверх
# видео-эстетики без речи, под музыку — референс владельца IMG_4731.MP4
# (Reels-редактор третьей стороны). Геометрия СНЯТА ЗАМЕРОМ с референса, не на
# глаз: яркостной скан текстовых строк на кадре 720×1280 дал ink-высоту строки
# хука ≈29px (среднее по 3 строкам) и подписи-призыва ≈13px — отношение 0.43-
# 0.45, не "просто мельче". Блок текста в референсе держится 62-77% высоты
# кадра — заведомо выше SAFE_BOTTOM (безопасная зона листайте ниже), с запасом
# от нижнего края под элементы Instagram. MUSIC_HOOK_SIZE — стартовый кегль
# автоподбора (шрифт садится, если 3 строки не влезают в SAFE_W), откалиброван
# так, что типичный хук книги смыслов (5-9 слов) укладывается в 2-3 строки с
# итоговой ink-высотой строки в районе замеренных ~29px на 720-кадре (≈43px в
# боевом 1080-канве движка).
MUSIC_HOOK_SIZE = 78
MUSIC_SUB_RATIO = 0.45                   # кегль призыва = MUSIC_HOOK_SIZE × это
MUSIC_GAP = 22                           # зазор между хуком и призывом, px
MUSIC_BLOCK_BOTTOM = 1465                # нижний край блока (подпись-призыв)


# ── СУБТИТРЫ: слова → реплики до 3 слов (R7+R8+R42) ─────────────────────────
_PUNCT = ".,!?…:;—"

# T-loud/2: НЕПРЕРЫВНАЯ ДОРОЖКА СУБТИТРОВ — потолок «додержания» реплики, сек.
#
# ЗАЧЕМ. Реплика жила РОВНО на словах (t1 = конец последнего слова), поэтому в
# каждой межрепличной паузе субтитр ПРОПАДАЛ и появлялся снова — текст мигал.
# Это не редкость на краю: замер на реальном транскрипте завода
# (out_talk/IMG_8138_transcript.json, 57 с речи) — 62 реплики и 61 пауза между
# ними, ВСЕ ненулевые: медиана 0.04 с, p95 0.08 с, максимум 0.41 с. То есть
# дорожка гасла 61 раз кусками по 1–2 кадра при 30 fps — ровно то мигание,
# которое видно глазом и не видно в метриках. Продолжение R7+R8 («реплика живёт
# дольше, текст мигает реже»), только теперь не за счёт укрупнения реплики, а за
# счёт додержания её до старта следующей.
#
# ПОЧЕМУ ПОТОЛОК, А НЕ «ТЯНУТЬ ВСЕГДА». Пауза бывает двух разных природ.
# Дыхание между синтагмами — там текст обязан оставаться, иначе мигание. Конец
# мысли / монтажная пауза — там держать прошлую фразу вредно: она виснет поверх
# новой картинки (в этих же секундах обычно и стоит b-roll-вставка) и врёт
# зрителю. Разделяем порогом.
#
# ПОЧЕМУ 1.2 с. Замер выше: 100% реальных пауз ≤0.41 с, то есть при пороге 1.2 с
# дорожка на живой речи становится СПЛОШНОЙ целиком (закрыто 2.7 с из 2.7 с
# пауз, ни одного обрезанного хвоста). Запас втрое от максимума наблюдённой
# паузы взят не «на глаз»: он покрывает и более рваную речь, но всё ещё заведомо
# короче тех пауз, ради которых порог и заводится (пауза «конец мысли» на
# просмотре роликов завода — секунды, а не десятые). Порог ловит именно длинный
# хвост, а не разброс дыхания.
#
# Тумблер KARUSEL_SUB_HOLD: секунды (0 — выключить додержание, поведение
# байт-в-байт как до правки; полезно для само-baseline и отката).
SUB_HOLD_MAX = 1.2


def _hold_cues(cues, max_hold=None):
    """Додержать каждую реплику до старта следующей (потолок SUB_HOLD_MAX).

    Меняется ТОЛЬКО `t1` реплики — сколько она висит на экране. Пословные
    тайминги караоке (`cue["words"]`, поля s/e) НЕ ТРОГАЮТСЯ ВООБЩЕ: подсветка
    ездит по ним (render: активное слово = последнее, чей старт уже прошёл),
    поэтому слова кончаются ровно там же, где кончались, а строка просто
    остаётся на экране — на додержанном хвосте подсветка стоит на последнем
    слове, как она уже стояла во внутрирепличных паузах.

    Последняя реплика не трогается: тянуть её некуда (следующей нет), а длину
    ролика этот слой не знает и знать не должен.
    """
    if max_hold is None:
        env = os.environ.get("KARUSEL_SUB_HOLD")
        max_hold = SUB_HOLD_MAX if env is None else float(env)
    if max_hold <= 0 or len(cues) < 2:
        return cues
    for cur, nxt in zip(cues, cues[1:]):
        # не заезжаем на следующую реплику и не сокращаем текущую
        cur["t1"] = max(cur["t1"], min(nxt["t0"], cur["t1"] + max_hold))
    return cues


def _phrase_to_cues(phrase, cues, max_words, max_chars, keep_words=False):
    """Разбить ФРАЗУ (слова до знака препинания) на реплики: жадно по max_words
    и max_chars, но без слова-сироты в конце — если после жадного куска остаётся
    ровно одно слово, кусок укорачивается (3+1 → 2+2). Слово длиннее max_chars
    уходит своей репликой (кегль ужимается автоподбором выше по стеку).

    keep_words=True — караоке: в реплику кладётся ещё и список слов с пословными
    таймингами (по ним ездит подсветка). Разбиение при этом ТО ЖЕ САМОЕ, что у
    обычных субтитров: караоке — дефолтный путь завода, и без общей логики
    правила R7/R8/R42 на него бы не распространились (находка критика)."""
    i, n = 0, len(phrase)
    while i < n:
        k = min(max_words, n - i)
        if n - i - k == 1 and k > 1:
            k -= 1                              # не оставлять сироту
        while k > 1 and len(" ".join(w["w"] for w in phrase[i:i + k])) > max_chars:
            k -= 1
        chunk = phrase[i:i + k]
        cue = {"text": " ".join(w["w"] for w in chunk),
               "t0": chunk[0]["s"], "t1": chunk[-1]["e"]}
        if keep_words:
            cue["words"] = [dict(w) for w in chunk]
        cues.append(cue)
        i += k


def chunk_cues(segments, max_words=3, max_chars=24):
    """Слова из транскрипта → реплики до 3 слов / 24 символов (R7+R8: против
    прежних 1–2 слов / 15 символов — реплика живёт дольше, текст мигает реже).

    R42: приоритет разрыва — ПО ЗНАКАМ ПРЕПИНАНИЯ. Слова копятся во фразу до
    знака (запятая/точка/…), и режется на реплики ФРАЗА ЦЕЛИКОМ — граница
    реплики не рвёт словосочетание посреди синтагмы, как рвал прежний буфер,
    и последняя реплика фразы не остаётся словом-сиротой."""
    cues, phrase = [], []
    for seg in segments:
        for w in seg.get("words", []):
            word = w["w"].strip()
            if not word:
                continue
            phrase.append({"w": word, "s": w["s"], "e": w["e"]})
            if word[-1] in _PUNCT:
                _phrase_to_cues(phrase, cues, max_words, max_chars)
                phrase = []
    if phrase:
        _phrase_to_cues(phrase, cues, max_words, max_chars)
    # T-loud/2: дорожка без дыр — реплика висит до старта следующей (см. _hold_cues)
    return _hold_cues(cues)


def chunk_cues_karaoke(segments, max_words=4, max_chars=26):
    """Караоке-режим: слова → СТРОКИ, но с СОХРАНЕНИЕМ пословных таймингов
    (w['s']/w['e'] из WhisperX): подсветка ездит по словам внутри строки — это и
    есть «караоке». Строка чуть длиннее обычной реплики (4 слова против 3): она
    и живёт дольше, потому что читается по подсветке, а не целиком.

    Разбиение — ОБЩЕЕ с chunk_cues (R42): слова копятся во фразу до знака
    препинания, фраза режется целиком, сироты не остаются. Прежний буфер рвал
    строку посреди синтагмы; после того как караоке стало ДЕФОЛТОМ завода (R9),
    эта разница определяла вид почти всех роликов.

    Каждая реплика: {text, t0, t1, words:[{w,s,e}, ...]}.
    """
    cues, phrase = [], []
    last_e = 0.0
    for seg in segments:
        for w in seg.get("words", []):
            word = w["w"].strip()
            if not word:
                continue
            # тайминги слова: WhisperX даёт s/e; если их нет — тянем по соседям,
            # чтобы подсветка не падала (край сегмента как запасной вариант).
            s = w.get("s", phrase[-1]["e"] if phrase else
                      (last_e or seg.get("end", 0.0)))
            en = w.get("e", s)
            last_e = en
            phrase.append({"w": word, "s": s, "e": en})
            if word[-1] in _PUNCT:
                _phrase_to_cues(phrase, cues, max_words, max_chars,
                                keep_words=True)
                phrase = []
    if phrase:
        _phrase_to_cues(phrase, cues, max_words, max_chars, keep_words=True)
    # T-loud/2: то же додержание, что и у обычных субтитров. Пословные тайминги
    # (words[i]["s"]/["e"]) остаются нетронутыми — подсветка не растягивается.
    return _hold_cues(cues)


def cues_from_text(raw, dur, karaoke=False):
    """СУБТИТРЫ ИЗ ЗАДАННОГО ТЕКСТА (E4-24, вход-агностик): когда речи нет и
    транскрипта тоже (демонстрация/процесс-клип). Каждая непустая строка —
    отдельная подпись. Тайминги:
      • строка вида `[t0-t1] текст` или `[t0] текст` — явные метки в секундах
        (t1 у формы `[t0]` тянется до старта следующей метки / до конца);
      • строки без меток — распределяются РАВНОМЕРНО по длине `dur` (в промежутках
        между явными метками, если те есть; иначе по всей длине).
    Возвращает cues в том же формате, что chunk_cues (+ `words` при karaoke —
    слова строки с равномерными суб-таймингами, чтобы подсветка ездила и без
    WhisperX). Пустой текст → []. Полностью аддитивно: старый путь (transcript)
    эту функцию не вызывает."""
    import re
    if isinstance(raw, (list, tuple)):
        lines = [str(x).strip() for x in raw if str(x).strip()]
    else:
        lines = [ln.strip() for ln in str(raw).splitlines() if ln.strip()]
    if not lines:
        return []
    dur = float(dur) if dur and dur > 0 else float(len(lines))

    mark = re.compile(r"^\[\s*([\d.]+)\s*(?:-\s*([\d.]+)\s*)?\]\s*(.*)$")
    parsed = []                              # (t0|None, t1|None, text)
    any_mark = False
    for ln in lines:
        m = mark.match(ln)
        if m:
            any_mark = True
            t0 = float(m.group(1))
            t1 = float(m.group(2)) if m.group(2) else None
            parsed.append([t0, t1, m.group(3).strip()])
        else:
            parsed.append([None, None, ln])

    if not any_mark:
        # РАВНОМЕРНО по всей длине: n равных окон
        n = len(parsed)
        step = dur / n
        for i, p in enumerate(parsed):
            p[0] = i * step
            p[1] = (i + 1) * step
    else:
        # явные метки закрепляют t0; форма `[t0]` и строки без метки тянутся до
        # следующего известного старта (или до конца ролика).
        starts = [p[0] for p in parsed]
        # строки без явного t0 получают t0 линейной интерполяцией между соседними
        # известными стартами (равномерное дробление промежутка).
        i = 0
        n = len(parsed)
        # гарантируем стартовую точку
        if starts[0] is None:
            parsed[0][0] = 0.0
            starts[0] = 0.0
        while i < n:
            if starts[i] is None:
                # найти предыдущий известный старт j и следующий известный k
                j = i - 1
                k = i
                while k < n and starts[k] is None:
                    k += 1
                lo = parsed[j][0]
                hi = parsed[k][0] if k < n and starts[k] is not None else dur
                gap = k - j                    # число интервалов
                for q in range(i, k):
                    parsed[q][0] = lo + (hi - lo) * (q - j) / gap
                    starts[q] = parsed[q][0]
                i = k
            else:
                i += 1
        # t1 каждой строки — старт следующей (или конец ролика)
        for i in range(n):
            if parsed[i][1] is None:
                parsed[i][1] = parsed[i + 1][0] if i + 1 < n else dur

    cues = []
    for t0, t1, text in parsed:
        t0 = max(0.0, float(t0))
        t1 = max(t0 + 0.05, float(t1))
        cue = {"text": text, "t0": t0, "t1": t1}
        if karaoke:
            toks = text.split()
            if toks:
                span = (t1 - t0) / len(toks)
                cue["words"] = [{"w": w, "s": t0 + k * span, "e": t0 + (k + 1) * span}
                                for k, w in enumerate(toks)]
            else:
                cue["words"] = []
        cues.append(cue)
    return cues


def _sub_contrast_region(rgb):
    """E4-46: контраст субтитра в регионе ГОТОВОГО кадра (спрайт уже вклеен) →
    WCAG-ratio или None на вырожденном регионе.

    Оценка обязана совпадать с `checks.sub_contrast` — это ОДНА шкала, и пороги
    R22 (<2:1 hard, <3:1 warn) откалиброваны именно на ней: «светлая сторона» =
    среднее верхнего дециля светимости, «тёмная сторона» = медиана нижней
    половины. Прежняя метрика движка (R44) мерила фон ДО вклейки спрайта против
    условного белого и потому игнорировала собственную обводку 9 px и тень —
    те самые пиксели, которые и делают текст читаемым; расхождение доходило до
    5× (pip: 3.61 у движка против 17.93 у гейта на одном файле, B-regress P2-1).
    Прочие достоинства движка сохранены: он мерит КАЖДЫЙ кадр реплики, а гейт —
    девять на ролик, поэтому минимум движка честнее и остаётся ведущим.
    Правишь здесь — правь и checks.sub_contrast (парность проверяет regress).
    """
    a = rgb.astype(np.float32) / 255.0
    a = np.where(a <= 0.03928, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    L = (a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722).ravel()
    if L.size < 64:
        return None
    lo_half = L[L <= np.percentile(L, 50)]
    hi = float(L[L >= np.percentile(L, 90)].mean())
    lo = float(np.median(lo_half)) if lo_half.size else float(L.min())
    return (hi + 0.05) / (lo + 0.05)


def _wcag_ratio(rgb1, rgb2):
    """WCAG-контраст двух ПЛОСКИХ цветов (0..255) — для проверки читаемости
    пары «заливка плашки ↔ текст на ней» (E4-51)."""
    def _l(c):
        a = np.asarray(c, dtype=np.float32) / 255.0
        a = np.where(a <= 0.03928, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
        return float(a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722)
    l1, l2 = _l(rgb1), _l(rgb2)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


# E4-57/U9: АВТО-АКЦЕНТ ПО WCAG (доминанта фона → контраст → откат на бренд).
# Порог 3:1 — тот же, что у karaoke_plate_contrast (_plate_contrast выше):
# завод уже считает 3:1 планкой читаемости крупного графического элемента на
# произвольном фоне, отдельного числа для этой задачи не вводим.
ACCENT_CONTRAST_MIN = 3.0


def _bg_dominant_rgb(region):
    """Доминантный цвет фона в регионе кадра (ФОТО/ВИДЕО-вставка тоже сюда
    попадает — это пиксели ГОТОВОГО композита, а не заливка). Медиана по
    каналам — грубая, но устойчивая к шуму/бликам оценка «типичного» пикселя
    зоны (та же идея, что и у «тёмной половины» в _sub_contrast_region, только
    тут нужен именно ЦВЕТ, а не яркость). region — HxWx3, None/мало пикселей
    → None (вырожденная зона, решение по ней не принимаем)."""
    if region is None or region.size == 0:
        return None
    a = np.asarray(region, dtype=np.float32).reshape(-1, 3)
    if a.shape[0] < 16:
        return None
    return tuple(float(v) for v in np.median(a, axis=0))


def pick_accent(base, box, accent, family, threshold=ACCENT_CONTRAST_MIN):
    """E4-57/U9: выбрать акцент для элемента, который сейчас будет нарисован
    В ЗОНЕ box поверх base (текущий композит — фото/видео/сплошная заливка,
    ЛЮБОЙ фон, ДО отрисовки самого акцентного элемента).

    1. меряем доминанту фона в box (_bg_dominant_rgb);
    2. WCAG-контраст accent↔доминанта (_wcag_ratio, та же формула, что и
       everywhere в этом файле — E4-46/E4-51: одна шкала, не изобретаем новую);
       уже ≥ threshold или зона вырождена (не на чем измерить) → accent как
       есть, ничего не трогаем;
    3. иначе пробуем family (styles/*.json accent_family — того же бренда, не
       случайный цвет) и берём кандидата с максимальным контрастом;
    4. лучший кандидат ВСЁ РАВНО < threshold (family не спасает, в т.ч. если
       она пуста) → откат на исходный accent. Никогда не бросаем исключение и
       не возвращаем «пусто» — рендер не должен падать из-за подбора цвета.

    Возвращает (rgb: tuple, contrast_before: float|None, swapped: bool).
    """
    accent = tuple(accent)
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(base.width, x1), min(base.height, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return accent, None, False
    region = np.asarray(base.convert("RGB").crop((x0, y0, x1, y1)))
    bg = _bg_dominant_rgb(region)
    if bg is None:
        return accent, None, False
    before = _wcag_ratio(accent, bg)
    if before >= threshold or not family:
        return accent, before, False
    best_rgb, best_ratio = accent, before
    for cand in family:
        cand = tuple(cand)
        r = _wcag_ratio(cand, bg)
        if r > best_ratio:
            best_rgb, best_ratio = cand, r
    if best_ratio < threshold or best_rgb == accent:
        return accent, before, False       # ничего в семье не спасает — откат
    return best_rgb, before, True


def _draw_box(d, pad, w, h, box):
    """R32 (стиль клиента, kind='boxed'): скруглённая подложка ЗА блоком текста.
    box — dict {fill:(r,g,b), alpha, pad_x, pad_y, radius}; None → ничего не
    рисуется (дефолт движка байт-в-байт). Эталон математики —
    styles/make_previews.py::text_sprite."""
    if not box:
        return
    bx, by = box["pad_x"], box["pad_y"]
    d.rounded_rectangle([pad - bx, pad - by, w - pad + bx, h - pad + by],
                        box["radius"],
                        fill=tuple(box["fill"]) + (box["alpha"],))


def _text_sprite(text, font, fill, stroke=INK, stroke_w=9, pad=26, shadow=True,
                 shadow_dy=4, shadow_alpha=120, box=None, glow=0,
                 glow_alpha=150):
    """Тугой RGBA-спрайт строки: (опц.) подложка + заливка + (опц.) тёмная обводка
    + (опц.) тень. Обводка нужна, чтобы белый субтитр читался на любом кадре.
    В split-стиле (реф. Пример2) обводки и тени НЕТ — чистый белый шрифт
    (stroke_w=0, shadow=False).

    R30: shadow_dy/shadow_alpha/box — параметры слоя стиля клиента; дефолты
    равны прежним константам, box=None → подложки нет (байт-в-байт)."""
    probe = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(probe)
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    w, h = (x1 - x0) + pad * 2, (y1 - y0) + pad * 2
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ox, oy = pad - x0, pad - y0
    _draw_box(d, pad, w, h, box)
    if glow:
        # БЕЛОЕ СВЕЧЕНИЕ (владелец 28.08.2026): «небольшое фоновое белое
        # осветление» вокруг букв. Плашки не рисуем — вместо неё мягкое пятно
        # света ровно по форме текста: белая копия строки, размытая по Гауссу и
        # положенная ПОД буквы. Кадр сквозь неё просвечивает, а контраст растёт,
        # потому что фон под буквами становится светлее. Радиус = glow.
        g = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(g).text((ox, oy), text, font=font,
                               fill=(255, 255, 255, int(glow_alpha)),
                               stroke_width=stroke_w + max(2, glow // 3),
                               stroke_fill=(255, 255, 255, int(glow_alpha)))
        im.alpha_composite(g.filter(ImageFilter.GaussianBlur(glow)))
    if shadow:                          # мягкая тень
        d.text((ox, oy + shadow_dy), text, font=font, fill=(0, 0, 0, shadow_alpha),
               stroke_width=stroke_w, stroke_fill=(0, 0, 0, shadow_alpha))
    d.text((ox, oy), text, font=font, fill=tuple(fill) + (255,),
           stroke_width=stroke_w, stroke_fill=tuple(stroke) + (255,))
    return im


def _fix_orphan(lines, fits):
    """СИРОТА: последняя строка переноса — одно-единственное слово.

    Жадный перенос набивает верхние строки под завязку, а остаток сваливает
    вниз, поэтому у крупного субтитра (кегль ~100-150 при ширине зоны 870 px)
    сплошь и рядом выходит «И это очень / важно» — последняя строка короче
    предыдущей в разы. Замер на живом транскрипте (out_talk/IMG_8138, 56 реплик
    штатным chunk_cues_karaoke): 26 реплик многострочные, у 18 из них последняя
    строка — одно слово.

    Лечим ровно так, как это делает типографика: тащим ВНИЗ последнее слово
    предыдущей строки, чтобы сирота получила пару («И это / очень важно»).
    Заодно это даёт правильную для субтитра форму «пирамидой» — верхняя строка
    короче нижней (правило BBC/Netflix: разрыв не должен рвать словосочетание,
    а нижняя строка не должна быть огрызком).

    ПОЧЕМУ БЕЗ ПОРОГА «КОРОТКОГО СЛОВА»: длинное слово-сирота склеить с соседом
    всё равно не выйдет — `fits` не пропустит, и строка останется как была. То
    есть длина отсекается сама, ЗАМЕРОМ ширины, а не ещё одним числом с потолка.

    Не трогаем, когда чинить нечем:
      • строк меньше двух — сироты нет по построению;
      • в последней строке не одно слово — не сирота;
      • в предыдущей строке одно слово — забрать нечего, иначе останется пустая
        строка (типичный случай при огромном кегле: «социальные / сети,»);
      • склейка не влезает в ту же ширину (`fits`) — лечение хуже болезни.

    lines — список СТРОК-СПИСКОВ (элементы любые: слова-строки или слова-словари
    караоке), fits(список_элементов) -> влезает ли строка в ширину. Возвращает
    новый список строк (исходный не меняется)."""
    if len(lines) < 2 or len(lines[-1]) != 1 or len(lines[-2]) < 2:
        return lines
    moved = [lines[-2][-1]] + lines[-1]
    if not fits(moved):
        return lines
    return lines[:-2] + [lines[-2][:-1], moved]


def _wrap_lines(text, font, max_w):
    """Разбить строку на строки-переносы шириной ≤ max_w (жадно, по словам). Слово
    длиннее max_w уходит на свою строку целиком (перенос внутри слова не делаем —
    кегль ужимается выше по стеку, чтобы не вылезти за кадр). Возвращает ≥1 строку.

    Последним проходом снимаем слово-сироту в конце (`_fix_orphan`)."""
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    def fits(words):
        return probe.textlength(" ".join(words), font=font) <= max_w

    lines, cur = [], []
    for word in str(text).split():
        cur.append(word)
        if len(cur) > 1 and not fits(cur):
            cur.pop()
            lines.append(cur)
            cur = [word]
    if cur:
        lines.append(cur)
    return [" ".join(ln) for ln in _fix_orphan(lines, fits)] or [str(text)]


def _text_sprite_wrapped(text, font, fill, max_w, stroke=INK, stroke_w=9, pad=26,
                         shadow=True, line_gap=1.12, shadow_dy=4,
                         shadow_alpha=120, box=None, glow=0, glow_alpha=150):
    """Многострочный вариант _text_sprite: текст переносится по ширине max_w и
    рисуется в НЕСКОЛЬКО строк, каждая центрируется по горизонтали. Нужен для
    крупного субтитра ×3 (demo/compare/без-голоса), чтобы длинная подпись не
    вылезала за кадр. Обводка/тень масштабируются штатно (передаются кеглем шрифта
    и stroke_w извне). Одна строка → результат совпадает по стилю с _text_sprite."""
    lines = _wrap_lines(text, font, max_w)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    boxes = [probe.textbbox((0, 0), ln, font=font, stroke_width=stroke_w)
             for ln in lines]
    line_h = max((b[3] - b[1]) for b in boxes)
    widths = [b[2] - b[0] for b in boxes]
    step = int(round(line_h * line_gap))
    W = int(max(widths) + pad * 2)
    H = int(step * (len(lines) - 1) + line_h + pad * 2)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _draw_box(d, pad, W, H, box)               # R30 boxed: подложка за блоком
    for i, ln in enumerate(lines):
        b = boxes[i]
        lw = b[2] - b[0]
        ox = (W - lw) // 2 - b[0]              # центрируем каждую строку
        oy = pad - b[1] + i * step
        if shadow:
            d.text((ox, oy + shadow_dy), ln, font=font,
                   fill=(0, 0, 0, shadow_alpha), stroke_width=stroke_w,
                   stroke_fill=(0, 0, 0, shadow_alpha))
        d.text((ox, oy), ln, font=font, fill=tuple(fill) + (255,),
               stroke_width=stroke_w, stroke_fill=tuple(stroke) + (255,))
    return im


def _plate_text_color(accent, fill=WHITE):
    """E4-51: цвет слова НА акцентной плашке караоке — из пары «тёмный INK ↔
    светлая заливка субтитра» берём тот, что контрастнее самой плашки. На
    светлых акцентах (лайм, мята) это прежний INK — кадр байт-в-байт; на тёмных
    (бордо, графит) прежний INK давал тёмное на тёмном."""
    return INK if _wcag_ratio(accent, INK) >= _wcag_ratio(accent, fill) else fill


def _plate_contrast(accent, fill=WHITE):
    """Контраст выбранной пары «плашка ↔ слово» — уходит в meta для гейта."""
    return _wcag_ratio(accent, _plate_text_color(accent, fill))


def _karaoke_sprite(words, font_maker, size, active_idx, accent, split,
                    style="accent", stroke=INK, pad=32, stroke_w=None,
                    shadow=None, scale_pop=1.08, plate_pad=12, plate_radius=14,
                    fill=WHITE, box=None, glow=0, glow_alpha=150):
    """Строка субтитра с ОДНИМ подсвеченным словом (караоке). Слоты слов
    ФИКСИРОВАНЫ (позиции считаются базовым кеглем и не зависят от того, какое
    слово активно), поэтому при смене активного строка не дёргается: предрендерим
    по спрайту на каждый индекс и просто меняем их в цикле. `font_maker(size)` →
    шрифт нужного кегля (нужен для scale-pop без хардкода начертания).

    split — чистый белый жирный без обводки/тени (реф. Пример2); A/B/cutout —
    Black с обводкой и тенью (читается на любом фоне).

    style — ПРЕСЕТ подсветки активного слова (дефолт accent, обратная совместимость):
      • accent — активное слово красится акцентом + scale-pop (спека формата 1);
      • plate  — под активным словом акцентная плашка-подсветка, текст тёмным (INK);
      • reveal — пословное появление: слова ПОСЛЕ активного ещё не нарисованы,
                 активное — акцентом со scale-pop, прошедшие — белым.
    Scale-pop подбирается так, чтобы увеличенное слово НЕ съело пробел до соседа
    (кегль ужимается, если +8% не влезает) — тогда слот остаётся читаемым.

    R30 (слой стиля клиента): stroke_w/shadow/scale_pop/plate_pad/plate_radius/
    fill приходят из Style. None у stroke_w/shadow → прежняя логика движка
    (split — чистый белый, иначе обводка 9 + тень), остальные дефолты равны
    прежним константам — style=None даёт байт-в-байт ту же строку.

    T1: `box` (sub_style.kind='boxed') раньше сюда не доходил вовсе —
    `_text_sprite`/`_text_sprite_wrapped` подложку умели рисовать, а
    `_karaoke_sprite` параметра не принимала. Караоке — ДЕФОЛТ для всех шести
    говорящих раскладок (R9), поэтому boxed-пресет, который checks.verify_talk
    сам же советует как лекарство от низкого контраста субтитра, на практике
    НЕ включался (замер T1: split на IMG_8138 с --style subtitle_boxed.json
    контраст остаётся 1.2:1 — байт в байт как без стиля). box=None → подложки
    нет, поведение прежнее.
    """
    if stroke_w is None:
        stroke_w = 0 if split else 9
    if shadow is None:
        shadow = not split
    font = font_maker(size)
    tokens = [x["w"] for x in words]

    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    space = probe.textlength(" ", font=font)
    base_w = [probe.textlength(t, font=font) for t in tokens]
    xs, xx = [], pad                      # левые края слотов (старт = левый pad)
    for w0 in base_w:
        xs.append(xx)
        xx += w0 + space
    total = xx - space                    # правый край текста
    fph = font_maker(int(round(size * scale_pop)))
    hb = probe.textbbox((0, 0), "Ац", font=fph, stroke_width=stroke_w)
    W, H = int(total + pad), int((hb[3] - hb[1]) + pad * 2)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _draw_box(d, pad, W, H, box)           # T1: подложка ЗА словами, ДО текста
    cy = H // 2

    # МЯГКИЙ БЕЛЫЙ ФОН ВОКРУГ БУКВ (владелец 02.09.2026). Рисуется ДО слов, на
    # отдельном слое: белая копия строки, сильно размытая по Гауссу. Это не
    # плашка — форма повторяет текст, кадр сквозь неё просвечивает.
    # 02.09.2026: параметр был только у _text_sprite, а караоке — ДЕФОЛТ всех
    # говорящих раскладок, поэтому фон не рисовался вовсе, хотя стоял в пресете.
    if glow:
        _g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        _gd = ImageDraw.Draw(_g)
        _fnt = font_maker(size)
        for _i, _tok in enumerate(tokens):
            _gd.text((xs[_i], cy), _tok, font=_fnt,
                     fill=(255, 255, 255, int(glow_alpha)), anchor="lm",
                     stroke_width=(stroke_w or 0) + max(3, int(glow) // 3),
                     stroke_fill=(255, 255, 255, int(glow_alpha)))
        im.alpha_composite(_g.filter(ImageFilter.GaussianBlur(int(glow))))

    def _word(cx_or_x, tok, f, color, center, sw=None, sh=None):
        anc = "mm" if center else "lm"
        sw = stroke_w if sw is None else sw
        sh = shadow if sh is None else sh
        if sh:
            d.text((cx_or_x, cy + 4), tok, font=f, fill=(0, 0, 0, 120), anchor=anc,
                   stroke_width=sw, stroke_fill=(0, 0, 0, 120))
        d.text((cx_or_x, cy), tok, font=f, fill=tuple(color) + (255,), anchor=anc,
               stroke_width=sw, stroke_fill=tuple(stroke) + (255,))

    for i, tok in enumerate(tokens):
        if style == "reveal" and active_idx >= 0 and i > active_idx:
            continue                       # будущее слово ещё не появилось
        is_active = (i == active_idx)

        if style == "plate" and is_active:
            b = probe.textbbox((xs[i], cy), tok, font=font, anchor="lm")
            p = plate_pad
            d.rounded_rectangle([b[0] - p, b[1] - p, b[2] + p, b[3] + p],
                                plate_radius, fill=tuple(accent) + (255,))
            # E4-51: на плашке слово рисуется БЕЗ обводки и тени. Раньше оно шло
            # общим путём: заливка INK и обводка stroke (у kind=stroked это тот
            # же INK, толщина 9) — тёмное на тёмном, слово превращалось в кляксу
            # (находка C5 на каталоге пресетов; «Энергичный молодёжный» пришлось
            # уводить в boxed). Плашка сама даёт отбивку от фона, обводка ей не
            # нужна ни при каком цвете; вдобавок берём ту из пары INK/fill,
            # что контрастнее заливки плашки, — тёмный акцент больше не съедает
            # тёмный текст.
            _word(xs[i], tok, font, _plate_text_color(accent, fill),
                  center=False, sw=0, sh=False)
            continue

        # T7 (владелец, показ split/card/pip Валерии): «субтитр — просто белый,
        # без цветных акцентных выделений караоке». Раньше активное слово стилей
        # accent/reveal красилось accent (лайм/бордо/…); теперь красится тем же
        # fill, что и неактивные слова (по дефолту WHITE) — цвета в караоке-
        # субтитре больше нет вовсе, а scale-pop (укрупнение активного слова)
        # ниже НЕ завязан на color и остаётся как был. `accent` тут больше не
        # читается style=="accent"/"reveal" веткой намеренно — он используется
        # только style=="plate" (см. выше): там подсветка — АКЦЕНТНАЯ ПЛАШКА за
        # словом, отдельная от заливки текста сущность, владелец её не касался
        # (не дефолт, `--karaoke-style plate` нужно указать явно), поэтому не
        # трогаем. CTA-плашка кодового слова (E4-57 pick_accent/accent_family)
        # тоже не задета — она в другом коде (_apply_overlays/_insert_card),
        # accent там свой, независимый от караоке.
        color = fill
        if is_active:
            # scale-pop: ищем кегль ≤ base_w+0.8*space, чтобы не съесть пробел
            ps = int(round(size * scale_pop))
            while ps > size:
                if probe.textlength(tok, font=font_maker(ps)) <= base_w[i] + 0.8 * space:
                    break
                ps -= 1
            _word(xs[i] + base_w[i] / 2, tok, font_maker(ps), color, center=True)
            continue

        _word(xs[i], tok, font, color, center=False)
    return im


def _karaoke_sprite_wrapped(words, font_maker, size, active_idx, accent, split,
                            style="accent", max_w=None, stroke=INK, **kw):
    """Многострочное караоке для крупного кегля ×3: слова раскладываются по
    строкам шириной ≤ max_w, каждая строка рендерится обычным _karaoke_sprite
    (активное слово подсвечивается только в своей строке), строки центрируются и
    ставятся стопкой. Одна строка → идентично _karaoke_sprite."""
    if not words:
        return _karaoke_sprite(words, font_maker, size, active_idx, accent, split,
                               style, stroke, **kw)
    font = font_maker(size)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    def fits(chunk):
        return probe.textlength(" ".join(x["w"] for x in chunk),
                                font=font) <= max_w

    lines, cur = [], []
    for wd in words:
        cur.append(wd)
        if max_w and len(cur) > 1 and not fits(cur):
            cur.pop()
            lines.append(cur)
            cur = [wd]
    if cur:
        lines.append(cur)
    # сирота в конце (см. _fix_orphan): караоке — ДЕФОЛТНЫЙ путь завода (R9),
    # так что без этой строчки правило не действовало бы почти нигде.
    if max_w:
        lines = _fix_orphan(lines, fits)
    # индекс первого слова строки считаем ПОСЛЕ лечения сироты: слово могло
    # переехать между строками, а по этому индексу ездит подсветка.
    sprites = []
    start = 0
    for lw in lines:
        end = start + len(lw)
        local = active_idx - start if start <= active_idx < end else -1
        start = end
        sprites.append(_karaoke_sprite(lw, font_maker, size, local, accent, split,
                                       style, stroke, **kw))
    W = max(s.width for s in sprites)
    H = sum(s.height for s in sprites)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    y = 0
    for s in sprites:
        im.paste(s, ((W - s.width) // 2, y), s)
        y += s.height
    return im


def _cover(img_bgr, w, h):
    """Вписать кадр в w×h по большей стороне и обрезать центр (без растяжения)."""
    ih, iw = img_bgr.shape[:2]
    s = max(w / iw, h / ih)
    nw, nh = int(round(iw * s)), int(round(ih * s))
    r = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    x = (nw - w) // 2
    y = (nh - h) // 2
    return r[y:y + h, x:x + w]


def _det_best(img_bgr, det, tr=None, key=None):
    """Самое крупное лицо кадра: ОТНОСИТЕЛЬНАЯ рамка mediapipe (xmin, ymin,
    width, height) или None. Общая детекция для `_face_center` и `_face_box`.

    Раньше каждая из них звала `det.process` сама и одинаково выбирала лицо с
    максимальной площадью. Здесь это один вызов; арифметика выбора сохранена
    ДОСЛОВНО (площадь считается в пикселях ИСХОДНИКА, `(b.width*ww)*(b.height*hh)`),
    поэтому при tr=None результат обеих функций байт в байт прежний.

    W2/F1: tr — `talk_reframe.Tracker`, key — место кадрирования. tr=None (дефолт)
    — прежний путь: полное разрешение, детекция на КАЖДОМ кадре. tr задан — кадр
    перед детекцией ужимается до `tr.max_w` (донор chopify: DET_W=640) и детектор
    зовётся не чаще раза в `tr.stride` кадров (донор: DET_FPS=5), между сэмплами
    отдаётся последняя рамка. Координаты mediapipe ОТНОСИТЕЛЬНЫЕ, от масштаба
    картинки не зависят, поэтому единицы возврата не меняются — вызывающий
    по-прежнему умножает их на размер исходника."""
    hh, ww = img_bgr.shape[:2]

    def _run():
        a = img_bgr
        if tr is not None:
            k = tr.scale(ww)
            if k < 1.0:
                # низкорезный кадр под детектор. INTER_LINEAR, а не INTER_AREA:
                # донор жмёт ffmpeg-фильтром `scale` (там дефолт билинейный), а
                # замер показал, что INTER_AREA на этом ужатии стоит 2.70 мс
                # против 0.39 мс у INTER_LINEAR — при 4.37 мс на саму детекцию
                # area-усреднение съело бы весь выигрыш. Качество детекции от
                # этого не страдает: mediapipe всё равно ресайзит вход в свои
                # 192×192.
                a = cv2.resize(a, (max(1, int(round(ww * k))),
                                   max(1, int(round(hh * k)))),
                               interpolation=cv2.INTER_LINEAR)
        a = cv2.cvtColor(a, cv2.COLOR_BGR2RGB)
        res = det.process(a)
        best, area = None, 0
        for d in (res.detections or []):
            b = d.location_data.relative_bounding_box
            s = (b.width * ww) * (b.height * hh)
            if s > area:
                area = s
                best = (b.xmin, b.ymin, b.width, b.height)
        return best

    if tr is None or key is None:
        return _run()
    return tr.sample(key, _run)


def _face_center(img_bgr, det, tr=None, key=None):
    """Центр самого крупного лица в кадре (пиксели исходника) или None."""
    hh, ww = img_bgr.shape[:2]
    b = _det_best(img_bgr, det, tr, key)
    if b is None:
        return None
    return ((b[0] + b[2] / 2) * ww, (b[1] + b[3] / 2) * hh)


def _cover_face(img_bgr, w, h, det, ytarget=0.44, bias=1.0, scale=1.0, ref=None,
                sm=None, key=None, tr=None):
    """Как _cover, но окно кадрирования сдвинуто на лицо. bias=1.0 — жёстко на
    лицо (спикер: голова в кадре с воздухом над головой). bias<1 — мягко: в
    основном центр-кроп, к лицу лишь подтягиваем (b-roll: сохраняем общий план,
    не зумим средний кадр в крупный, но не даём голове уехать за край). Нет
    лица → центр.

    scale>1 — ЗУМ-УДАР (формат 7, punch-in): исходник масштабируется сильнее
    (s×scale), окно w×h вырезается меньше исходной картинки — кадр «наезжает» на
    лицо. ytarget держит голову с воздухом сверху, поэтому при умеренном scale
    (рамп ограничен в render, ≤~1.12) голова НЕ срезается — verify_talk по лицу
    остаётся зелёной. scale=1.0 (дефолт) — прежнее поведение байт в байт.

    ref — кадр для ДЕТЕКЦИИ лица, если он не совпадает с кадрируемым img_bgr
    (напр. img_bgr — RGBA-силуэт спикера, а лицо ищем на исходном BGR-кадре тех
    же размеров). None → детекция по самому img_bgr (обычный путь).

    W1: sm — talk_reframe.Smoother, key — имя места вызова (у каждого свой трек).
    sm=None (дефолт) — прежнее поведение БАЙТ В БАЙТ: сырая детекция уходит прямо
    в позицию окна, дрожание рамки становится дрожанием кадра. sm задан — центр
    лица проходит через экспоненциальный фильтр со snap-on-cut и поводком, а
    пропущенная детекция заполняется последним известным центром (щелчок «кадр
    прыгнул к центру и вернулся» исчезает).

    W2: tr — talk_reframe.Tracker (разреженная детекция). tr=None (дефолт) —
    детектор зовётся на каждом кадре полного разрешения, как раньше. tr задан —
    низкорезный кадр и один сэмпл на tr.stride кадров, между сэмплами держится
    последняя рамка, а сглаживание в неё доезжает экспонентой."""
    ih, iw = img_bgr.shape[:2]
    s = max(w / iw, h / ih) * scale
    nw, nh = int(round(iw * s)), int(round(ih * s))
    r = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    cx, cy = (nw - w) // 2, (nh - h) // 2           # центр-кроп
    fc = _face_center(ref if ref is not None else img_bgr, det, tr, key)

    def _win(fc0):
        """Позиция окна кадрирования по центру лица (или центр-кроп при None).
        Формулы — прежние, вынесены в замыкание, чтобы считать позицию и по
        сырой детекции (замер «до»), и по сглаженной (замер «после»)."""
        if fc0 is None:
            return cx, cy
        fx = fc0[0] * s - w / 2                       # смещение «на лицо»
        fy = fc0[1] * s - h * ytarget
        return (max(0, min(int(cx + bias * (fx - cx)), nw - w)),
                max(0, min(int(cy + bias * (fy - cy)), nh - h)))

    if sm is None:
        x, y = _win(fc)                               # прежний путь байт в байт
    else:
        x, y = _win(sm.center(key, fc, (iw, ih), (w / s, h / s)))
        sm.note(key, _win(fc), (x, y))                # само-baseline «до/после»
    return r[y:y + h, x:x + w]


def _face_box(img_bgr, det, tr=None, key=None):
    """Рамка самого крупного лица (x0,y0,x1,y1) в пикселях исходника или None."""
    hh, ww = img_bgr.shape[:2]
    b = _det_best(img_bgr, det, tr, key)
    if b is None:
        return None
    x0, y0 = b[0] * ww, b[1] * hh
    w0, h0 = b[2] * ww, b[3] * hh
    return (x0, y0, x0 + w0, y0 + h0)


def _cover_keep_head(img_bgr, w, h, det, headroom=0.10, ref=None,
                     sm=None, key=None, tr=None):
    """Кадрировать img_bgr в окно w×h ЦЕНТРОМ по горизонтали с ГАРАНТИЕЙ, что вся
    голова (лицо + запас над макушкой и подбородком) целиком в окне. Если при
    cover-масштабе голова не влезает по высоте — уменьшаем зум и вписываем кадр
    (чёрные поля лучше среза головы). Нет лица (b-roll) → обычный центр-кроп
    _cover, чтобы не менять поведение без лица.

    Возвращает (кадр_w×h, info|None). info={"top_inside","margin_px"} — для метрики
    «голова в кадре»: top_inside=True, если макушка И подбородок внутри окна;
    margin_px — расстояние от верха окна до макушки (запас над головой).

    W1: sm/key — сглаживание трека (см. _cover_face). Здесь через фильтр идёт вся
    РАМКА: её центр задаёт вертикаль окна, а её высота — зум (голова не влезла →
    зум-аут). Дрожал и центр, и высота, поэтому кадр не только трясло, но и
    «дышало» масштабом. sm=None — прежнее поведение байт в байт.

    W2: tr — talk_reframe.Tracker (разреженная детекция), см. `_cover_face`."""
    ih, iw = img_bgr.shape[:2]
    fb_raw = _face_box(ref if ref is not None else img_bgr, det, tr, key)
    if sm is None:
        fb = fb_raw                                   # прежний путь байт в байт
    else:
        s0 = max(w / iw, h / ih)                      # окно в пикселях исходника
        fb = sm.box(key, fb_raw, (iw, ih), (w / s0, h / s0))
    if fb is None:
        return _cover(img_bgr, w, h), None

    def _geom(fb0):
        """(s, head_top, head_bot, margin) по рамке лица — прежние формулы."""
        _fx0, fy0_, _fx1, fy1_ = fb0
        fh_ = max(1.0, fy1_ - fy0_)
        ht = max(0.0, fy0_ - 0.6 * fh_)          # макушка/волосы над рамкой лица
        hb = min(float(ih), fy1_ + 0.05 * fh_)   # подбородок с малым запасом
        hh = max(1.0, hb - ht)
        mg = headroom * h                        # запас над макушкой (px окна)
        s_ = max(w / iw, h / ih)                 # базовый cover-масштаб
        if hh * s_ + 2 * mg > h:                 # голова не влезает → зум-аут
            s_ = max((h - 2 * mg) / hh, 1e-3)
        return s_, ht, hb, mg

    s, head_top, head_bot, margin = _geom(fb)
    nw, nh = max(1, int(round(iw * s))), max(1, int(round(ih * s)))
    r = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((h, w, 3), np.uint8)
    # горизонталь: центр окна (или поле по бокам, если кадр уже окна)
    if nw >= w:
        sx0, dx, cw = (nw - w) // 2, 0, w
    else:
        sx0, dx, cw = 0, (w - nw) // 2, nw
    # вертикаль: держим макушку с запасом внутри окна (или вписываем с полями)
    head_top_s, head_bot_s = head_top * s, head_bot * s
    if nh >= h:
        y = max(0, min(int(round(head_top_s - margin)), nh - h))
        sy0, dy, ch = y, 0, h
    else:
        sy0, dy, ch = 0, (h - nh) // 2, nh
    crop = r[sy0:sy0 + ch, sx0:sx0 + cw]
    canvas[dy:dy + crop.shape[0], dx:dx + crop.shape[1]] = crop
    win_head_top = dy + (head_top_s - sy0)       # макушка в координатах окна
    win_head_bot = dy + (head_bot_s - sy0)       # подбородок в координатах окна
    top_inside = (win_head_top >= 0) and (win_head_bot <= h)
    if sm is not None:
        # замер «до/после» в ПИКСЕЛЯХ ОКНА: канал 0 — высота головы в окне (её
        # дрожание = «дыхание» зума), канал 1 — макушка в окне (вертикальный
        # тремор). Горизонталь тут всегда центр — мерить нечего.
        sm.note(key, _head_metric(fb_raw, _geom, h, ih),
                ((head_bot_s - head_top_s), win_head_top))
    return canvas, {"top_inside": bool(top_inside),
                    "margin_px": float(win_head_top)}


def _head_metric(fb, geom, h, ih):
    """W1-замер: где оказались бы голова и её размер в окне при рамке fb.
    Повторяет вертикальную геометрию _cover_keep_head без ресайза (нужны только
    числа). fb=None (детекции нет) → центр-кроп: голова «нулевая», макушка на
    середине окна — так щелчок «кадр прыгнул к центру» попадает в метрику."""
    if fb is None:
        return (0.0, h / 2.0)
    s, ht, hb, mg = geom(fb)
    nh = max(1, int(round(ih * s)))
    if nh >= h:
        sy0, dy = max(0, min(int(round(ht * s - mg)), nh - h)), 0
    else:
        sy0, dy = 0, (h - nh) // 2
    return ((hb - ht) * s, dy + (ht * s - sy0))


def _punch_scale(t, windows, peak, ramp_frames, fps, style="smooth"):
    """Множитель зума для формата 7 в момент t. Вне окон punch — 1.0.

    style="smooth" (дефолт, байт-в-байт): рамп ВВЕРХ за ramp_frames кадров у
    левой границы, рамп ВНИЗ у правой, между — держим peak. Рамп сглажен
    smoothstep (мягкий старт/финиш, без рывка) — «наезд».

    style="step" (R15): монтажный УДАР — рамп 1 кадр, без сглаживания: весь
    кадр окна держится на peak, вход/выход скачком (как склейка с наездом).
    Рекомендованный peak для step — 1.18 (сильнее smooth-дефолта 1.10, иначе
    удар не читается). windows: [{"t0","t1"}, ...]."""
    if not windows or peak <= 1.0:
        return 1.0
    if style == "step":
        for w in windows:
            if w["t0"] <= t < w["t1"]:
                return peak                            # удар с первого кадра
        return 1.0
    rampd = max(1e-3, ramp_frames / (fps or 30.0))
    for w in windows:
        if w["t0"] <= t < w["t1"]:
            into, out = t - w["t0"], w["t1"] - t
            f = min(into / rampd, out / rampd, 1.0)
            f = max(0.0, f)
            f = f * f * (3 - 2 * f)                    # smoothstep
            return 1.0 + (peak - 1.0) * f
    return 1.0


def _title_behind(text, acc, cy=760, color=None):
    """КРУПНЫЙ текст-заголовок формата 4 (text-behind) на всю ширину кадра.

    Возвращает RGBA-слой во весь кадр: заголовок ложится МЕЖДУ фоном и силуэтом
    спикера — буквы уходят за плечи/голову, а левый/правый край и верх строки
    читаются рядом с фигурой и над ней. Автокегль под ширину (поля M), 1..3
    строки по словам. Тёмная обводка — чтобы текст читался и на b-roll-фоне, и
    там, где выглядывает из-за головы. Цвет по умолчанию — акцент (единственная
    координата цвета завода); буквы за фигурой всё равно частично видны, поэтому
    контраст обводки важнее заливки."""
    fill = tuple(color if color is not None else acc)
    im = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    inner = VW - 2 * M
    words = text.upper().split()

    def layout(size):
        f = e.font("Black", size)
        lines, cur = [], ""
        for w0 in words:
            trial = (cur + " " + w0).strip()
            if cur and d.textlength(trial, font=f) > inner:
                lines.append(cur)
                cur = w0
            else:
                cur = trial
        if cur:
            lines.append(cur)
        widest = max((d.textlength(ln, font=f) for ln in lines), default=0)
        return f, lines, widest

    size = 210
    while size > 60:
        f, lines, widest = layout(size)
        lh = int(size * 1.02)
        if widest <= inner and lh * len(lines) <= 1040 and len(lines) <= 3:
            break
        size -= 6
    f, lines, widest = layout(size)
    lh = int(size * 1.02)
    y = int(cy - lh * len(lines) / 2)
    sw = max(6, size // 22)                            # обводка масштабом кегля
    for ln in lines:
        w = d.textlength(ln, font=f)
        d.text(((VW - w) // 2, y), ln, font=f, fill=fill + (255,),
               stroke_width=sw, stroke_fill=(14, 12, 11, 235))
        y += lh
    return im


#: во сколько раз крупнее кадр на чётной сцене джамп-ката. 1.08 — заметно
#: глазами как рез, но не читается как «наехали зумом».
JUMP_ZOOM = 1.08


def _speaker_wide(frame, det, wide, face_y, sm=None, key="speaker", tr=None):
    """ШИРОКИЙ план спикера: человек уменьшен до `wide` доли кадра и прижат
    ВВЕРХ, снизу — размытое продолжение того же кадра.

    ЗАЧЕМ (решение владельца 28.08.2026). Съёмка идёт 9:16, кадр выхода тоже
    9:16 — cover-кроп берёт кадр ЦЕЛИКОМ, обрезать нечего, и подвинуть лицо
    вверх нельзя: слака нет. На раскладке card это упиралось в тупик: лицо
    занимает 0.08-0.40 высоты, подпись стоит на 0.4536, уводить её некуда —
    сверху лицо, снизу она сама (замер: 10 реплик из 45 на лице). Уменьшив
    спикера, получаем под подбородком ЧИСТУЮ полосу под подпись — то же, что в
    референсе владельца, где спикер снят шире.

    Низ заполняем размытым и затемнённым тем же кадром, а не чёрной полосой:
    полоса читалась бы как брак, а размытие — как приём. Оно же поднимает
    контраст подписи, потому что темнее исходника."""
    base = _cover(frame, VW, VH)
    nw, nh = int(round(VW * wide)), int(round(VH * wide))
    small = cv2.resize(base, (nw, nh), interpolation=cv2.INTER_AREA)
    # фон: тот же кадр, размытый и притемнённый
    bg = cv2.GaussianBlur(base, (0, 0), sigmaX=VW * 0.035)
    bg = (bg.astype(np.float32) * 0.55).astype(np.uint8)
    x = (VW - nw) // 2
    # СТАВИМ ПО ЛИЦУ, а не по краю. Прижим к фиксированной высоте работал, пока
    # в кадре один человек; на подкасте камера показывает и собеседницу, она
    # сидит ниже — её подбородок снова попадал в строку (замер: 3 реплики из
    # 45). Ищем лицо в уменьшенном кадре и двигаем так, чтобы оно встало на
    # face_y. Лица нет (отвернулся, спина) — прежний прижим вверх.
    y = SAFE_TOP // 2
    if det is not None and face_y > 0.0:
        fb = _face_box(small, det)
        # ПРОПУСК ДЕТЕКЦИИ — главный источник дрожания, а не микрошум рамки.
        # Детектор находит лицо не в каждом кадре; раньше на «ненайденных»
        # кадрах положение падало на запасное (SAFE_TOP//2), и спикер скакал
        # между двумя точками кадр через кадр. Замер 28.08: 33.6 склейки/мин,
        # 18 сцен из 30 короче 0.6 c — и всё это на кусках БЕЗ вставок, то есть
        # трясся именно спикер. Поэтому сглаживатель зовём ВСЕГДА, в том числе
        # с None: он держит последний известный центр через пропуск (talk_reframe
        # ._fill) — ради этого он и написан.
        raw = None
        if fb is not None:
            raw = ((fb[0] + fb[2]) / 2.0, (fb[1] + fb[3]) / 2.0)
        pt = sm.center(key + ":wide", raw, (nw, nh), (nw, nh)) if sm is not None else raw
        if pt is not None:
            y = int(round(face_y * VH - pt[1]))
    y = max(0, min(y, VH - nh))
    out = bg.copy()
    out[y:y + nh, x:x + nw] = small
    return out


def _speaker_crop(frame, det, ps, sm=None, key="speaker", tr=None, face_y=0.0,
                  jump=1.0, wide=1.0):
    """Кадр спикера VW×VH (BGR) для полноэкранных раскладок, где спикер — ЖИВОЙ
    кадр (split-низ off, крупная голова). При ps>1 и живом детекторе — зум-удар
    по лицу (формат 7); иначе БАЙТ В БАЙТ обычный cover-кроп (дефолт не тронут).

    W1: sm/key прокидываются в _cover_face. Путь без лица (ps==1) детектор не
    зовёт вовсе — там сглаживать нечего и трогать нечего.
    W2: tr (разреженная детекция) прокидывается туда же."""
    if ps > 1.0 and det is not None:
        return _cover_face(frame, VW, VH, det, ytarget=0.42, bias=1.0, scale=ps,
                           sm=sm, key=key, tr=tr)
    # ПЕРЕАДРИРОВАНИЕ ЛИЦА ВВЕРХ (style.speaker_face_y, 28.08.2026). Без
    # него простой cover ставит человека по центру, и подпись на 0.4536
    # ложится ему на рот. face_y=0 — прежний путь байт-в-байт.
    if wide < 1.0:                     # широкий план: спикер меньше кадра
        return _speaker_wide(frame, det, wide, face_y, sm=sm, key=key, tr=tr)
    if face_y > 0.0 and det is not None:
        return _cover_face(frame, VW, VH, det, ytarget=face_y, bias=1.0,
                           scale=max(1.0, jump), sm=sm, key=key, tr=tr)
    return _cover(frame, VW, VH)


def _mask_bbox(mu8):
    """bbox бинарной маски (uint8) через осевые редукции (C-быстро, без
    аллокации точек). Возвращает (x0,y0,x1,y1) или None если маска пуста."""
    cols = np.any(mu8, axis=0)
    if not cols.any():
        return None
    rows = np.any(mu8, axis=1)
    x0 = int(cols.argmax()); x1 = int(len(cols) - 1 - cols[::-1].argmax())
    y0 = int(rows.argmax()); y1 = int(len(rows) - 1 - rows[::-1].argmax())
    return x0, y0, x1, y1


def _place_speaker_pin(rgba, pin, mx=70, mt=140, mb=60, warm=12, beta=0.9):
    """Шаг 3+4 (E4-22): УСАДКА силуэта под запас рамки + ПИННИНГ его центроида к
    единому якорю ролика. Источники 9:16 не дают запаса кропа (nw==VW), поэтому
    единственный способ и убрать касание рамки, и стабилизировать позицию —
    масштабировать силуэт < 1 и класть на прозрачный холст VW×VH.

    • Единый на ролик scale замораживается по первым `warm` кадрам (90-й
      перцентиль bbox — крупные позы влезают), далее фикс → площадь не «дышит».
    • Смещение пиннинга СГЛАЖИВАЕТСЯ EMA (o=β·o+(1-β)·target): движение маски
      плавное → edge-flicker (iou) не растёт, но медленный дрейф гасится
      (cx/cy std падают). β→1 сильнее сглаживание.
    • Clamp сглаженного смещения под поля mx/mt/mb → верх/лево/право не касаются
      рамки (пока bbox влезает в поле).
    pin — изменяемый dict-состояние на ролик. После разогрева полноразмерная
    альфа НЕ сканируется (scale зафиксирован) — экономия рендера."""
    ih, iw = rgba.shape[:2]
    s0 = max(VW / iw, VH / ih)                     # базовый cover-scale (как _cover)
    # МОНОТОННО-адаптивный scale: усаживаем силуэт ровно настолько, чтобы САМАЯ
    # крупная поза, встреченная к этому кадру, влезала в поле (mx/mt/mb). scale
    # только УМЕНЬШАЕТСЯ (усадка допустима — «дышать» вверх нельзя, иначе площадь
    # скачет). На «ровных» клипах (5356) bbox почти постоянен → усадки нет, клип
    # не ломаем; на клипах с движением (4525) scale опускается под пик → касаний
    # рамки 0 без фиксированного окна разогрева (пик может прийти и после него).
    # bbox берём по alpha в 1/4 разрешения — дёшево, точности хватает на поле.
    a_small = cv2.resize((rgba[:, :, 3] > 127).astype(np.uint8),
                         (max(1, iw // 4), max(1, ih // 4)),
                         interpolation=cv2.INTER_NEAREST)
    bb0 = _mask_bbox(a_small)
    if bb0 is None:
        return _cover(rgba, VW, VH)               # нет силуэта — прежний путь
    bw0 = (bb0[2] - bb0[0] + 1) * 4 * s0
    bh0 = (bb0[3] - bb0[1] + 1) * 4 * s0
    req = pin.get("sfac", 1.0) * min(
        1.0, (VW - 2 * mx) / max(1.0, bw0), (VH - mt - mb) / max(1.0, bh0))
    scale = min(pin.get("scale", 1.0), req)       # только вниз (усадка)
    pin["scale"] = scale
    st = s0 * scale
    nw, nh = max(1, int(round(iw * st))), max(1, int(round(ih * st)))
    r = cv2.resize(rgba, (nw, nh), interpolation=cv2.INTER_AREA)
    mu8 = (r[:, :, 3] > 127).astype(np.uint8)
    bb = _mask_bbox(mu8)
    if bb is None:
        return _cover(rgba, VW, VH)
    bx0, by0, bx1, by1 = bb
    mm = cv2.moments(mu8, binaryImage=True)        # центроид (C-быстро)
    if mm["m00"] <= 0:
        return _cover(rgba, VW, VH)
    gx = mm["m10"] / mm["m00"]; gy = mm["m01"] / mm["m00"]
    # вертикальный якорь ролика: центроид на фикс. высоте (макушка ~mt)
    if pin.get("ay") is None:
        pin.setdefault("_gyoff", []).append(gy - by0)
        if len(pin["_gyoff"]) >= warm:
            pin["ay"] = mt + float(np.percentile(pin["_gyoff"], 50))
    ax = VW / 2.0
    ay = pin.get("ay") if pin.get("ay") is not None else (mt + (gy - by0))
    beta = pin.get("beta", beta)                   # даёт перекрыть β на ролик
    tox, toy = ax - gx, ay - gy                    # цель пиннинга центроида
    # EMA-сглаживание смещения (состояние храним ДО clamp, чтобы clamp не «портил»
    # фильтр) → плавное движение маски, iou не деградирует
    ox = pin.get("ox"); oy = pin.get("oy")
    ox = tox if ox is None else (beta * ox + (1.0 - beta) * tox)
    oy = toy if oy is None else (beta * oy + (1.0 - beta) * toy)
    pin["ox"], pin["oy"] = ox, oy
    # clamp сглаженного смещения под поля → нет касания верх/лево/право
    if bx1 - bx0 <= VW - 2 * mx:
        ox = min(max(ox, mx - bx0), (VW - mx) - bx1)
    if by1 - by0 <= VH - mt - mb:
        oy = min(max(oy, mt - by0), (VH - mb) - by1)
    ofx, ofy = int(round(ox)), int(round(oy))
    canvas = np.zeros((VH, VW, 4), np.uint8)
    dx0, dy0 = max(0, ofx), max(0, ofy)
    sx0, sy0 = max(0, -ofx), max(0, -ofy)
    dw = min(nw - sx0, VW - dx0)
    dh = min(nh - sy0, VH - dy0)
    if dw > 0 and dh > 0:
        canvas[dy0:dy0 + dh, dx0:dx0 + dw] = r[sy0:sy0 + dh, sx0:sx0 + dw]
    return canvas


def _cover_speaker_rgba(rgba, frame, det, ps, pin=None, sm=None, key="spk_rgba",
                        tr=None):
    """Кадрирование RGBA-силуэта спикера VW×VH для cutout/text-behind. ps==1 и
    pin=None — ровно прежний _cover(rgba) (дефолт байт-в-байт). pin (dict) —
    Шаг 3+4: усадка+пиннинг (см. _place_speaker_pin). ps>1 — зум-удар:
    face-aware кроп силуэта, лицо ищем на исходном BGR-кадре (ref=frame)."""
    if ps > 1.0 and det is not None:
        return _cover_face(rgba, VW, VH, det, ytarget=0.42, bias=1.0,
                           scale=ps, ref=frame, sm=sm, key=key, tr=tr)
    if pin is not None:
        return _place_speaker_pin(rgba, pin)
    return _cover(rgba, VW, VH)


def _speaker_alpha_border(spk):
    """E4-22 (P1-1): по RGBA-силуэту спикера В ТОЧКЕ РАЗМЕЩЕНИЯ (холст VW×VH,
    альфа = маска СПИКЕРА, а НЕ композит с b-roll) вернуть (touch, dropout):
      touch   — силуэт касается ВЕРХ/ЛЕВ/ПРАВ границы кадра (низ НЕ считаем:
                спикер стоит от нижнего края по построению — там касание штатно);
      dropout — маска провалилась (площадь альфы <1% кадра: матирование ушло в
                пустой/чёрный кадр).
    Считает ДВИЖОК на рендере, потому что только он знает альфу ИМЕННО спикера
    в точке вставки. checks.verify_talk берёт агрегат из meta и НЕ пере-
    сегментирует готовый композит: там SelfieSegmentation ловит и людей из
    b-roll → ложный «тело режет рамка» на исправном cutout (P1-1)."""
    a = np.asarray(spk)
    if a.ndim != 3 or a.shape[2] < 4:
        return False, False
    bw = a[:, :, 3] > 127
    if float(bw.mean()) < 0.01:
        return False, True          # провал матирования — границы не смотрим
    touch = bool(bw[0, :].any() or bw[:, 0].any() or bw[:, -1].any())
    return touch, False


def _faces_union_box(img_bgr, det):
    """Общая рамка ВСЕХ лиц кадра (x0,y0,x1,y1) или None.

    ЗАЧЕМ. `_face_box` отдаёт САМОЕ КРУПНОЕ лицо — для кадрирования это верно
    (ведём кадр за спикером). Для ПОДПИСИ неверно: у владельца подкаст, в кадре
    бывают двое, движок обходил одну голову, а подпись садилась на вторую.
    Проверка `checks.face_boxes_img` собирает ВСЕ лица — отсюда и расхождение
    «движок не видит, приёмка видит» (замер 28.08: 3 реплики из 45, до 12%).
    Правило владельца «текст не должен закрывать лицо никогда» не различает,
    чьё лицо, поэтому здесь берём объединение. Детектор тот же и с теми же
    порогами, что у проверки (model_selection=1, conf=0.5)."""
    try:
        res = det.process(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    except Exception:
        return None
    hh, ww = img_bgr.shape[:2]
    box = None
    for d in (res.detections or []):
        b = d.location_data.relative_bounding_box
        x0, y0 = b.xmin * ww, b.ymin * hh
        x1, y1 = x0 + b.width * ww, y0 + b.height * hh
        box = (x0, y0, x1, y1) if box is None else (
            min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1))
    return box


def _sub_face_box(base_bgr, det, spk_alpha=None):
    """Рамка лица/головы спикера В КООРДИНАТАХ ГОТОВОГО КАДРА (для позиции
    субтитра). Приоритет — детектор лица по финальному композиту (точная рамка
    лица на месте, где реально стоит спикер после кадрирования). Детектор не нашёл
    лицо (отвёрнут, cutout без punch) — берём голову из АЛЬФЫ силуэта спикера
    (talk_matte): верхняя доля bbox маски = голова+лицо, новые модели не нужны.
    Ни того ни другого нет → None (лица нет — позиция субтитра прежняя)."""
    if det is not None and base_bgr is not None:
        # ВСЕ лица кадра, а не самое крупное: правило не различает, чьё лицо.
        fb = _faces_union_box(base_bgr, det) or _face_box(base_bgr, det)
        if fb is not None:
            return (int(fb[0]), int(fb[1]), int(fb[2]), int(fb[3]))
    if spk_alpha is not None:
        bb = _mask_bbox((spk_alpha > 127).astype(np.uint8))
        if bb is not None:
            x0, y0, x1, y1 = bb
            head_h = max(1, int(round((y1 - y0) * SUB_HEAD_FRAC)))
            return (x0, y0, x1, y0 + head_h)
    return None


def _boxes_hit(a, b):
    """Пересекаются ли два прямоугольника (x0,y0,x1,y1) по площади."""
    if a is None or b is None:
        return False
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oy = min(a[3], b[3]) - max(a[1], b[1])
    return ox > 0 and oy > 0


def _place_sub_cy(default_cy, sw, sh, sx0, fbox, force=False):
    """Позиция центра субтитра по вертикали для одной реплики. Решение принимается
    ОДИН раз на реплику и дальше не пересчитывается покадрово (иначе прыгает).

    force=True (sub_style.cy_force) — позиция из пресета ЖЕЛАЕМАЯ, но не
    безусловная. 28.08.2026 владелец задал правило на ВСЕ форматы: «текст не
    должен закрывать лицо никогда». Поэтому форс задаёт точку отсчёта и зажим по
    краям кадра, а дальше работает то же уклонение от лица, что и без форса.
    Раньше форс уклонение ОТКЛЮЧАЛ — и подпись садилась на лицо (замер 28.08:
    ПОЛОВИНКИ 3 реплики из 53, СУБТИТРЫ 5 из 45, до 46% лица).
    Дефолт force=False — прежняя логика движка байт-в-байт.

    • Рамка текста на дефолтной позиции не пересекает лицо fbox → прежний центр
      (там, где лицо не мешает, позиция прежняя — байт в байт).
    • Пересекает → блок переносится в чистую полосу. Приоритет — ВВЕРХ, под нижнюю
      границу верхней безопасной зоны (верх блока = SAFE_TOP), как в ТЗ. Если
      сверху лицо (голова заходит в верхнюю зону — крупный план «голова-плечи»),
      уходим ВНИЗ, под подбородок (низ лица + зазор), в пределах нижней безопасной
      зоны — там над грудью лица нет, полоса устойчива к движению головы. Из
      подошедших вариантов берём с БОЛЬШИМ запасом до лица. Лицо съедает обе зоны
      → reduced=True (сигнал «сократить число строк»; кегль НЕ трогаем), блок
      прижимаем вверх.

    Рамку лица слегка расширяем по вертикали (SUB_FACE_INFLATE) — запас на
    движение головы внутри реплики, чтобы поднятый блок не задело позже."""
    moved = False
    if force:
        # Форс — только точка отсчёта и зажим по краям кадра. Уклонение от лица
        # ниже работает и с ним: правило «никогда не на лице» сильнее пресета.
        cy = max(sh // 2, min(default_cy, VH - sh // 2))
        moved = cy != default_cy
        default_cy = cy
    if fbox is None:
        return default_cy, moved, False
    sx1 = sx0 + sw
    dy0, dy1 = default_cy - sh // 2, default_cy + sh // 2
    fx0, fy0, fx1, fy1 = fbox
    if not _boxes_hit((sx0, dy0, sx1, dy1), fbox):
        return default_cy, moved, False          # лицо не под субтитром — не трогаем
    infl = int(round((fy1 - fy0) * SUB_FACE_INFLATE))
    ify0, ify1 = fy0 - infl, fy1 + infl          # рамка лица с запасом на движение
    cands = []                                   # (clearance, cy)
    up_cy = SAFE_TOP + sh // 2                    # ВВЕРХ: флаш под верхнюю зону
    if up_cy + sh // 2 <= ify0:                   # блок целиком выше лица
        cands.append((ify0 - (up_cy + sh // 2), up_cy))
    dn_cy = ify1 + SUB_FACE_GAP + sh // 2         # ВНИЗ: под подбородок
    if dn_cy + sh // 2 <= VH - SAFE_BOTTOM:       # влезает в нижнюю зону
        cands.append(((dn_cy - sh // 2) - ify1, dn_cy))
    if cands:
        cands.sort(reverse=True)                  # максимальный запас до лица
        return cands[0][1], True, False
    # ОБЕ ЗОНЫ ЗАНЯТЫ ЛИЦОМ. Раньше здесь слепо возвращался up_cy — «вверх», даже
    # если наверху лицо перекрывалось сильнее, чем в других местах. Правило
    # владельца «текст не должен закрывать лицо никогда» на таком кадре
    # невыполнимо в принципе (крупный план, лицо занимает и верх, и центр), но
    # выбирать всё равно надо ОСОЗНАННО: берём положение с НАИМЕНЬШИМ
    # перекрытием. Замер 28.08: на card оставалось 3 реплики из 45 с перекрытием
    # до 12% — именно из-за слепого «вверх».
    def _overlap(cy):
        y0, y1 = cy - sh // 2, cy + sh // 2
        oy = max(0, min(y1, ify1) - max(y0, ify0))
        ox = max(0, min(sx1, fx1) - max(sx0, fx0))
        return oy * ox
    dn_free = ify1 + SUB_FACE_GAP + sh // 2
    variants = [up_cy, dn_cy, dn_free, default_cy,
                (VH - SAFE_BOTTOM) - sh // 2]      # у нижней границы зоны
    variants = [int(max(sh // 2, min(v, VH - sh // 2))) for v in variants]
    best = min(variants, key=_overlap)
    return best, True, True                        # reduced: сигнал «короче»


def _round_mask(w, h, rad):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], rad, fill=255)
    return m


# ── ТИПОГРАФСКАЯ ВСТАВКА ─────────────────────────────────────────────────────
def _insert_card(text, acc, fmt):
    """Плашка-тезис в стиле завода: тёмный фон, акцентный текст Onest.
    Возвращает (RGBA-спрайт, (x, y)) под нужный формат."""
    if fmt == "A":                 # на весь экран (голова в углу)
        cw, ch = VW, VH
        pos = (0, 0)
        rad = 0
        top = 200                  # текст в ВЕРХНЕЙ зоне — угол с головой ниже,
        #                            плашка не заезжает под PIP и не спорит с субтитром
    else:                          # полноэкранная перебивка-плашка
        cw, ch = VW, VH
        pos = (0, 0)
        rad = 0
        top = None                 # текст по центру кадра

    card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle([0, 0, cw - 1, ch - 1], rad, fill=(14, 12, 11, 255))

    lines = text.split("\n")
    inner = cw - 2 * 70
    size = 132
    while size > 40:
        f = e.font("Black", size)
        if all(d.textlength(ln, font=f) <= inner for ln in lines):
            break
        size -= 4
    f = e.font("Black", size)
    lh = int(size * 1.12)
    total = lh * len(lines)
    y = top if top is not None else (ch - total) // 2
    # акцентный штрих по центру над текстом
    d.rounded_rectangle([cw // 2 - 60, y - 46, cw // 2 + 60, y - 34], 4,
                        fill=acc + (255,))
    for ln in lines:
        w = d.textlength(ln, font=f)
        d.text(((cw - w) // 2, y), ln, font=f, fill=acc + (255,),
               stroke_width=3, stroke_fill=(0, 0, 0, 200))
        y += lh
    return card, pos


def _lower_third(text, acc):
    """Плашка-лента формата 5: спикер остаётся ЖИВЫМ на весь кадр, а снизу поверх
    него ложится скруглённая плашка акцентом с тезисом/числом (деньги/кейсы
    Валерии: «136 точек», «+500 000 ₽», «2 часа сессии»). Возвращает готовый
    RGBA-слой во весь кадр (плашка в зоне LT_*), кладётся на base как есть.

    Геометрия LT_* уже в безопасной зоне и разведена с субтитром по вертикали —
    поэтому лицо/субтитр/кнопки Instagram не спорят с плашкой за место.

    ЗАМЕР 2x (владелец, 2026-08-16): расширенная плашка (LT_W=848) сама по себе
    подняла кегль лишь частично — при старых полях (2*44) и старом условии
    высоты (size*1.25<=LT_H) автоподбор всё ещё оседает заметно ниже 2× от
    старых 104 (см. замер в комментарии у LT_W/LT_H выше). Поля сужены 44→24,
    условие высоты ослаблено 1.25→1.05 (плашка и так выросла по LT_H, запас
    большой) — это и есть компромисс из ТЗ п.4: кегль растёт ОТНОСИТЕЛЬНО
    плашки, а не только вместе с ней. Итог на канонических примерах — «136
    точек» 104→166 (~1.6×), «+500 000 ₽» 104→136 (~1.3×): длинная строка
    упирается в ширину (SAFE_RIGHT), короткая — почти дотягивает до 2×."""
    im = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = LT_X, LT_Y, LT_X + LT_W, LT_Y + LT_H
    d.rounded_rectangle([x0, y0, x1, y1], 28, fill=tuple(acc) + (255,))
    # авто-кегль под ширину и высоту плашки (число/тезис — крупно, но внутри)
    inner = LT_W - 2 * 24
    size = 220
    while size > 30:
        f = e.font("Black", size)
        if d.textlength(text, font=f) <= inner and size * 1.05 <= LT_H:
            break
        size -= 3
    f = e.font("Black", size)
    tb = d.textbbox((0, 0), text, font=f)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.text((x0 + (LT_W - tw) // 2 - tb[0], y0 + (LT_H - th) // 2 - tb[1]),
           text, font=f, fill=tuple(INK) + (255,))
    return im


def _compare_label(text, acc, good=True):
    """Плашка-ярлык формата «хорошо/плохо» (E4-23, NAVIDUU Ф3). Скруглённая лента
    у верхнего края СВОЕЙ половины: зелёный акцент для «ПРАВИЛЬНО» (галка), красный
    для «НЕ ПРАВИЛЬНО» (крест). Возвращает тугой RGBA-спрайт (не во весь кадр), его
    кладут по центру сверху каждой половины стопки. Обводка держит читаемость на
    любом кадре клипа.

    Галку/крест РИСУЕМ вектором, а не глифом ✓/✗: фирменный Onest их не содержит
    (иначе на плашке тофу-квадрат). Текст ярлыка чистим от этих символов, если их
    туда положили в подписи."""
    text = "".join(ch for ch in text if ch not in "✓✔✗✘×") .strip()
    plate = (tuple(acc) if good else (196, 52, 44))          # зелёный / красный
    fg = tuple(INK) if good else (245, 245, 245)
    f = e.font("Black", 58)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tb = probe.textbbox((0, 0), text, font=f)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    padx, pady, gap = 46, 24, 26
    mk = th                                                  # сторона значка ≈ кегль
    W, H = padx + tw + gap + mk + padx, th + 2 * pady
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, W - 1, H - 1], 26, fill=plate + (255,))
    d.text((padx - tb[0], pady - tb[1]), text, font=f, fill=fg + (255,),
           stroke_width=3, stroke_fill=(0, 0, 0, 200))
    # значок справа от текста, по центру высоты
    mx = padx + tw + gap
    my = (H - mk) // 2
    lw = max(5, mk // 6)
    if good:                                                 # галка ✓
        d.line([(mx + mk * 0.10, my + mk * 0.55),
                (mx + mk * 0.40, my + mk * 0.85),
                (mx + mk * 0.92, my + mk * 0.15)],
               fill=fg + (255,), width=lw, joint="curve")
    else:                                                    # крест ✗
        d.line([(mx + mk * 0.15, my + mk * 0.15),
                (mx + mk * 0.85, my + mk * 0.85)], fill=fg + (255,), width=lw)
        d.line([(mx + mk * 0.85, my + mk * 0.15),
                (mx + mk * 0.15, my + mk * 0.85)], fill=fg + (255,), width=lw)
    return im


def _music_caption(hook, sub, family=(), stats=None):
    """Раскладка "music": подпись хук+призыв, ОДНА на весь ролик (не по-
    репличная, как обычный субтитр) — референс IMG_4731.MP4 держит один и тот
    же текст все 14 секунд поверх видео-эстетики без синхронной речи. Поэтому,
    в отличие от `_apply_overlays`/каретки субтитра, рисуется ОДИН РАЗ до
    цикла кадров и просто вклеивается в каждый кадр — дешевле и байт-в-байт
    без караоке-состояния.

    Кегль хука автоподбирается вниз (та же идея, что `_fit_size` у обычных
    субтитров): держим SAFE_W и ≤3 строки, иначе длинный хук книги смыслов
    вылезет за безопасную зону. Кегль призыва — MUSIC_SUB_RATIO от итогового
    кегля хука (не от MUSIC_HOOK_SIZE до автоподбора) — соотношение снято
    замером с референса, а не "просто мельче на глаз".

    family/stats (E4-57/U9, тот же приём, что у _apply_overlays): плашки под
    текстом здесь нет — акцент используется только для акцентного слова, если
    он когда-нибудь понадобится; сейчас параметры приняты для единообразия
    сигнатуры, не используются отрисовкой.

    Возвращает (image, box): box — (x0,y0,x1,y1) объединённой рамки хук+призыв,
    тем же контрактом, что sub_box у обычных раскладок (checks.verify_talk
    ждёт его для проверки «подпись↔лицо»; референсный формат «музыка» её не
    отменяет — просто рамка одна на весь ролик, а не по реплике)."""
    max_w = VW - 2 * SAFE_RIGHT - 2 * 26        # SAFE_W минус паддинг спрайта
    size = MUSIC_HOOK_SIZE
    while size > 34:
        f = e.font("Black", size)
        lines = _wrap_lines(hook, f, max_w)
        if len(lines) <= 3:
            break
        size -= 3
    hook_spr = _text_sprite_wrapped(hook, e.font("Black", size), WHITE, max_w,
                                    stroke=INK, stroke_w=max(3, size // 11))

    sub_size = max(18, round(size * MUSIC_SUB_RATIO))
    sub_f = e.font("Bold", sub_size)
    sub_spr = _text_sprite_wrapped(sub, sub_f, WHITE, max_w, stroke=INK,
                                   stroke_w=max(2, sub_size // 11)) if sub else None

    im = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    total_h = hook_spr.height + (MUSIC_GAP + sub_spr.height if sub_spr else 0)
    y0 = MUSIC_BLOCK_BOTTOM - total_h
    hook_x = (VW - hook_spr.width) // 2
    im.paste(hook_spr, (hook_x, y0), hook_spr)
    box = [hook_x, y0, hook_x + hook_spr.width, y0 + hook_spr.height]
    if sub_spr is not None:
        ysub = y0 + hook_spr.height + MUSIC_GAP
        sub_x = (VW - sub_spr.width) // 2
        im.paste(sub_spr, (sub_x, ysub), sub_spr)
        box[0] = min(box[0], sub_x)
        box[2] = max(box[2], sub_x + sub_spr.width)
        box[3] = ysub + sub_spr.height
    return im, tuple(box)


# ── ОВЕРЛЕИ (E4-11): кодовое слово и подсказка, опциональные слои ─────────────
def _apply_overlays(base, t, ov, acc, family=(), stats=None):
    """Поверх кадра: подсказка-стикер (вверху) и кодовое слово РОСТ (в окне CTA).
    Кодовое слово — механика завода (слово в директ = охват, как «БРЕНД» в реф.
    Пример1). Рисуем на прозрачном слое и накладываем — полупрозрачные плашки
    смешиваются корректно. Всё в безопасной зоне Instagram.

    ov: {"cta": "РОСТ", "cta_sub": "пиши в личку", "cta_at": (t0,t1)|None,
         "hint": "смотри до конца"|None}. cta_at=None → слово весь ролик.

    family/stats (E4-57/U9): family — style.accent_family_rgb (пусто → плашка
    красится accent как раньше, байт-в-байт); stats — dict-аккумулятор для
    meta (accent_contrast_min/accent_swaps), см. pick_accent. base читается
    ДО наложения layer — плашка ещё не нарисована, под ней настоящий фон
    (видео/фото/статика этого кадра), не сама плашка предыдущего кадра.
    """
    layer = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    hint = ov.get("hint")
    if hint:
        f = e.font("SemiBold", 34)
        tw = d.textlength(hint, font=f)
        pad, y0 = 20, SAFE_TOP + 8
        x0 = (VW - tw) / 2 - pad
        d.rounded_rectangle([x0, y0, x0 + tw + 2 * pad, y0 + 54], 27,
                            fill=(14, 12, 11, 150))
        d.text(((VW - tw) / 2, y0 + 9), hint, font=f, fill=(245, 245, 245, 255))

    word = ov.get("cta")
    win = ov.get("cta_at")
    if word and (win is None or (win[0] <= t < win[1])):
        f = e.font("Black", 100)
        tw = d.textlength(word, font=f)
        cx, y, pad = VW / 2, 300, 46
        box = (cx - tw / 2 - pad, y - 22, cx + tw / 2 + pad, y + 118)
        plate_acc, contrast_before, swapped = pick_accent(base, box, acc, family)
        if stats is not None:
            if contrast_before is not None and (stats.get("min") is None
                                                 or contrast_before < stats["min"]):
                stats["min"] = contrast_before
            stats["swaps"] = stats.get("swaps", 0) + (1 if swapped else 0)
        d.rounded_rectangle(list(box), 22, fill=tuple(plate_acc) + (255,))
        d.text((cx - tw / 2, y - 4), word, font=f, fill=tuple(INK) + (255,))
        sub = ov.get("cta_sub")
        if sub:
            fs = e.font("SemiBold", 42)
            sw = d.textlength(sub, font=fs)
            d.text((cx - sw / 2, y + 128), sub, font=fs, fill=(245, 245, 245, 255),
                   stroke_width=5, stroke_fill=(0, 0, 0, 220))

    base.paste(layer, (0, 0), layer)


# ── R51: CTA-СЛОВО РАСКЛАДКИ CARD ──────────────────────────────────────────
# Встроенная особенность card (без флага, в отличие от общего --cta оверлея
# выше): как только реплики субтитра заканчиваются, на их месте само
# появляется кодовое слово (дефолт «РОСТ» — приём завода, см. ПРОЕКТ.md
# «ГОЛОС») кеглем вдвое крупнее субтитра (R7 sub_size_base=100). Держится до
# конца ролика. Геометрия (шрифт/автоподбор/bbox) посчитана один раз до цикла
# кадров в render() (card_cta_font/card_cta_bbox) — меняется только фон под
# плашкой, поэтому pick_accent вызывается КАЖДЫЙ кадр здесь, как и в
# _apply_overlays. Пишет в тот же accent_stats-аккумулятор (accent_contrast_
# min/accent_swaps в meta) — по смыслу та же метрика качества акцентной
# плашки, что и у общего CTA-оверлея.
CARD_CTA_PAD_X = 56     # горизонтальный отступ плашки; учтён в автоподборе выше
CARD_CTA_PAD_Y = 36


def _draw_card_cta(base, word, font, bbox, cy, acc, family=(), stats=None):
    layer = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx, padx, pady = VW / 2, CARD_CTA_PAD_X, CARD_CTA_PAD_Y
    box = (cx - tw / 2 - padx, cy - th / 2 - pady,
           cx + tw / 2 + padx, cy + th / 2 + pady)
    plate_acc, contrast_before, swapped = pick_accent(base, box, acc, family)
    if stats is not None:
        if contrast_before is not None and (stats.get("min") is None
                                             or contrast_before < stats["min"]):
            stats["min"] = contrast_before
        stats["swaps"] = stats.get("swaps", 0) + (1 if swapped else 0)
    d.rounded_rectangle(list(box), 30, fill=tuple(plate_acc) + (255,))
    d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), word, font=font,
           fill=tuple(INK) + (255,))
    base.paste(layer, (0, 0), layer)


# ── НАКЛАДНЫЕ ФОРМАТЫ (комбинируются поверх ЛЮБОЙ раскладки, включаются флагами;
#    дефолт не рисует ничего — поведение движка без флагов не меняется) ─────────

# Формат 3 — СЛОВО-ВСПЫШКА. Вертикальный центр вспышки: верхняя-средняя зона, выше
# субтитра (низ) и ниже окна кодового слова (y≈300) — не спорят за место.
FLASH_CY = 700


def _flash_sprite(text, acc):
    """Крупное слово-акцент как плашка в стиле _insert_card (акцентная заливка,
    тёмный INK-текст). Кегль ужимается под безопасную ширину. Возвращает тугой
    RGBA-спрайт; анимацию (pop-in/fade) накладывает _draw_flash по кадрам."""
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    inner = VW - 2 * SAFE_RIGHT - 60          # держим слово в безопасной ширине
    size = 156
    while size > 48:
        f = e.font("Black", size)
        if probe.textlength(text, font=f) <= inner:
            break
        size -= 4
    f = e.font("Black", size)
    b = probe.textbbox((0, 0), text, font=f)
    tw, th = b[2] - b[0], b[3] - b[1]
    padx, pady = 48, 30
    W, H = int(tw + 2 * padx), int(th + 2 * pady)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, W - 1, H - 1], 26, fill=tuple(acc) + (255,))
    d.text((padx - b[0], pady - b[1]), text, font=f, fill=tuple(INK) + (255,))
    return im


def _draw_flash(base, spr, prog):
    """Наложить спрайт-вспышку с анимацией по прогрессу окна prog∈[0,1]:
    pop-in в начале (масштаб 0.82→1, alpha 0→1), короткий hold, fade-out в конце."""
    if prog <= 0.18:
        k = prog / 0.18
        scale, alpha = 0.82 + 0.18 * k, k
    elif prog >= 0.82:
        k = (1.0 - prog) / 0.18
        scale, alpha = 1.0 + 0.06 * (1.0 - k), k
    else:
        scale, alpha = 1.0, 1.0
    w = max(1, int(spr.width * scale))
    h = max(1, int(spr.height * scale))
    s = spr.resize((w, h), Image.LANCZOS)
    if alpha < 1.0:
        a = s.split()[3].point(lambda v: int(v * max(0.0, min(1.0, alpha))))
        s.putalpha(a)
    base.paste(s, ((VW - w) // 2, FLASH_CY - h // 2), s)


def _progress_bar(base, frac, acc):
    """Формат 6 — тонкая полоса-досмотр вверху (в безопасной зоне под шапкой,
    SAFE_TOP). Тёмный полупрозрачный трек + акцентная заливка шириной t/dur."""
    layer = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, x1, y, hgt = M, VW - M, SAFE_TOP + 8, 8
    d.rounded_rectangle([x0, y, x1, y + hgt], hgt // 2, fill=(14, 12, 11, 120))
    fx = x0 + int((x1 - x0) * max(0.0, min(1.0, frac)))
    if fx > x0:
        d.rounded_rectangle([x0, y, max(x0 + hgt, fx), y + hgt], hgt // 2,
                            fill=tuple(acc) + (255,))
    base.paste(layer, (0, 0), layer)


def _list_counter(base, no, total, title, acc):
    """Формат 8 — счётчик пунктов «N / M» + заголовок пункта, лентой вверху (под
    полосой-досмотром, в безопасной зоне). Заголовок обрезается, чтобы правый
    край держался левее колонки кнопок Instagram (SAFE_RIGHT)."""
    layer = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y = SAFE_TOP + 30
    padx, padh = 22, 12
    fnum = e.font("Black", 40)
    label = "%d / %d" % (no, total)
    lw = d.textlength(label, font=fnum)
    pill_w, pill_h = lw + 2 * padx, 40 + 2 * padh
    x0 = M
    d.rounded_rectangle([x0, y, x0 + pill_w, y + pill_h], pill_h // 2,
                        fill=tuple(acc) + (255,))
    d.text((x0 + padx, y + padh - 4), label, font=fnum, fill=tuple(INK) + (255,))
    if title:
        ft = e.font("Bold", 40)
        tx = x0 + pill_w + 20
        maxw = (VW - SAFE_RIGHT) - tx
        t = title
        while t and d.textlength(t + ("…" if t != title else ""),
                                 font=ft) > maxw and len(t) > 1:
            t = t[:-1]
        if t != title:
            t = t.rstrip() + "…"
        d.text((tx, y + padh - 4), t, font=ft, fill=(245, 245, 245, 255),
               stroke_width=6, stroke_fill=(0, 0, 0, 220))
    base.paste(layer, (0, 0), layer)


# Формат 9 — ЕДИНЫЙ ГРЕЙД: ОДИН ffmpeg-фильтр на финальном энкоде (см. render()).
# Пресеты мягкие, чтобы не пережать лицо (правило «глаз на контактном листе»).
#   warm    — тёплый киношный вид: чуть контраста/насыщенности, тёплый баланс.
#   duotone — лёгкий сплит-тон (тёплые света, прохладные тени), не давит кожу.
GRADES = {
    "warm": "eq=contrast=1.05:brightness=0.008:saturation=1.08:gamma_r=1.05:"
            "gamma_g=1.0:gamma_b=0.95",
    "duotone": "curves=r='0/0.05 0.5/0.52 1/0.98':g='0/0.05 0.5/0.48 1/0.94':"
               "b='0/0.12 0.5/0.46 1/0.86'",
}


#: ПЕРСИСТЕНТНЫЙ кэш нормализованных перебивок (P1). Раньше `prepare_broll`
#: писал только в `td` — временный каталог рендера, который удаляется на выходе,
#: поэтому ОДИН И ТОТ ЖЕ клип банка перекодировался заново в каждом рендере.
#: Замер (57-сек ролик, 6 перебивок из 4K-стока): 24.4 сек из 94.2 сек полного
#: рендера — 26% времени уходило на повторную работу. Конвейер завода собирает
#: НЕСКОЛЬКО раскладок на одной речи (см. ФОРМАТЫ_РАЗГОВОРНЫХ_REELS.md), так что
#: цена умножалась на число форматов.
BROLL_CACHE = os.path.join(HERE, ".cache_broll_prepared")


def prepare_broll(src, seg_start, dur, w, h, td, fps=30):
    """Нормализовать кусок b-roll: автоповорот (снимает ловушку .MOV с флагом),
    кадрирование 9:16 без растяжения под w×h, без звука. Возвращает путь к mp4,
    который дальше читается покадрово через cv2 — уже в правильной геометрии.
    R12: crf 14 (было 20) — у перебивки 3 генерации потерь (исходник → эта
    нормализация → финал), так что промежуточная почти прозрачна.

    P1: результат кладётся в персистентный `BROLL_CACHE` и переиспользуется
    между рендерами. Ключ включает ВСЕ параметры, влияющие на содержимое файла
    (исходник+его mtime/размер, начало куска, длительность, геометрия, fps) —
    иначе кэш отдал бы кусок не той длины при том же клипе и той же секунде
    старта. Кэш не читается — падаем на обычный путь, если каталог недоступен
    (только для чтения, нет места): скорость не должна ломать рендер."""
    vf = ("scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
          % (w, h, w, h))

    def _encode(dst):
        _run([FF, "-y", "-loglevel", "error", "-ss", "%.2f" % seg_start,
              "-t", "%.2f" % (dur + 0.3), "-i", src, "-an", "-vf", vf,
              "-r", str(fps), "-c:v", "libx264", "-crf", "14",
              "-preset", ENC_PRESET, "-pix_fmt", "yuv420p", dst])

    try:
        st = os.stat(src)
        sig = "%s_%d_%d_%d_%d_%dx%d_%d" % (
            os.path.splitext(os.path.basename(src))[0], int(st.st_mtime),
            st.st_size, int(seg_start * 100), int((dur + 0.3) * 100), w, h,
            int(fps))
        cached = os.path.join(BROLL_CACHE, sig + ".mp4")
        if os.path.exists(cached) and os.path.getsize(cached) > 0:
            return cached
        os.makedirs(BROLL_CACHE, exist_ok=True)
        # пишем во временное имя и переименовываем: параллельные рендеры
        # (несколько раскладок разом) не должны читать полуготовый файл.
        # Расширение .mp4 в конце ОБЯЗАТЕЛЬНО: ffmpeg выбирает формат контейнера
        # по расширению, и на «...part» он падает «Unable to choose an output
        # format» — а запасной путь ниже проглатывал бы это молча, оставляя кэш
        # вечно пустым (наступил на это при первом замере).
        tmp = os.path.join(BROLL_CACHE, "%s.%d.part.mp4" % (sig, os.getpid()))
        _encode(tmp)
        os.replace(tmp, cached)
        return cached
    except Exception:
        out = os.path.join(td, "%s_%s_%dx%d.mp4"
                           % (os.path.splitext(os.path.basename(src))[0],
                              int(seg_start * 100), w, h))
        _encode(out)
        return out


def _kb_zoom(img, z):
    """R14 (кен-бёрнс): центрический зум BGR-кадра без смены размера холста.
    z<=1 — кадр не трогаем (байт-в-байт при выключенном broll_motion)."""
    if z <= 1.0:
        return img
    h, w = img.shape[:2]
    zw, zh = int(round(w * z)), int(round(h * z))
    big = cv2.resize(img, (zw, zh), interpolation=cv2.INTER_LINEAR)
    x0, y0 = (zw - w) // 2, (zh - h) // 2
    return big[y0:y0 + h, x0:x0 + w]


def _kb_z(t, act, amp):
    """Текущий зум сцены: 1.0 → 1.0+amp линейно по окну вставки [t0..t1]."""
    span = max(1e-3, act["t1"] - act["t0"])
    p = min(1.0, max(0.0, (t - act["t0"]) / span))
    return 1.0 + amp * p


class _Clip:
    """Последовательное чтение нормализованного b-roll с зацикливанием."""
    def __init__(self, path):
        # b-roll нормализован ffmpeg (`prepare_broll`) — поворот там уже
        # впечатан, флага не остаётся. Автоповорот всё равно гасим: так кадр
        # приходит ровно «как в файле» на любой версии OpenCV (E4-63).
        self.cap = _no_auto_orient(cv2.VideoCapture(path))
    def next(self):
        ok, fr = self.cap.read()
        if not ok:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, fr = self.cap.read()
        return fr
    def close(self):
        self.cap.release()


def render(video_path, transcript_path, out, acc_name=None, fmt="B",
           inserts=None, sub_size=None, template=None, matte_backend="mediapipe",
           overlays=None, karaoke=False, karaoke_style=None,
           flashes=None, progress_bar=False, list_items=None, grade=None,
           titles=None, punch=None, punch_scale=None, punch_ramp=None,
           punch_style="smooth",
           cues=None, video2=None, compare_labels=None, sub_scale=1.0,
           sub_stroke=0, enc=os.environ.get("KARUSEL_ENC", "pipe"),
           broll_motion=0.0, style=None, hook_text=None, hook_sub=None,
           speaker_cuts=None):
    """Собрать говорящее видео: субтитры + композиция + вставки + исходный звук.

    sub_scale (E4-25): множитель кегля субтитра ДЛЯ ВХОД-АГНОСТИЧНЫХ ФОРМАТОВ
    (demo/compare/без-голоса, путь build_textmode). 1.0 (дефолт) — прежнее
    поведение байт в байт (split/cutout/pip/card/lower-third/text-behind идут
    через build() и sub_scale НЕ передают). >1 — база sub_size×sub_scale (74×3=222)
    с ПЕРЕНОСОМ СТРОК по ширине и жёстким пределом = кадр 1080, вертикаль
    прижата к низу безопасной зоны.

    broll_motion (R14, кен-бёрнс): амплитуда медленного зума b-roll по длине
    сцены (вставки). 0 (дефолт) — выкл, кадр не трогается (байт-в-байт);
    0.05 — зум 1.0→1.05 линейно от начала к концу окна вставки. Действует в
    ветках split-низ, cutout-фон и text-behind-фон; амплитуду держать ≤0.06
    (головокружение при переборе).

    enc (R11): путь кодирования. "pipe" (ДЕФОЛТ с 16.08.2026, P3) — кадры BGR
    пишутся прямо в stdin ffmpeg (-f rawvideo), звук вторым входом, ОДИН энкод
    libx264 crf18 (1 генерация потерь, минус полный проход диска). "legacy" —
    прежний путь: кадры через cv2.VideoWriter (mp4v) во временный silent.mp4,
    затем ВТОРОЙ энкод libx264 с муксом звука (2 генерации потерь).

    Почему дефолт наконец переключён. Путь pipe написан ещё в R11 и тогда же
    замерен как ЛУЧШИЙ по качеству (SSIM 0.9894 против 0.9845) и по размеру
    файла (−15%), но дефолтом не стал: не было регрессии раскладок, чтобы
    доказать отсутствие поломок. Регрессия с тех пор появилась
    (`scripts/regress_formats.py`, R50) — условие выполнено. Замер скорости
    (57-сек ролик): legacy тратит лишние 9.8 сек на запись mp4v-времянки плюс
    её повторное декодирование во втором энкоде. Внимание: эталон регрессии
    (`scripts/regress_baseline.json`) снимался на legacy — после переключения
    он ОБЯЗАН быть пересобран (`--write-baseline`), иначе поле size_bytes
    честно расходится.

    sub_stroke (R6): ширина тёмной обводки split-субтитра, px. 0 (дефолт) —
    чистый белый без обводки, байт-в-байт (реф. Пример2); >0 — обводка INK
    для контраста на светлом фоне шва. На не-split раскладки не влияет
    (там обводка всегда есть, stroke_w=9).

    Вставка (inserts[i]) — либо b-roll (есть 'src' = путь к видео банка/стока и
    'seg' = старт куска в нём), либо типографская плашка (есть 'text').
    Формат A: вставка на весь экран (b-roll — как в референсе 1), голова в углу.
    Формат B: голова крупно, вставка всплывает снизу карточкой.

    template="split" (реф. 2, Режим A завода): экран пополам — спикер сверху,
    b-roll снизу; когда активной вставки нет, спикер на весь кадр (сплит
    включается только под вставку). Перекрывает fmt. Субтитр — в нижней трети,
    в безопасной зоне, как в формате B.

    template="cutout" (реф. 1, Режим B завода): спикер ВЫРЕЗАН по силуэту
    (talk_matte, альфа по кадру) и лежит ПОВЕРХ b-roll на весь кадр; фон
    меняется под ним, спикер всегда на экране. Нет вставки — спикер на весь
    кадр. Субтитр — в нижней трети с обводкой (читается на любом фоне).
    matte_backend: 'mediapipe' (по умолчанию, без скачиваний) | 'rvm' (тоньше
    край, но тянет модель из сети).

    НАКЛАДНЫЕ ФОРМАТЫ (комбинируются поверх любой раскладки, дефолт — выключены):
      flashes      — формат 3, список окон слова-вспышки [{t0,t1,text}]; в каждом
                     окне поверх спикера всплывает крупное слово-акцент (не на
                     кадрах полноэкранной плашки-перебивки). Пусто → не вспыхивает.
      progress_bar — формат 6, тонкая полоса t/dur вверху в безопасной зоне.
      list_items   — формат 8, список [{t0,t1,no,total,title}]; в окне рисуется
                     счётчик «N / M» + заголовок пункта. Пусто → не рисуется.
      grade        — формат 9, имя пресета из GRADES (warm|duotone); ОДИН
                     ffmpeg-фильтр на финальном энкоде. None → без грейда.

    template="text-behind" (формат 4): слоёная сборка — фон (тёмный/акцент или
    b-roll, если попал в inserts) → КРУПНЫЙ заголовок (`titles`, поле блока) →
    силуэт спикера ПОВЕРХ текста (тот же talk_matte, что и cutout). Буквы уходят
    за плечи/голову; текст читается и из-за головы, и по краям/сверху. Корректен
    только на ЧИСТОЙ СОЛЬНОЙ съёмке (как cutout): на кадре с двумя людьми
    mediapipe вырежет обоих → двойной силуэт (ожидаемо на IMG_8138).
      titles: [{"t0","t1","text"}] — окна показа заголовка. Нет активного —
      заголовка нет (спикер на фоне).

    template="music" (P4, референс владельца IMG_4731.MP4): видео-эстетика без
    говорящей головы под музыку — исходник идёт на весь кадр (как demo, тем же
    cover-путём, без детекции лица/матирования), а поверх — ОДНА статичная
    подпись хук+призыв на весь ролик (не по-репличный субтитр). Текст —
    hook_text/hook_sub (обязателен hook_text, ValueError без него); строит
    _music_caption() один раз до цикла кадров, cues для этого шаблона не
    используются (вход-агностик, как demo/compare, но без --text: см.
    render_reels.build_textmode про авто-подбор хука из книги смыслов).

    punch (формат 7, ЗУМ-УДАР): [{"t0","t1"}] окна микро-наезда на лицо (хук/пик
    планировщика). Аддитивно ПОВЕРХ любой раскладки — зумится полноэкранный
    спикер (split-верх, cutout, text-behind, крупная голова). punch_scale —
    пик зума (дефолт 1.10, ограничен чтобы не срезать голову), punch_ramp —
    длина рампы в кадрах (4–6). punch=None → поведение не меняется.
    punch_style (R15): "smooth" (дефолт, байт-в-байт) — мягкий smoothstep-рамп;
    "step" — монтажный удар: рамп 1 кадр, окно целиком на пике (рекомендованный
    punch_scale для step — 1.18).

    style (R30, СЛОЙ СТИЛЯ КЛИЕНТА): None → дефолт = прежние константы движка,
    картинка байт-в-байт; str → путь к json-пресету (styles/*.json);
    style.Style → готовый объект. Из стиля берутся: гарнитура (font_family,
    файлы из fonts/), кегль субтитра и пол автоподбора, нарезка реплик, вид
    субтитра (clean|boxed|stroked: заливка/обводка/тень/подложка), акцент,
    позиции sub_cy по шаблонам, параметры караоке, зум-удар и БЕЗОПАСНАЯ ЗОНА
    (профиль из styles/platform_zones.json).
    ПРИОРИТЕТ: дефолт < style < явный аргумент. Всё, что пришло не-None
    (acc_name, sub_size, karaoke_style, punch_scale, punch_ramp), сильнее стиля;
    karaoke — store_true-флаг, поэтому правило «флаг ИЛИ style.karaoke.enabled».
    """
    _t_render0 = time.time()      # R45: замер времени рендера (render_secs/ratio)
    if enc not in ("legacy", "pipe"):
        raise ValueError('enc: ожидается "legacy" или "pipe", получено %r' % (enc,))

    # ── R30: СЛОЙ СТИЛЯ КЛИЕНТА ─────────────────────────────────────────────
    # st всегда есть (None → дефолт = константы движка). Явные аргументы бьют
    # стиль: подстановка идёт только там, где пришло None. Это ЕДИНСТВЕННАЯ
    # точка разрешения приоритета «дефолт < style < CLI» на весь конвейер.
    st = style if isinstance(style, style_mod.Style) else style_mod.load(style)
    if acc_name is None and isinstance(st.accent, str):
        acc_name = st.accent
    acc = e.ACCENTS[acc_name] if acc_name else tuple(st.accent_rgb)
    if karaoke_style is None:
        karaoke_style = st.karaoke_style
    karaoke = bool(karaoke) or st.karaoke_enabled
    if punch_scale is None:
        punch_scale = st.punch_scale
    if punch_ramp is None:
        punch_ramp = st.punch_ramp
    # БЕЗОПАСНАЯ ЗОНА из стиля. SAFE_* читают ~20 мест модуля (PIP, плашки,
    # вспышка, счётчик, автокегль, прижим ×N) — переписывать каждое означало бы
    # дифф в десять раз длиннее без выигрыша. Рендер однопроходный, присвоение
    # безусловное и идемпотентное: style=None кладёт те же 170/430/150/72, что
    # стоят в модуле, поэтому «грязного» состояния между вызовами не остаётся.
    global SAFE_TOP, SAFE_BOTTOM, SAFE_RIGHT, M
    SAFE_TOP, SAFE_BOTTOM = st.safe_top, st.safe_bottom
    SAFE_RIGHT, M = st.safe_right, st.safe_margin

    if grade is not None and grade not in GRADES:
        raise ValueError("неизвестный грейд %r (есть: %s)"
                         % (grade, ", ".join(GRADES)))

    # CLI-имена шаблонов A/B → внутренний fmt (ветки A/B в цикле ниже уже есть).
    # split/cutout рисуются по `template`, а PIP/крупная голова — по `fmt` при
    # template без спец-обработки, поэтому обнуляем template для этих двух.
    is_card = template == "card"          # R51: CTA-слово card, своя ветка ниже
    if template == "pip":
        fmt, template = "A", None
    elif template == "card":
        fmt, template = "B", None

    # ВХОД-АГНОСТИК (E4-24): если готовые cues переданы (из --text, речи нет) —
    # берём их и транскрипт не требуется. Старый путь (cues=None) читает
    # transcript.json ровно как раньше — байт в байт.
    if cues is None:
        segs = json.load(open(transcript_path, encoding="utf-8"))
        cues = (chunk_cues_karaoke(segs, max_words=st.karaoke_max_words,
                                   max_chars=st.karaoke_max_chars)
                if karaoke else
                chunk_cues(segs, max_words=st.sub_max_words,
                           max_chars=st.sub_max_chars))

    split = template == "split"
    # ×N-путь (перенос строк, центровка в контентной зоне SAFE_ZONE_W) —
    # раньше был завязан ТОЛЬКО на sub_scale>1.0, потому что demo/compare
    # всегда звали render() с sub_scale=3.0 (E4-25). Отмена E4-25 (16.08.2026,
    # sub_scale вернули к 1.0) молча роняла demo/compare в ОБЫЧНУЮ ветку
    # single/ml_wrap — ту, что T7b строил под sub_size_base=200 и pip-специфичные
    # sub_cy/SAFE_W; на demo/compare её content-zone центрирование давало
    # СУБТИТР ЗА КРАЕМ БЕЗОПАСНОЙ ЗОНЫ (замер: sub_box правым краем на 938 при
    # границе 930). Найдено сведением веток регрессией `regress_formats.py`, не
    # на глаз. Путь и масштаб — независимые оси: demo/compare ВСЕГДА идут ×N-
    # путём (это их родная, под них написанная центровка), а sub_scale только
    # держит кегль. Раньше это совпадало случайно (sub_scale>1.0 ⇒ demo/compare),
    # теперь развязано явно.
    WRAP = template in ("demo", "compare") or bool(sub_scale and sub_scale > 1.0)

    # R7: базовый кегль субтитра к конвенции коротких видео (5–8% высоты кадра).
    # Дефолт 100 px (5.2% от 1920) для обычных раскладок; split оставлен 74 —
    # у шва свой стиль (реф. Пример2); ×N-путь (sub_scale>1, demo/compare) тоже
    # 74 — его база откалибрована как 74×3=222 (E4-25). Явно переданный
    # sub_size уважается без изменений (обратная совместимость). R30: оба числа
    # живут в стиле (sub_size_split / sub_size_base), дефолт — те же 74/100.
    if sub_size is None:
        sub_size = st.sub_size_for(split or WRAP)

    # АВТОПОДБОР КЕГЛЯ СУБТИТРА ПОД БЕЗОПАСНУЮ ШИРИНУ. Субтитр центрован, поэтому
    # его правый край не должен заезжать под колонку действий Instagram справа
    # (SAFE_RIGHT). Ловушка, которую поймала verify_talk на IMG_8138: длинная
    # реплика при кегле 74 давала ширину 958 px (правый край 1019 > 930) и уходила
    # под кнопки. Ужимаем кегль, пока самая широкая реплика не влезет; пол — 34.
    # R30: вид субтитра решает стиль. split исторически рисуется ЧИСТЫМ белым без
    # обводки/тени (реф. Пример2) — это поведение включено флагом стиля
    # split_uses_clean (дефолт True = как было; R6 sub_stroke по-прежнему может
    # добавить шву тёмную обводку). Остальные раскладки идут по kind:
    #   stroked — обводка stroke_w + тень (дефолт движка, байт-в-байт);
    #   clean   — только заливка; boxed — заливка на скруглённой подложке.
    # Правило kind→(обводка,тень) взято из styles/make_previews.py::text_sprite,
    # чтобы кадр совпал с уже утверждёнными превью R32.
    SPLIT_CLEAN = split and st.split_uses_clean
    if SPLIT_CLEAN:
        # E4-49: белый здесь — тот же единый WHITE, что и у караоке (был литерал
        # (255,255,255), совпадавший с ним по значению, но не по источнику).
        sub_fill, sub_sw, sub_sh, sub_box_kw = WHITE, sub_stroke, False, None
    else:
        sub_fill = st.sub_fill
        sub_sw = st.sub_stroke_w if st.sub_kind == "stroked" else 0
        sub_sh = st.sub_shadow and st.sub_kind == "stroked"
        sub_box_kw = (dict(fill=st.box_fill, alpha=st.box_alpha,
                           pad_x=st.box_pad_x, pad_y=st.box_pad_y,
                           radius=st.box_radius)
                      if st.sub_kind == "boxed" else None)
    SUB_KW = dict(stroke=st.sub_stroke_fill, stroke_w=sub_sw, pad=st.sub_pad,
                  shadow=sub_sh, shadow_dy=st.sub_shadow_dy,
                  shadow_alpha=st.sub_shadow_alpha, box=sub_box_kw,
                  # БЕЛОЕ СВЕЧЕНИЕ (владелец 28.08.2026) — вместо плашки.
                  glow=st.sub_glow, glow_alpha=st.sub_glow_alpha)
    # караоке-шов и раньше игнорировал R6 sub_stroke (обводка там всегда 0) —
    # оставляем как было, чтобы sub_stroke>0 не менял караоке-split задним числом.
    # T1: box=sub_box_kw — раньше boxed-подложка сюда не пробрасывалась вовсе
    # (_karaoke_sprite параметра box не принимала), поэтому sub_style.kind =
    # boxed молча не работал на караоке-субтитрах — а караоке включено по
    # умолчанию для ВСЕХ шести говорящих раскладок (R9). SPLIT_CLEAN (белый
    # без подложки, реф. Пример2) по-прежнему сильнее: подложка не рисуется,
    # пока style явно не снял split_uses_clean.
    # СВЕЧЕНИЕ и в караоке тоже. 02.09.2026: glow стоял только в SUB_KW, а при
    # включённом караоке строку рисует _karaoke_sprite со СВОИМ набором — фон
    # вокруг букв не рисовался вовсе, хотя в пресете был задан. Тот же класс
    # ловушки, что с числом слов: настройка есть, читает её не тот путь.
    KAR_KW = dict(stroke_w=(0 if SPLIT_CLEAN else sub_sw), shadow=sub_sh,
                  glow=st.sub_glow, glow_alpha=st.sub_glow_alpha,
                  fill=st.karaoke_fill, stroke=st.sub_stroke_fill,
                  scale_pop=st.karaoke_scale_pop,
                  plate_pad=st.karaoke_plate_pad,
                  plate_radius=st.karaoke_plate_radius,
                  box=(None if SPLIT_CLEAN else sub_box_kw))

    # РЕГИСТР субтитра-реплики (sub_style.case). "keep" (дефолт) — текст идёт в
    # спрайт как есть, БАЙТ-В-БАЙТ со старым движком (той же строкой-объектом);
    # "upper" — реплика поднимается в ПРОПИСНЫЕ. Касается ТОЛЬКО субтитра: hook/
    # заголовок (_title_behind) и плашки-тезисы рисуются отдельным кодом и здесь
    # не затрагиваются. Для караоке апим только отображаемое слово ("w"), тайминги
    # (s/e) не трогаем — подсветка ездит по тем же индексам.
    sub_upper = (st.sub_case == "upper")
    # СНЯТИЕ ЗНАКОВ ПРЕПИНАНИЯ (владелец 02.09.2026). Делается ОДИН раз
    # здесь, до постройки спрайтов: и обычный субтитр, и караоке берут
    # текст отсюда, поэтому расхождения между ними быть не может.
    if st.sub_strip_punct:
        _PUNCT = ",.;:!?…"
        for _c in cues:
            _c["text"] = _c["text"].translate({ord(ch): None for ch in _PUNCT}).strip()
            for _w in _c.get("words", []):
                _w["w"] = _w["w"].translate({ord(ch): None for ch in _PUNCT}).strip()

    def _make_sprites(size, wrap_w=None):
        f = st.make_font("Bold" if split else "Black", size)
        # фабрика шрифта нужного кегля для scale-pop активного слова (формат 1)
        mk = lambda s: st.make_font("Bold" if split else "Black", s)
        for c in cues:
            if karaoke:
                kwords = ([dict(w, w=w["w"].upper()) for w in c["words"]]
                          if sub_upper else c["words"])
                # спрайт на каждый индекс активного слова + база (idx=-1, вся
                # строка белая) — база задаёт ширину для автоподбора кегля и
                # рамку sub_box, ею же рисуем паузы между словами. wrap_w (×N) —
                # многострочная раскладка, чтобы крупное караоке не вылезло за кадр.
                if wrap_w:
                    c["kspr"] = [_karaoke_sprite_wrapped(kwords, mk, size, i,
                                                         acc, split, karaoke_style,
                                                         wrap_w, **KAR_KW)
                                 for i in range(len(kwords))]
                    c["spr"] = _karaoke_sprite_wrapped(kwords, mk, size, -1,
                                                       acc, split, karaoke_style,
                                                       wrap_w, **KAR_KW)
                else:
                    c["kspr"] = [_karaoke_sprite(kwords, mk, size, i, acc,
                                                 split, karaoke_style, **KAR_KW)
                                 for i in range(len(kwords))]
                    c["spr"] = _karaoke_sprite(kwords, mk, size, -1, acc,
                                               split, karaoke_style, **KAR_KW)
                continue
            # split — чистый белый жирный без обводки (реф. Пример2); R6:
            # sub_stroke>0 добавляет split-субтитру тёмную обводку (INK) для
            # контраста на светлом шве — дефолт 0, байт-в-байт (реш. Валерии).
            # A/B/cutout — Black с обводкой (читается на любом фоне). wrap_w (×N) —
            # многострочный спрайт с переносом по ширине (demo/compare/без-голоса).
            # R30: конкретика вида (заливка/обводка/тень/подложка) — в SUB_KW.
            txt = c["text"].upper() if sub_upper else c["text"]
            if wrap_w:
                c["spr"] = _text_sprite_wrapped(txt, f, sub_fill, wrap_w,
                                                line_gap=st.sub_line_gap, **SUB_KW)
            else:
                c["spr"] = _text_sprite(txt, f, sub_fill, **SUB_KW)

    SAFE_W = VW - 2 * SAFE_RIGHT          # симметричный зазор от правой колонки

    def _fits(cap_w):
        """Самая широкая реплика влезает в cap_w? (спрайты уже построены)"""
        return (not cues) or max(c["spr"].width for c in cues) <= cap_w

    def _fit_size(hi, lo, cap_w, wrap_w=None):
        """E4-47: САМЫЙ КРУПНЫЙ кегль из [lo..hi], при котором самая широкая
        реплика влезает в cap_w. Прежний спуск фиксированным шагом (−4 для
        обычных раскладок, −6 для ×N) промахивался мимо достижимого максимума:
        с базы 100 цепочка 100,96,…,52,48 перескакивала через 51 и садилась на
        48 — субтитр выходил МЕЛЬЧЕ, чем при старой базе 74 (та давала ровно 50).
        Ширина однострочного спрайта монотонна по кеглю, поэтому берём деление
        пополам: точный максимум и вдвое меньше построений спрайтов (с базы 100
        было 13, стало ~9). Возвращает выбранный кегль; спрайты в cues остаются
        построенными ИМЕННО этим кеглем.
        lo — «пол», влезание на нём не проверяется (как и в прежнем цикле:
        дойдя до пола, движок отдавал его безусловно)."""
        _make_sprites(hi, wrap_w=wrap_w)
        if hi <= lo or _fits(cap_w):
            return hi
        good, bad = lo, hi                 # good — влезает (по договорённости), bad — нет
        while bad - good > 1:
            mid = (good + bad) // 2
            _make_sprites(mid, wrap_w=wrap_w)
            if _fits(cap_w):
                good = mid
            else:
                bad = mid
        _make_sprites(good, wrap_w=wrap_w)   # вернуть спрайты финального кегля
        return good

    # T7b: перенос на 2 строки для ОБЫЧНЫХ (не-×N) раскладок — отдельный от
    # WRAP флаг. Первая попытка (WRAP=True целиком) сломала pip: анкор-к-низу
    # безопасной зоны у sub_cy (ниже, "если WRAP and cues") рассчитан на ×N
    # путь demo/compare, где под субтитром просто b-roll во весь кадр; у pip
    # там СПЕЦИАЛЬНО низкий sub_cy=560 (fmt_A), чтобы не спорить с маленьким
    # окошком спикера внизу справа — анкор-к-низу игнорировал это и утащил
    # 2-строчный субтитр вниз, ПРЯМО НА ЛИЦО в окошке (замер: 63% лица закрыто
    # на 5.2с, verify_talk(strict=True) FAIL на T7b_valeria_1883_pip). ml_wrap
    # держит перенос/центрирование по ширине как у WRAP, но НЕ трогает
    # per-раскладочный sub_cy — только подрезает его в безопасную зону, если
    # 2-строчный блок туда не влезает целиком (см. ниже).
    ml_wrap = False
    if not WRAP:
        size = _fit_size(sub_size, st.sub_size_floor, SAFE_W)
        # T7b (владелец, разбор T7_valeria_1883_pip_SHEET.jpg): удвоение
        # sub_size_base/sub_size_split в T7 визуально «не сработало» для
        # большинства реальных реплик — ОДНОСТРОЧНЫЙ `_fit_size` УЖЕ ужимал
        # кегль ниже потолка ДО удвоения (реплика просто не влезала в одну
        # строку на SAFE_W=780px), так что подъём потолка 100→200/74→148 не
        # менял фактический возвращаемый размер для этих фраз вовсе —
        # `_fit_size` по-прежнему упирался в ширину, не в потолок.
        #
        # Чиним переносом на 2 строки ВМЕСТО сжатия кегля в одну — той же
        # механикой, что уже есть для ×N-пути demo/compare чуть ниже
        # (_karaoke_sprite_wrapped/_text_sprite_wrapped: активное слово
        # караоке подсвечивается на СВОЕЙ строке). Не пишем перенос с нуля —
        # переиспользуем `_fit_size(..., wrap_w=...)`, когда перенос даёт
        # кегль КРУПНЕЕ однострочного (пробуем оба варианта и берём больший;
        # если перенос не помогает — напр. одно очень длинное слово —
        # остаёмся на однострочном сжатии, байт-в-байт как раньше).
        if cues and size < sub_size:
            # тот же вычет левого отступа, что и на ×N-пути ниже: блок при
            # выравнивании к левому краю ставится от safe_left+M, и без этого
            # двухстрочная реплика вылезала под колонку кнопок ровно на M.
            SAFE_ZONE_W2 = VW - SAFE_RIGHT
            if st.sub_align == "left":
                SAFE_ZONE_W2 -= (st.safe_left + M)
            wrap_w2 = SAFE_ZONE_W2 - 60
            size2 = _fit_size(sub_size, st.sub_size_floor, SAFE_ZONE_W2,
                              wrap_w=wrap_w2)
            if size2 > size and not st.sub_single_line:
                # single_line: вторая строка запрещена владельцем 02.09.2026,
                # даже если она позволяет кегль крупнее.
                size = size2
                ml_wrap = True
            else:
                _make_sprites(size)   # откат: 2-строчная проба выше уже
                                       # перезаписала cues — вернуть однострочные
    else:
        # ×N (demo/compare/без-голоса): крупный субтитр с переносом строк. Блок
        # центрируется В КОНТЕНТНОЙ ЗОНЕ [0 .. VW-SAFE_RIGHT] (левее колонки кнопок
        # Instagram), поэтому его предел ширины — SAFE_ZONE_W, а не весь кадр:
        # так строка ×3 остаётся в безопасной зоне и не лезет под кнопки. Ломаем по
        # ширине с запасом на pad; если ОДНО слово всё равно шире зоны — ужимаем
        # кегль (пол = sub_size, не мельче исходного).
        SAFE_ZONE_W = VW - SAFE_RIGHT            # контентная ширина (0..930)
        # 28.08.2026. При выравнивании К ЛЕВОМУ КРАЮ блок ставится от
        # safe_left+M (см. _sub_left_x), а подгонка считала ширину от НУЛЯ —
        # это смещение в неё не входило, и строка залезала под колонку кнопок
        # ровно на M (замер: 7-66 px в 4 репликах из 53). Вычитаем отступ, из
        # которого блок реально начинается. Для center/right формула прежняя
        # байт-в-байт: center симметричен, right прижат к самой границе зоны.
        if st.sub_align == "left":
            SAFE_ZONE_W -= (st.safe_left + M)
        wrap_w = SAFE_ZONE_W - 60                # запас на pad спрайта
        frame_cap = SAFE_ZONE_W
        # E4-47: тот же точный фит, что и на однострочном пути (было −6 от 222,
        # т.е. промах мимо достижимого).
        #
        # T-loud/3: ПОЛ АВТОПОДБОРА. Раньше полом всегда стоял sub_size («×N-
        # субтитр не бывает мельче исходного»). Это было верно, пока demo/compare
        # ехали с sub_scale=3.0: потолок 222 стоял ВЫШЕ пола 74, деление пополам
        # работало. После отмены E4-25 (16.08.2026, sub_scale вернули к 1.0) и
        # удвоения кегля в T7 (sub_size_split 74→148) потолок СОВПАЛ с полом:
        # _fit_size(148, 148, ...) отдаёт hi ПЕРВОЙ ЖЕ СТРОКОЙ (`if hi <= lo`),
        # НЕ проверив, влезает ли самая широкая реплика в контентную зону. Отсюда
        # и берётся известный дефект demo/compare: спрайт 945 px в зоне 930,
        # sub_box правым краем на 938 при границе 930 (замер в эталоне до правки,
        # verify_talk ругался «вылезает под колонку кнопок Instagram на 8 px»).
        # Лечим полом: когда наверх не масштабируем (sub_scale<=1), пол — общий
        # движковый sub_size_floor, тот же, что на однострочном пути. При
        # sub_scale>1 всё как было: пол sub_size, до него дело не доходит.
        _hi = int(round(sub_size * sub_scale))
        _lo = sub_size if _hi > sub_size else st.sub_size_floor
        size = _fit_size(_hi, _lo, frame_cap, wrap_w=wrap_w)

    # R51: геометрия CTA-слова card — один раз, кегль = 2×фактического кегля
    # субтитра этого ролика (size выше, уже автоподобранный под SAFE_W), с
    # собственным автоподбором вниз под ту же безопасную ширину. Меряем
    # ПОЛНУЮ ширину плашки (текст + отступы CARD_CTA_PAD_X с обеих сторон,
    # те же, что рисует _draw_card_cta) — иначе плашка может вылезти за
    # SAFE_RIGHT на величину отступа, как у голого textlength без запаса.
    # overlays["cta"] уважается, если передан явно (--cta), иначе бренд-
    # дефолт завода «РОСТ».
    card_cta_word = card_cta_font = card_cta_bbox = None
    if is_card:
        card_cta_word = (overlays.get("cta") if overlays else None) or "РОСТ"
        _cta_probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        _cta_size = 2 * size
        card_cta_font = st.make_font("Black", _cta_size)
        while (_cta_size > st.sub_size_floor
               and (_cta_probe.textlength(card_cta_word, font=card_cta_font)
                    + 2 * CARD_CTA_PAD_X) > SAFE_W):
            _cta_size -= 4
            card_cta_font = st.make_font("Black", _cta_size)
        card_cta_bbox = _cta_probe.textbbox((0, 0), card_cta_word,
                                            font=card_cta_font)

    # music (P4): подпись хук+призыв строится РАЗ на весь ролик, до цикла
    # кадров — не по кадру и не по реплике, см. _music_caption. hook_text
    # обязателен: без текста подпись рисовать не из чего, и молчаливый пустой
    # ролик хуже понятной ошибки на этапе сборки.
    music_layer = music_box = None
    if template == "music":
        if not hook_text:
            raise ValueError('template="music" требует hook_text (--hook или '
                             "авто-подбор из книги смыслов, см. build_textmode)")
        music_layer, music_box = _music_caption(hook_text, hook_sub or "")

    # (cap, rot) одной парой: угол согласован с тем, что реально отдаёт cv2 на
    # этой машине, — иначе на OpenCV 5 спикер лёг бы на бок (E4-63, _open_src).
    cap, src_rot = _open_src(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # compare (E4-23): второй клип для стопки «плохо/хорошо». Открываем свою
    # дорожку и флаг поворота; primary — первый клип (его длина/fps ведут цикл),
    # второй держит последний кадр, если короче. Плашки — _compare_label.
    cap2 = None
    src_rot2 = 0
    cmp_bad, cmp_good = "НЕ ПРАВИЛЬНО  ✗", "ПРАВИЛЬНО  ✓"
    if compare_labels:
        cmp_bad = compare_labels.get("bad", cmp_bad)
        cmp_good = compare_labels.get("good", cmp_good)
    if template == "compare" and video2:
        cap2, src_rot2 = _open_src(video2)
    cmp_top = cmp_bot = None                 # предрендер плашек (раз на ролик)
    if template == "compare":
        cmp_top = _compare_label(cmp_bad, acc, good=False)
        cmp_bot = _compare_label(cmp_good, acc, good=True)
    _last2 = None                            # последний прочитанный кадр cap2

    # длительность для полосы-досмотра (формат 6): доля t/dur. Берём из счётчика
    # кадров источника; если недоступен — прогресс считаем по факту (fi/nframes≈1).
    nframes = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    dur_total = (nframes / fps) if (nframes and fps) else 0.0

    # предрендер слов-вспышек (формат 3): спрайт на окно, анимация — по кадрам
    flash_list = [dict(t0=fl["t0"], t1=fl["t1"],
                       spr=_flash_sprite(fl["text"], acc))
                  for fl in (flashes or [])]
    items = list(list_items or [])          # формат 8, окна счётчика пунктов

    # геометрия PIP (формат A)
    # T7b (владелец, разбор T7_valeria_1883_pip_SHEET.jpg): формула T7
    # («2 см вниз + равные отступы, выведенные друг из друга») дала окно
    # НЕ в углу — производный нижний отступ (374px) зеркально утянул окно и
    # по горизонтали, и оно уехало в центр кадра. Владелец отменил формулу
    # ЦЕЛИКОМ и дал новое прямое указание:
    #   1) окно должно быть ДЕЙСТВИТЕЛЬНО маленьким;
    #   2) отступ — фиксированный МАЛЕНЬКИЙ и ОДИНАКОВЫЙ, не выведенный из
    #      SAFE_BOTTOM-формулы.
    # РАЗМЕР: старые 384×682 = 35–36% кадра по каждой стороне — не «маленькое
    # окошко». Уменьшаем линейно в ~1.75 раза (владелец предложил диапазон
    # 1.5–2×, конкретные 220×390 — тоже его пример), пропорция 384:682
    # сохранена (390/220=1.773 ≈ 682/384=1.776, расхождение на округлении).
    # Готовой отдельной "маленькой" конвенции PIP-инсета в других раскладках/
    # пресетах нет (grep по проекту) — ориентир только этот.
    pip_w, pip_h = 220, 390
    # ПОЗИЦИЯ: отступ 28px — фиксированный, НЕ выводится из pip_h/SAFE_BOTTOM
    # (в отличие от T7). 28 — не с потолка: это тот же box_pad_x/pad стиля
    # субтитра-плашки (style._DEFAULT["sub_style"]["box_pad_x"]=28) — заимствуем
    # уже принятый в заводе масштаб «небольшого отступа», а не придумываем новый.
    #
    # T7b/2 (владелец, разбор кадра ПОПИКСЕЛЬНО): первая версия (полный
    # SAFE_RIGHT/SAFE_BOTTOM) не читалась как «угол» — под окном оставался
    # пустой отступ 458px до низа кадра (БОЛЬШЕ высоты самого окна, 390px) —
    # выглядело как «где-то справа посередине». ПРАВЫЙ край не трогаем (жалобы
    # не было, отступ 178px от истинного края уже держит окно левее реальной
    # колонки кнопок IG x>930 с запасом). НИЖНИЙ — компромисс: SAFE_BOTTOM=430
    # защищает ЧИТАЕМЫЙ ТЕКСТ субтитра (если он туда попадёт — нечитаем
    # полностью), а PIP — просто видео-окно (частичный наезд подписи/иконки IG
    # на угол окна не убивает его функцию так, как убил бы субтитр). Поэтому
    # берём ПОЛОВИНУ резерва (215 вместо 430) — весь резерв не используется в
    # pip НИКЕМ (ни субтитр, ни CTA-плашка «РОСТ» туда не заходят — субтитр на
    # sub_cy=560, CTA на y≈300, оба у верха; проверено численно ниже), поэтому
    # часть риска безопасно отдаём под «похоже на угол». Итог: отступ от
    # истинного нижнего края упал 458→243px (меньше высоты окна) — читается
    # как угол; риск попасть под РЕАЛЬНУЮ подпись/иконки Instagram (не
    # отрисовку завода) остаётся, но уменьшенный вдвое, а не нулевой.
    # Если владелец хочет буквальный угол (риск полностью) или, наоборот,
    # весь SAFE_BOTTOM обратно (риск ноль, но не «угол») — один параметр.
    #
    # T7b/3 (владелец, круг 4): выбрал буквальный угол. Резерв снят целиком —
    # тот же принцип, что уже применён к правому краю (PIP_MARGIN=28 от
    # ИСТИННОГО края кадра, без промежуточного SAFE_*-отступа). Отступ снизу
    # теперь равен отступу справа по построению (28=28, симметрично), а не
    # выводится из SAFE_BOTTOM. Итог: отступ от истинного нижнего края
    # 243→28px. Риск, озвученный в комментарии выше, реализуется теперь
    # полностью (не наполовину): окно может частично попасть под РЕАЛЬНУЮ
    # подпись/иконки Instagram внизу кадра (которых в самом видео нет и
    # движок их не видит) — это осознанный выбор владельца, а не дефект.
    PIP_MARGIN = 28
    PIP_BOTTOM_RESERVE = 0                      # буквальный угол — весь резерв снят
    pip_x = (VW - SAFE_RIGHT) - PIP_MARGIN - pip_w
    pip_y = (VH - PIP_BOTTOM_RESERVE) - PIP_MARGIN - pip_h
    pip_mask = _round_mask(pip_w, pip_h, 40)
    # ПРОВЕРКА ПЕРЕСЕЧЕНИЯ с sub_box (T7b/2, владелец: "докажи численно, не на
    # глаз") — sub_box считается чуть ниже по коду (после sub_cy), поэтому
    # сверяем постфактум и падаем громко, если геометрия когда-нибудь разъедется
    # (стиль клиента подвинет sub_cy/pip_w и т.п.), а не тихо рисуем внахлёст.
    _pip_box_for_check = (pip_x, pip_y, pip_x + pip_w, pip_y + pip_h) if fmt == "A" else None

    # центр строки субтитра. split — у самого шва, в половине спикера (как в
    # реф. Пример2): нижняя половина с b-roll остаётся чистой. lower-third —
    # субтитр ВЫШЕ плашки-ленты (LT_Y=1235), разведены по вертикали.
    # R30: все семь позиций — в стиле (sub_cy_overrides), дефолт тот же.
    sub_cy = (st.sub_cy["split"] if split
              else st.sub_cy["lower-third"] if template == "lower-third"
              else st.sub_cy["fmt_A"] if fmt == "A"
              else st.sub_cy["default"])
    # text-behind: субтитр в нижней трети (как cutout/B), заголовок сидит выше
    # (центр кадра) — они разведены по вертикали и не спорят за место.
    if template == "text-behind":
        sub_cy = st.sub_cy["text-behind"]
    # demo (E4-24): процесс-клип на весь кадр, подписи в нижней трети (как cutout).
    # compare (E4-23): два клипа стопкой; подписи под нижним клипом, ниже шва.
    if template == "demo":
        sub_cy = st.sub_cy["demo"]
    elif template == "compare":
        # нижняя треть, но ВНУТРИ безопасной зоны (низ выше VH-SAFE_BOTTOM=1490):
        # под нижним «правильным» клипом, не спорит с плашками-ярлыками.
        sub_cy = st.sub_cy["compare"]

    def _anchor_bottom(sh):
        """cy для блока высотой sh, прижатого низом к границе безопасной зоны.
        Верхняя граница сильнее: очень высокий блок лучше упереть в SAFE_TOP,
        чем срезать ему макушку кадром."""
        cy = (VH - SAFE_BOTTOM - SUB_BOTTOM_GAP) - sh // 2
        return max(cy, SAFE_TOP + sh // 2)

    # ×N-субтитр (demo/compare/без-голоса): крупная многострочная подпись может
    # быть высокой — ПРИЖИМАЕМ её нижний край к низу безопасной зоны и растим
    # вверх, чтобы блок не уехал под ник/кнопки и целиком остался в кадре.
    if WRAP and cues:
        sub_cy = _anchor_bottom(max(c["spr"].height for c in cues))
    elif (cues and fmt != "A" and not split
          and template in SUB_BOTTOM_ANCHORED and not st.sub_cy_force):
        # sub_cy_force (T-fmt): анкор к низу ПЕРЕКРЫВАЛ заданный пресетом центр —
        # «ставить РОВНО в sub_cy» на card/B не работало вовсе (замер: строка на
        # 0.70 высоты при цели 0.59 из реф-замера). Форс выключает анкор; дефолт
        # (cy_force=false) — прежний анкор байт-в-байт.
        # ПОЛНОКАДРОВЫЕ РАСКЛАДКИ (card/B, cutout, text-behind): тот же анкор к
        # низу, что у ×N выше. Обоснование и замеры — у SUB_BOTTOM_GAP; коротко:
        # фиксированный центр 1200 держал не расстояние до интерфейса, а само
        # положение центра, из-за чего низ блока гулял 1265..1524 в зависимости
        # от подобранного кегля. Под субтитром у этих раскладок ничего нет —
        # значит и держать его выше границы зоны незачем.
        sub_cy = _anchor_bottom(max(c["spr"].height for c in cues))
    elif ml_wrap and cues:
        # T7b: 2-строчный перенос для обычных раскладок держит ШТАТНЫЙ sub_cy
        # раскладки (см. комментарий у ml_wrap выше — anchor-к-низу тут ломал
        # pip), просто не даём выросшему в высоту блоку вылезти за
        # SAFE_TOP/SAFE_BOTTOM: подрезаем cy к границам, если не влезает.
        sh_max = max(c["spr"].height for c in cues)
        half = sh_max // 2
        sub_cy = min(max(sub_cy, SAFE_TOP + half), VH - SAFE_BOTTOM - half)

    # рамка субтитра для checks.verify_talk: объединение всех строк. Считается
    # здесь, потому что кегль и sub_cy знает только движок — снаружи это было бы
    # догадкой, а проверка безопасной зоны на догадке не стоит ничего. Для ×N
    # (WRAP) блок центрируется в КОНТЕНТНОЙ зоне [0..VW-SAFE_RIGHT] (левее колонки
    # кнопок Instagram); обычные форматы — центр кадра, формула байт в байт.
    # T7b: ml_wrap (2-строчный перенос обычных раскладок) центрируется ТАК ЖЕ,
    # как WRAP, по ширине — перенос считался под контентную ширину
    # (SAFE_ZONE_W2), плоский центр кадра дал бы лишних ~75px вправо, под
    # колонку кнопок Instagram.
    # ВЫРАВНИВАНИЕ блока субтитра по горизонтали (sub_style.align). "center"
    # (дефолт) — прежняя формула БАЙТ-В-БАЙТ (для ×N/ml_wrap центр контентной
    # зоны, иначе центр кадра). "left"/"right" — прижим к краю безопасной зоны:
    # слева safe_left+M, справа VW-SAFE_RIGHT-M (не под колонку кнопок). Левый
    # край блока считает одна функция — ею же покадрово вклеиваем спрайт, чтобы
    # рамка в meta совпадала с реально нарисованным.
    def _sub_left_x(width):
        # Выравниваем в КОНТЕНТНОЙ зоне [safe_left .. VW-SAFE_RIGHT] — той же, что
        # движок держит для ×N-переноса (SAFE_ZONE_W). Левый край открыт (кнопок
        # нет) — отступаем на margin ради воздуха; правый упирается в КОЛОНКУ
        # КНОПОК Instagram (VW-SAFE_RIGHT) — там прижимаем блок вплотную к границе
        # зоны, а зазор от кнопок даёт прозрачный pad самого спрайта (буквы под
        # кнопки не заходят). Иначе на широких репликах (subs-2 ~0.69 ширины) ещё
        # один margin справа утащил бы «правый» блок левее центра кадра.
        if st.sub_align == "left":
            return st.safe_left + M
        if st.sub_align == "right":
            return (VW - SAFE_RIGHT) - width
        # ЦЕНТР — ОТ СЕРЕДИНЫ КАДРА (владелец 02.09.2026: «строка по середине
        # экрана по горизонтальной линии»). БЫЛО: при переносе строк центр брался
        # от контентной зоны (VW - SAFE_RIGHT)//2 = 465 вместо 540 — текст
        # систематически уезжал влево на 75 px. Владелец увидел это глазами,
        # а я до того сверял только значение align в пресете, не пиксели.
        # Правая колонка кнопок Instagram при этом остаётся безопасной, потому
        # что реплика ограничена двумя словами: длинная строка сюда не приходит.
        # Центр истинный, НО блок не выпускаем за безопасную зону: длинная
        # строка иначе залезла бы под колонку кнопок Instagram. На коротких
        # репликах (2 слова) зажим не срабатывает и центр остаётся ровным.
        _x = (VW - width) // 2
        return max(0, min(_x, (VW - SAFE_RIGHT) - width))

    sub_box = None
    if cues:
        sw = max(c["spr"].width for c in cues)
        sh = max(c["spr"].height for c in cues)
        if st.sub_align == "center":
            # рамка центра — прежняя формула движка БАЙТ-В-БАЙТ (симметрия cx±sw//2
            # для ×N/ml_wrap; иначе (VW∓sw)//2), meta не меняется на дефолте.
            if WRAP or ml_wrap:
                cx = (VW - SAFE_RIGHT) // 2
                sub_box = (cx - sw // 2, sub_cy - sh // 2,
                           cx + sw // 2, sub_cy + sh // 2)
            else:
                sub_box = ((VW - sw) // 2, sub_cy - sh // 2,
                           (VW + sw) // 2, sub_cy + sh // 2)
        else:
            sx = _sub_left_x(sw)
            sub_box = (sx, sub_cy - sh // 2, sx + sw, sub_cy + sh // 2)

    # T7b/2: pip-окно и рамка субтитра (sub_box, самый широкий/высокий вариант
    # на весь ролик, а не один кадр) НЕ должны пересекаться. Проверяем
    # численно, а не на глаз (было пересечение на реальном рендере
    # T7b_valeria_1883_pip до этой правки — субтитр падал на угол окна).
    # Падаем громко: если стиль/раскладка когда-нибудь сведут их вместе,
    # молчаливый нахлёст хуже понятной ошибки на этапе сборки.
    if _pip_box_for_check is not None and sub_box is not None:
        sx0, sy0, sx1, sy1 = sub_box
        px0, py0, px1, py1 = _pip_box_for_check
        ox = max(0, min(sx1, px1) - max(sx0, px0))
        oy = max(0, min(sy1, py1) - max(sy0, py0))
        if ox > 0 and oy > 0:
            raise RuntimeError(
                "pip: рамка субтитра %r пересекает PIP-окно %r (нахлёст "
                "%dx%d px) — сдвинь sub_cy['fmt_A'] или геометрию pip_x/"
                "pip_y/pip_w/pip_h, см. T7-style-tweaks.md §6"
                % (sub_box, _pip_box_for_check, ox, oy))

    # детектор лица: центровка головы в верхней половине split + зум-удар по лицу
    # (punch-in, формат 7) в любой полноэкранной раскладке + compare (гарантия
    # головы в обоих окнах, E4-25).
    det = None
    # 28.08.2026: добавлен card — но ПО is_card, а не по template. Ловушка:
    # выше (строка ~2128) раскладка card НОРМАЛИЗУЕТСЯ в fmt="B", template=None,
    # и к этому месту template уже None. Первая попытка добавить сюда "card"
    # в проверку template не срабатывала никогда, и это стоило пяти пересборок
    # с побайтово одинаковым замером.
    # Без детектора на card были МЁРТВЫ сразу три механики: уклонение подписи от
    # лица (_sub_face_box возвращал None), подъём лица (speaker_face_y) и
    # привязка широкого плана к лицу — то есть правило владельца «текст не
    # должен закрывать лицо никогда» на этой раскладке не работало вовсе.
    if template in ("split", "compare") or is_card or punch:
        import mediapipe as mp
        det = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5)

    # W1: СГЛАЖИВАНИЕ ТРЕКА ЛИЦА. Детектор зовётся покадрово (как и раньше), но
    # его сырая рамка больше не уходит в позицию кропа напрямую: talk_reframe
    # гасит микродрожание экспонентой, телепортирует кадр на склейке и держит
    # последний известный центр в пропуске детекции. Один сглаживатель на ролик,
    # состояние — по ключу места вызова. Тумблер KARUSEL_REFRAME_SMOOTH=0 →
    # прежнее поведение байт в байт (само-baseline, как KARUSEL_SUB_AVOID).
    smooth = None
    if det is not None and os.environ.get("KARUSEL_REFRAME_SMOOTH", "1") != "0":
        import talk_reframe
        smooth = talk_reframe.Smoother()

    # W2/F1: РАЗРЕЖЕННАЯ ДЕТЕКЦИЯ. Детектор перестаёт зваться на каждом кадре
    # полного разрешения: кадр ужимается до 640 px и сэмплируется 5 раз в секунду
    # (донор chopify: DET_W=640, DET_FPS=5) — на 30 fps это каждый 6-й кадр, то
    # есть примерно в 6 раз меньше вызовов и на порядок меньше пикселей в каждом.
    # Промежутки закрывает тот же сглаживатель W1: между сэмплами держится
    # последняя рамка, а окно кадрирования доезжает до неё экспонентой (у донора
    # роль интерполятора играет ровно та же `smooth_track`).
    #
    # Тумблер KARUSEL_REFRAME_TRACK=0 → детекция снова покадровая и полноразмерная
    # (поведение W1 байт в байт). KARUSEL_REFRAME_SMOOTH=0 гасит и трек тоже:
    # разреженные сэмплы БЕЗ сглаживания давали бы ступеньки раз в 6 кадров, так
    # что разреженность без фильтра не включается принципиально.
    tracker = None
    if smooth is not None and os.environ.get("KARUSEL_REFRAME_TRACK", "1") != "0":
        tracker = talk_reframe.Tracker(stride=talk_reframe.det_stride(fps))

    # W2/F4: ключи, у которых на границе вставки МЕНЯЕТСЯ ИСТОЧНИК КАДРА, — их
    # сглаживание нельзя тянуть через монтажный стык. Границы вставок и есть
    # границы сцен плана (`timeline["plan"]["scenes"]`: b-roll/micro приезжают в
    # движок как inserts). Разбор по местам:
    #   split_top — спикер В ПОЛОВИННОМ окне, живёт только внутри вставки;
    #   split_bot — на КАЖДОЙ вставке новый клип b-roll (жёсткая склейка);
    #   speaker   — спикер на ВЕСЬ кадр, живёт только СНАРУЖИ вставки;
    #   spk_rgba  — в cutout силуэт кадрируется только внутри вставки (в
    #               text-behind он идёт сквозь границу, там не сбрасываем).
    # pip и cmp_top/cmp_bot проходят границу непрерывно — их не трогаем, иначе
    # сброс сам стал бы щелчком на ровном месте.
    cut_keys = ["split_top", "split_bot", "speaker"]
    if template == "cutout":
        cut_keys.append("spk_rgba")

    # A4: ПОЗИЦИЯ СУБТИТРА ↔ ЛИЦО. По умолчанию субтитр там же, где раньше
    # (sub_cy_default). Если рамка текста пересекает лицо спикера в готовом кадре —
    # блок один раз на реплику поднимается вверх (решение фиксируется, покадрово не
    # прыгает). Детектор лица по КОМПОЗИТУ (там, где спикер реально стоит после
    # кадрирования); для cutout/text-behind без него — fallback по альфе силуэта.
    # Тумблер KARUSEL_SUB_AVOID=0 → прежнее поведение байт в байт (само-baseline).
    sub_avoid = os.environ.get("KARUSEL_SUB_AVOID", "1") != "0"
    sub_cy_default = sub_cy
    sub_det = det
    if (sub_avoid and sub_det is None and cues
            and template in ("cutout", "text-behind", "lower-third")):
        import mediapipe as mp
        sub_det = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5)
    # само-baseline A4 (A7 недоступен): считаем на рендере обе метрики сразу —
    # «до» (дефолтная рамка ↔ лицо) и «после» (фактическая поднятая рамка ↔ лицо).
    sub_face_frames = 0        # кадры, где активна реплика и найдено лицо
    sub_hit_default = 0        # из них: дефолтная позиция пересекает лицо
    sub_hit_actual = 0         # из них: фактическая позиция пересекает лицо
    sub_lifts = 0              # реплик поднято вверх
    sub_reduce = 0             # реплик не влезло над лицом (нужно короче)
    # R44 + E4-46: минимальный WCAG-контраст субтитра по кадрам реплик, шкала
    # ГЕЙТА (регион готового кадра со спрайтом, см. _sub_contrast_region).
    # None — реплик не было. Гейт R22 читает значение из meta, но только при
    # маркере sub_contrast_scale="composite" (старое meta мерит сам).
    sub_contrast_min = None
    # E4-57/U9: авто-акцент CTA-плашки — аккумулятор pick_accent по кадрам
    # (min контраста accent↔фон ДО подбора, сколько кадров реально подменили
    # цвет). accent_family пустая (дефолт стиля) → swaps всегда 0, min либо
    # None (оверлея не было), либо контраст исходного accent — поведение и
    # meta не отличаются от того, что было ДО этой задачи.
    accent_stats = {"min": None, "swaps": 0}
    accent_family = list(st.accent_family_rgb)

    # матировщик спикера: cutout (вырез на b-roll) и text-behind (вырез поверх
    # заголовка) — одна и та же альфа по кадру.
    matter = None
    spk_pin = None
    # E4-22 (P1-1): агрегат стабильности силуэта — считаем альфу спикера В ТОЧКЕ
    # ВСТАВКИ (не композит) и отдаём в meta для checks.verify_talk. Инициализируем
    # безусловно: для не-cutout шаблонов останутся нулями (метрики не считаются).
    spk_border_frames = spk_border_touch = spk_border_drop = 0
    if template in ("cutout", "text-behind"):
        import talk_matte
        matter = talk_matte.Matter(backend=matte_backend)
        spk_pin = {}          # состояние пиннинга силуэта (единый якорь на ролик)

    # E4-25: агрегат «голова в кадре» для compare (оба окна). Считаем на кадрах,
    # где детектор нашёл лицо: доля кадров с полностью видимой головой и
    # минимальный запас над макушкой. Для не-compare остаётся None (не считается).
    cmp_head_frames = cmp_head_ok = 0
    cmp_head_min_margin = None

    # R3: тот же агрегат для НИЖНЕЙ ПАНЕЛИ split (b-roll с людьми). Панель
    # кадрируется _cover_keep_head — макушка не режется; метрика подтверждает
    # числом (доля кадров с целой головой + мин. запас). B-roll без лица идёт
    # прежним центр-кропом (info=None) — в метрику не попадает.
    split_head_frames = split_head_ok = 0
    split_head_min_margin = None

    with tempfile.TemporaryDirectory() as td:
        # предрендер вставок: b-roll нормализуем в нужный размер, текст — в спрайт
        ins = []
        for it in (inserts or []):
            dur = it["t1"] - it["t0"]
            entry = dict(t0=it["t0"], t1=it["t1"])
            if it.get("src"):
                # b-roll готовим во весь портретный кадр; для split нижняя
                # половина кадрируется по лицу в цикле (голова не срезается).
                entry["clip"] = prepare_broll(it["src"], it.get("seg", 0.0),
                                              dur, VW, VH, td, int(fps))
            elif template == "lower-third":
                # плашка-лента поверх живого спикера (не полноэкранная плашка)
                entry["lt"] = _lower_third(it["text"], acc)
            else:
                spr, pos = _insert_card(it["text"], acc, fmt)
                entry["spr"], entry["pos"] = spr, pos
            ins.append(entry)

        # предрендер заголовков text-behind (формат 4): один RGBA-слой на окно,
        # рисуется под силуэт спикера в цикле. Пусто/не тот шаблон — список пуст.
        tits = []
        if template == "text-behind":
            for it in (titles or []):
                txt = (it.get("text") or "").strip()
                if not txt:
                    continue
                tits.append(dict(t0=it["t0"], t1=it["t1"],
                                 layer=_title_behind(txt, acc)))

        if enc == "pipe":
            # R11: один энкод — кадры BGR прямо в stdin ffmpeg, звук вторым
            # входом, те же финальные параметры, что у legacy-мукса ниже.
            # grade ложится тем же -vf на этот же единственный энкод.
            pipe_cmd = [FF, "-y", "-loglevel", "error",
                        "-f", "rawvideo", "-pix_fmt", "bgr24",
                        "-s", "%dx%d" % (VW, VH), "-r", "%.6f" % fps,
                        "-i", "-", "-i", video_path,
                        "-map", "0:v", "-map", "1:a:0?"]
            if grade is not None:
                pipe_cmd += ["-vf", GRADES[grade]]
            pipe_cmd += ["-c:v", "libx264", "-profile:v", "high",
                         "-pix_fmt", "yuv420p", "-crf", ENC_CRF,
                         "-preset", ENC_PRESET, "-c:a", "aac", "-b:a", "160k",
                         "-movflags", "+faststart", "-shortest", out]
            vw = _PipeWriter(pipe_cmd, os.path.join(td, "pipe_err.log"))
        else:
            silent = os.path.join(td, "silent.mp4")
            vw = cv2.VideoWriter(silent, cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps, (VW, VH))
        fi = 0
        cur = None          # активная вставка
        clip = None         # открытый _Clip для активного b-roll
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if src_rot:                      # применяем флаг поворота исходника
                frame = _apply_rotation(frame, src_rot)
            t = fi / fps
            # ДЖАМП-КАТ (28.08.2026). Референс владельца режет и спикера:
            # 20.1 склейки/мин против наших 17.4. Материал НЕ режем —
            # меняем крупность на границе сцены планировщика, и стык
            # читается как рез. speaker_cuts=None → прежний путь.
            _jump = 1.0
            if speaker_cuts:
                _n = 0
                for _c in speaker_cuts:
                    if t >= _c:
                        _n += 1
                    else:
                        break
                _jump = 1.0 if _n % 2 == 0 else JUMP_ZOOM
            if tracker is not None:
                tracker.tick(fi)         # W2: номер кадра — сетка сэмплов трека
            ps = _punch_scale(t, punch, punch_scale, punch_ramp, fps,
                              punch_style)                             # зум-удар
            spk_alpha_final = None    # A4: альфа силуэта на холсте (fallback лица)

            act = next((x for x in ins if x["t0"] <= t < x["t1"]), None)
            if act is not cur:                       # сменилась вставка
                if clip is not None:
                    clip.close(); clip = None
                if act is not None and "clip" in act:
                    clip = _Clip(act["clip"])
                cur = act
                # W2/F4: МОНТАЖНЫЙ СТЫК. Границы вставок = границы сцен плана;
                # здесь спикер и перебивка меняются местами, а b-roll — вообще
                # новый клип. Трек лица из прошлой сцены обнуляется, чтобы кадр
                # не «переезжал» через склейку, и первый кадр новой сцены встаёт
                # по свежей детекции. Донор идеи — pyautoflip (MIT): там путь
                # камеры считается посценно, состояние между сценами не живёт.
                if tracker is not None:
                    tracker.cut()
                if smooth is not None:
                    for _k in cut_keys:
                        smooth.reset(_k)

            typo = act is not None and "spr" in act    # активна плашка-текст
            if template in ("demo", "music"):
                # DEMO (E4-24, NAVIDUU Ф5) и MUSIC (P4): клип на весь портретный
                # кадр, без лица/матирования — просто cover. Подписи и музыка —
                # общими путями ниже/снаружи (demo: --text по кадру; music:
                # статичный music_layer, вклеен ниже, минуя цикл cues).
                base = Image.fromarray(cv2.cvtColor(_cover(frame, VW, VH),
                                                    cv2.COLOR_BGR2RGB))
            elif template == "compare":
                # COMPARE (E4-23/E4-25, NAVIDUU Ф3): стопка «плохо/хорошо». Оба
                # окна кадрируются ЦЕНТРОМ по горизонтали, но с ГАРАНТИЕЙ головы:
                # если детектор нашёл лицо — макушка/подбородок целиком в окне
                # (при нехватке cover-масштаба — зум-аут + поля, голову не режем).
                # Нет лица (b-roll) — обычный центр-кроп.
                top, _hi_t = _cover_keep_head(
                    frame, VW, VH // 2, det,
                    sm=smooth, key="cmp_top", tr=tracker)
                if cap2 is not None:
                    ok2, fr2 = cap2.read()
                    if ok2:
                        _last2 = _apply_rotation(fr2, src_rot2) if src_rot2 else fr2
                if _last2 is not None:
                    bot, _hi_b = _cover_keep_head(
                        _last2, VW, VH // 2, det,
                        sm=smooth, key="cmp_bot", tr=tracker)
                else:
                    bot, _hi_b = np.zeros((VH // 2, VW, 3), np.uint8), None
                for _hi in (_hi_t, _hi_b):
                    if _hi is not None:
                        cmp_head_frames += 1
                        if _hi["top_inside"]:
                            cmp_head_ok += 1
                        cmp_head_min_margin = (_hi["margin_px"]
                                               if cmp_head_min_margin is None
                                               else min(cmp_head_min_margin,
                                                        _hi["margin_px"]))
                canvas = np.vstack([top, bot])
                base = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
                base.paste(cmp_top, ((VW - cmp_top.width) // 2, 40), cmp_top)
                base.paste(cmp_bot, ((VW - cmp_bot.width) // 2, VH // 2 + 40), cmp_bot)
            elif template == "split":
                # экран пополам: спикер сверху, b-roll снизу. Нет вставки —
                # спикер на весь кадр (переключение как в реф. 2).
                if act is not None and clip is not None:
                    # спикер сверху — жёстко по лицу (голова в кадре); b-roll
                    # снизу — с ГАРАНТИЕЙ головы (R3): в окне половинной высоты
                    # прежний _cover_face(bias=0.4, ytarget=0.44) уводил макушку
                    # человека среднего плана за верхний срез. _cover_keep_head
                    # держит макушку и подбородок целиком (при нехватке масштаба —
                    # зум-аут с полями); без лица — прежний центр-кроп.
                    top = _cover_face(frame, VW, VH // 2, det, bias=1.0, scale=ps,
                                      sm=smooth, key="split_top", tr=tracker)
                    bfr = clip.next()
                    if bfr is not None:
                        bot, _hi_s = _cover_keep_head(
                            bfr, VW, VH // 2, det,
                            sm=smooth, key="split_bot", tr=tracker)
                        if broll_motion:      # R14: кен-бёрнс нижней панели
                            bot = _kb_zoom(bot, _kb_z(t, act, broll_motion))
                    else:
                        bot, _hi_s = (np.zeros((VH // 2, VW, 3), np.uint8), None)
                    if _hi_s is not None:      # R3: метрика «голова в панели»
                        split_head_frames += 1
                        if _hi_s["top_inside"]:
                            split_head_ok += 1
                        split_head_min_margin = (_hi_s["margin_px"]
                                                 if split_head_min_margin is None
                                                 else min(split_head_min_margin,
                                                          _hi_s["margin_px"]))
                    canvas = np.vstack([top, bot])
                    base = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
                    # шва нет: два кадра просто соседствуют (реф. Пример2)
                elif typo:
                    base = Image.new("RGB", (VW, VH), (14, 12, 11))
                    base.paste(act["spr"], act["pos"], act["spr"])
                else:
                    base = Image.fromarray(cv2.cvtColor(
                        _speaker_crop(frame, det, ps, sm=smooth, tr=tracker,
                                      face_y=st.speaker_face_y, jump=_jump,
                                      wide=st.speaker_wide),
                        cv2.COLOR_BGR2RGB))
            elif template == "cutout":
                # спикер вырезан по силуэту и лежит ПОВЕРХ b-roll (реф. Пример1).
                if act is not None and clip is not None:
                    bfr = clip.next()
                    bg = (_cover(bfr, VW, VH) if bfr is not None
                          else np.zeros((VH, VW, 3), np.uint8))
                    if broll_motion and bfr is not None:   # R14: кен-бёрнс фона
                        bg = _kb_zoom(bg, _kb_z(t, act, broll_motion))
                    base = Image.fromarray(cv2.cvtColor(bg, cv2.COLOR_BGR2RGB))
                    rgba = talk_matte.cutout_rgba(frame, matter)   # спикер + альфа
                    spk = Image.fromarray(_cover_speaker_rgba(rgba, frame, det, ps, spk_pin,
                                                             sm=smooth, tr=tracker),
                                          "RGBA")
                    _tch, _drp = _speaker_alpha_border(spk)   # E4-22: замер по альфе спикера
                    spk_border_frames += 1
                    spk_border_touch += 1 if _tch else 0
                    spk_border_drop += 1 if _drp else 0
                    base.paste(spk, (0, 0), spk)          # спикер поверх фона
                    spk_alpha_final = np.asarray(spk)[:, :, 3]   # A4: fallback лица
                elif typo:
                    base = Image.new("RGB", (VW, VH), (14, 12, 11))
                    base.paste(act["spr"], act["pos"], act["spr"])
                else:
                    # нет b-roll — спикер ЖИВЫМ кадром на весь экран (реф. 2),
                    # как в исходном cutout; punch-in зумит его по лицу.
                    base = Image.fromarray(cv2.cvtColor(
                        _speaker_crop(frame, det, ps, sm=smooth, tr=tracker,
                                      face_y=st.speaker_face_y, jump=_jump,
                                      wide=st.speaker_wide),
                        cv2.COLOR_BGR2RGB))
            elif template == "text-behind":
                # ФОРМАТ 4: фон → КРУПНЫЙ заголовок → силуэт спикера поверх.
                # Буквы уходят за плечи/голову; фон — b-roll (если попал) или
                # тёмный. Спикер матируется тем же talk_matte, что и cutout.
                if typo:
                    base = Image.new("RGB", (VW, VH), (14, 12, 11))
                    base.paste(act["spr"], act["pos"], act["spr"])
                else:
                    if act is not None and clip is not None:
                        bfr = clip.next()
                        bg = (_cover(bfr, VW, VH) if bfr is not None
                              else np.zeros((VH, VW, 3), np.uint8))
                        if broll_motion and bfr is not None:  # R14: кен-бёрнс фона
                            bg = _kb_zoom(bg, _kb_z(t, act, broll_motion))
                        base = Image.fromarray(cv2.cvtColor(bg, cv2.COLOR_BGR2RGB))
                    else:
                        base = Image.new("RGB", (VW, VH), (14, 12, 11))
                    tit = next((x for x in tits if x["t0"] <= t < x["t1"]), None)
                    if tit is not None:                   # заголовок ПОД спикера
                        base.paste(tit["layer"], (0, 0), tit["layer"])
                    rgba = talk_matte.cutout_rgba(frame, matter)
                    spk = Image.fromarray(_cover_speaker_rgba(rgba, frame, det, ps, spk_pin,
                                                             sm=smooth, tr=tracker),
                                          "RGBA")
                    _tch, _drp = _speaker_alpha_border(spk)   # E4-22: замер по альфе спикера
                    spk_border_frames += 1
                    spk_border_touch += 1 if _tch else 0
                    spk_border_drop += 1 if _drp else 0
                    base.paste(spk, (0, 0), spk)          # силуэт поверх текста
                    spk_alpha_final = np.asarray(spk)[:, :, 3]   # A4: fallback лица
            elif template == "lower-third":
                # спикер ЖИВОЙ на весь кадр; b-roll (если попал) — перебивкой,
                # чтобы не терять сток. Плашка-лента с тезисом/числом ложится
                # снизу поверх кадра (субтитр рисуется отдельно, выше плашки).
                if act is not None and clip is not None:
                    base = Image.fromarray(cv2.cvtColor(clip.next(),
                                                        cv2.COLOR_BGR2RGB))
                else:
                    base = Image.fromarray(cv2.cvtColor(_cover(frame, VW, VH),
                                                        cv2.COLOR_BGR2RGB))
                if act is not None and "lt" in act:
                    base.paste(act["lt"], (0, 0), act["lt"])
            elif fmt == "A":
                # фон = b-roll / плашка / тёмный; сверху всегда голова в углу
                if act is not None and clip is not None:
                    base = Image.fromarray(cv2.cvtColor(clip.next(),
                                                        cv2.COLOR_BGR2RGB))
                elif typo:
                    base = Image.new("RGB", (VW, VH), (14, 12, 11))
                    base.paste(act["spr"], act["pos"], act["spr"])
                else:
                    base = Image.new("RGB", (VW, VH), (14, 12, 11))
                # PIP-голова — тоже по лицу с зумом punch-in (голова в углу «бьёт»)
                pip = Image.fromarray(cv2.cvtColor(
                    (_cover_face(frame, pip_w, pip_h, det, scale=ps,
                                 sm=smooth, key="pip", tr=tracker)
                     if ps > 1.0 and det is not None
                     else _cover(frame, pip_w, pip_h)), cv2.COLOR_BGR2RGB))
                base.paste(pip, (pip_x, pip_y), pip_mask)
            else:
                # голова крупно; во время вставки — полноэкранная перебивка
                if act is not None and clip is not None:
                    base = Image.fromarray(cv2.cvtColor(clip.next(),
                                                        cv2.COLOR_BGR2RGB))
                elif typo:
                    base = Image.new("RGB", (VW, VH), (14, 12, 11))
                    base.paste(act["spr"], act["pos"], act["spr"])
                else:
                    base = Image.fromarray(cv2.cvtColor(
                        _speaker_crop(frame, det, ps, sm=smooth, tr=tracker,
                                      face_y=st.speaker_face_y, jump=_jump,
                                      wide=st.speaker_wide),
                        cv2.COLOR_BGR2RGB))

            # MUSIC (P4): подпись статична — один слой, наклеенный на КАЖДЫЙ
            # кадр, без обращения к cues (для этого шаблона cues пуст: см.
            # build_textmode). Остальные шаблоны music_layer не строят
            # (None) — их ветка ниже не трогается.
            if music_layer is not None:
                base.paste(music_layer, (0, 0), music_layer)

            # субтитр поверх всего, КРОМЕ кадров с плашкой (там текст сам message)
            cue = next((c for c in cues if c["t0"] <= t < c["t1"]), None)
            if cue is not None and not typo:
                if karaoke and cue.get("kspr"):
                    # активное слово = последнее, чей старт уже прошёл (в паузах
                    # держим подсветку на нём, пока не начнётся следующее).
                    idx = 0
                    for i, wd in enumerate(cue["words"]):
                        if wd["s"] <= t:
                            idx = i
                    spr = cue["kspr"][idx]
                else:
                    spr = cue["spr"]
                # x: align центр — центр кадра/контентной зоны (формула байт в
                # байт, см. _sub_left_x); left/right — прижим к краю безопасной
                # зоны. Одна функция с sub_box выше: рамка meta = нарисованному.
                # ПОЗИЦИЯ БЛОКА — ПО БАЗОВОМУ СПРАЙТУ РЕПЛИКИ, а не по активному.
                # Владелец 02.09.2026: «устранить дрожание». Замер показал, что
                # КАДР не дрожит (медиана сдвига 0.2-0.5 px, меньше исходника) —
                # дрожал ТЕКСТ: при караоке спрайт меняется на каждом слове,
                # ширина скачет, и центрованный блок пересчитывался каждый кадр.
                # Базовый спрайт (вся реплика) один на реплику, поэтому x стоит.
                sub_x = _sub_left_x((cue.get("spr") or spr).width)
                # A4: позиция по вертикали. Тумблер off → прежний центр байт в байт.
                cy = sub_cy_default
                # ЗАМОК: подпись стоит намертво (владелец 28.08.2026).
                # Уклонение от лица выключено сознательно — неподвижность
                # важнее; от лиц защищает отбор врезок в talk_broll.
                if sub_avoid and not st.sub_cy_lock:
                    sw2, sh2 = spr.width, spr.height
                    if "_box" not in cue:     # рамка реплики для meta (дефолт, пока)
                        cue["_box"] = (sub_x, sub_cy_default - sh2 // 2,
                                       sub_x + sw2, sub_cy_default + sh2 // 2)
                        cue["_t0"], cue["_t1"] = cue["t0"], cue["t1"]
                    fbox = _sub_face_box(
                        cv2.cvtColor(np.asarray(base), cv2.COLOR_RGB2BGR),
                        sub_det, spk_alpha_final)
                    # подъём — ОДНОКРАТНЫЙ переход на реплику: держим дефолт, пока
                    # он реально не наедет на лицо; тогда уводим блок в чистую полосу
                    # и ФИКСИРУЕМ (назад не возвращаем, покадрово не пересчитываем —
                    # решение на реплику берёт худший из встреченных кадров).
                    if not cue.get("_lift"):
                        cy2, lifted, reduced = _place_sub_cy(
                            sub_cy_default, sw2, sh2, sub_x, fbox,
                            force=st.sub_cy_force)
                        if lifted:
                            cue["_cy"], cue["_lift"] = cy2, True
                            cue["_box"] = (sub_x, cy2 - sh2 // 2,
                                           sub_x + sw2, cy2 + sh2 // 2)
                            sub_lifts += 1
                            if reduced:
                                sub_reduce += 1
                    cy = cue.get("_cy", sub_cy_default)
                    if fbox is not None:      # само-baseline: «до» и «после» разом
                        sub_face_frames += 1
                        dbox = (sub_x, sub_cy_default - sh2 // 2,
                                sub_x + sw2, sub_cy_default + sh2 // 2)
                        if _boxes_hit(dbox, fbox):
                            sub_hit_default += 1
                        if _boxes_hit((sub_x, cy - sh2 // 2,
                                       sub_x + sw2, cy + sh2 // 2), fbox):
                            sub_hit_actual += 1
                base.paste(spr, (sub_x, cy - spr.height // 2), spr)
                # R44 + E4-46: контраст субтитра — ПОСЛЕ вклейки спрайта, тем же
                # оценщиком, что и гейт (`checks.sub_contrast`). Мерили до
                # наложения — метрика не видела собственную обводку и тень и
                # расходилась с гейтом до 5×; теперь шкала одна, пороги R22
                # валидны. Даунсэмпл ::2 (не ::4): обводка тонкая, на ::4
                # верхний дециль промахивается мимо текста. Минимум за ролик по
                # ВСЕМ кадрам реплик — это и есть преимущество движка над гейтом.
                _sy0 = cy - spr.height // 2
                _reg = np.asarray(base.crop((max(0, sub_x), max(0, _sy0),
                                             min(VW, sub_x + spr.width),
                                             min(VH, _sy0 + spr.height))))
                if _reg.size:
                    _c = _sub_contrast_region(_reg[::2, ::2, :3])
                    if _c is not None and (sub_contrast_min is None
                                           or _c < sub_contrast_min):
                        sub_contrast_min = _c

            # оверлеи (кодовое слово / подсказка) — поверх всего, опционально
            if overlays:
                _apply_overlays(base, t, overlays, acc, accent_family, accent_stats)

            # R51: CTA-слово card — встроенное поведение раскладки (без флага),
            # строго ПОСЛЕ конца реплик (субтитра уже нет, место освободилось —
            # тот же sub_cy_default, где рисовался субтитр). До конца ролика.
            if is_card and cues and t >= cues[-1]["t1"]:
                _draw_card_cta(base, card_cta_word, card_cta_font, card_cta_bbox,
                               sub_cy_default, acc, accent_family, accent_stats)

            # накладные форматы (поверх всего, опциональны, дефолт — выключены)
            if progress_bar:
                frac = (t / dur_total) if dur_total else (fi / max(1, nframes))
                _progress_bar(base, frac, acc)
            if items:
                it = next((x for x in items if x["t0"] <= t < x["t1"]), None)
                if it is not None:
                    _list_counter(base, it["no"], it["total"],
                                  it.get("title", ""), acc)
            if flash_list and not typo:      # вспышка — на спикере, не на плашке
                fa = next((x for x in flash_list if x["t0"] <= t < x["t1"]), None)
                if fa is not None:
                    span = max(1e-3, fa["t1"] - fa["t0"])
                    _draw_flash(base, fa["spr"], (t - fa["t0"]) / span)

            vw.write(cv2.cvtColor(np.asarray(base), cv2.COLOR_RGB2BGR))
            fi += 1
        if clip is not None:
            clip.close()
        cap.release()
        if cap2 is not None:
            cap2.release()
        vw.release()

        # звук исходника + h264/yuv420p под Instagram. Формат 9 — единый грейд:
        # ОДИН -vf фильтр (eq/curves) на этом же финальном энкоде, лишнего
        # прохода нет. Без grade фильтр не добавляется (кадр не трогаем).
        # R1: `-map 1:a:0?` — только ПЕРВАЯ аудиодорожка исходника. iPhone пишет
        # вторую spatial-дорожку (apac, «Audio: none») без декодера — `-map 1:a?`
        # брал ВСЕ и mux падал «no decoder found for: none» (IMG_3829/IMG_3046).
        # R11: в pipe-режиме out уже закодирован единственным энкодом при
        # vw.release() — второй проход не нужен.
        if enc == "legacy":
            mux = [FF, "-y", "-loglevel", "error",
                   "-i", silent, "-i", video_path,
                   "-map", "0:v", "-map", "1:a:0?"]
            if grade is not None:
                mux += ["-vf", GRADES[grade]]
            mux += ["-c:v", "libx264", "-profile:v", "high",
                    "-pix_fmt", "yuv420p", "-crf", ENC_CRF, "-preset", ENC_PRESET,
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                    "-shortest", out]
            _run(mux)

        # T-loud/1: ГРОМКОСТЬ ГОТОВОГО РОЛИКА → ОБЩАЯ (EBU R128, два прохода,
        # music.normalize_video). Видео не пересжимается (-c:v copy), кадр
        # байт-в-байт; трогается только аудиодорожка.
        #
        # ЗАЧЕМ. Выравнивание громкости жило ТОЛЬКО в engine_video (там оно
        # позвано на пути «без музыки»), а engine_talk не звал его вовсе — то
        # есть на разговорные ролики, ОСНОВНОЙ формат завода, общий фикс
        # разброса не распространялся: громкость готового ролика равнялась
        # громкости того, как человек говорил в телефон в этот день. Зритель
        # листает ленту с одной громкостью телефона, и половина ленты для него
        # либо орёт, либо не слышна.
        #
        # ПОЧЕМУ ЗДЕСЬ, А НЕ ВНУТРИ ЭНКОДА. Двухпроходный loudnorm обязан СНАЧАЛА
        # замерить готовую дорожку и лишь потом применить ОДНУ константу
        # (linear=true) — до записи файла мерить нечего. Однопроходный вариант
        # тянул бы уровень по ходу ролика и «дышал» бы на паузах речи.
        #
        # ЕСЛИ НЕ ВЫШЛО — ОТДАЁМ КАК ЕСТЬ. Ролик без выровненной громкости лучше,
        # чем не собравшийся ролик; дорожки может не быть вовсе (немой исходник),
        # тогда normalize_video честно возвращает False. Тот же приём и тот же
        # порядок действий, что у engine_video (см. там же).
        #
        # Тумблер KARUSEL_TALK_LOUDNORM=0 → шаг выключен, файл байт-в-байт как
        # до правки (нужен для само-baseline: замер «до» и «после» одним кодом).
        if os.environ.get("KARUSEL_TALK_LOUDNORM", "1") != "0":
            import music as mu
            # Времянка кладётся РЯДОМ с out, а не в td: td живёт в системном
            # TEMP, который на Windows запросто на другом томе, и os.replace
            # через том падает. Рядом — переименование атомарно.
            _loud_tmp = out + ".loudnorm.mp4"
            try:
                if mu.normalize_video(out, _loud_tmp):
                    os.replace(_loud_tmp, out)
            except Exception as _e:                          # noqa: BLE001
                print("  громкость не выровнялась (%s) — беру как есть" % _e)
            finally:
                if os.path.exists(_loud_tmp):
                    os.remove(_loud_tmp)

    # A4: субтитр теперь стоит ПОРЕПЛИЧНО (часть реплик поднята над лицом), поэтому
    # sub_box (единая рамка контракта) считается как ОБЪЕДИНЕНИЕ фактических рамок
    # реплик — так безопасная зона остаётся валидной. Точная поблочная проверка
    # «субтитр↔лицо» — в sub_boxes (порепличный контракт для checks, см. отчёт).
    sub_boxes = [dict(t0=c["_t0"], t1=c["_t1"], box=list(c["_box"]))
                 for c in (cues or []) if "_box" in c]
    if sub_boxes:
        xs0 = min(b["box"][0] for b in sub_boxes)
        ys0 = min(b["box"][1] for b in sub_boxes)
        xs1 = max(b["box"][2] for b in sub_boxes)
        ys1 = max(b["box"][3] for b in sub_boxes)
        sub_box = (xs0, ys0, xs1, ys1)
    elif music_box is not None:
        # MUSIC: рамка одна на весь ролик (не по-репличная) — тот же контракт
        # для checks.verify_talk, просто один интервал [0, dur] вместо cues.
        _music_dur = fi / fps if fps else 0.0
        sub_box = music_box
        sub_boxes = [dict(t0=0.0, t1=_music_dur, box=list(music_box))]

    # R45: фактор времени рендера — сколько секунд машины на секунду ролика
    # (цель <6× на 1080p). Только добавление ключей в meta, поведение не меняем.
    _render_secs = time.time() - _t_render0
    _dur = fi / fps if fps else 0.0

    # template/dur/sub_box/seam_y — то, что ждёт checks.verify_talk(). Без них
    # проверка субтитра и шва молча пропускалась предупреждением.
    return dict(out=out, fmt=fmt, cues=len(cues), inserts=len(ins),
                size_bytes=os.path.getsize(out),
                template=template, dur=fi / fps if fps else 0.0,
                sub_box=sub_box, sub_boxes=sub_boxes,
                # R30: чем рисовали — стиль, гарнитура, профиль безопасной зоны.
                # Замер «стиль реально доехал до кадра» делается по этим полям
                # вместе с sub_box/sub_px, а не по рапорту «применено».
                style_name=st.name, style_font=st.font_name,
                style_sub_kind=st.sub_kind, safe_zone_profile=st.safe_profile,
                safe_zone=[SAFE_TOP, SAFE_BOTTOM, SAFE_RIGHT, M],
                # A4 (позиция субтитра ↔ лицо): само-baseline на рендере.
                sub_face_frames=sub_face_frames,
                sub_hit_default=sub_hit_default,
                sub_hit_actual=sub_hit_actual,
                sub_lifts=sub_lifts, sub_reduce=sub_reduce,
                # ЛОКАЛЬНАЯ ПРАВКА (дефект 17, 09.09.2026): шов существует только
                # тогда, когда в ролике есть хоть одна вставка. При стиле с
                # pace.max_broll=0 (формат СУБТИТРЫ) сплит-сцен нет ни одной,
                # кадр полнокадровый — шва в видео физически нет, а
                # checks.verify_talk по непустому seam_y блокировал выпуск
                # претензией «граница режет лицо». Условие `and ins` это снимает.
                seam_y=(VH // 2 if (split and ins) else None),
                # E4-25: фактический кегль субтитра (для замера ×3: 74→222) и
                # метрика «голова в кадре» для compare (доля кадров + мин. запас).
                sub_px=size if cues else None,
                # R44 + E4-46: мин. WCAG-контраст субтитра по кадрам реплик.
                # Шкала — ГЕЙТА (регион готового кадра, см. _sub_contrast_region);
                # маркер sub_contrast_scale говорит гейту, что значению можно
                # верить как своему. None — реплик нет.
                sub_contrast_min=(round(sub_contrast_min, 2)
                                  if sub_contrast_min is not None else None),
                sub_contrast_scale="composite",
                # E4-51: чем рисовалось караоке — гейт по этим полям ловит
                # заведомо нечитаемые связки стиля (обводка на плашке, тёмный
                # акцент под тёмным словом).
                karaoke=bool(karaoke), karaoke_style=(karaoke_style if karaoke
                                                      else None),
                sub_stroke_w=sub_sw,
                karaoke_plate_contrast=(round(_plate_contrast(acc,
                                                              st.karaoke_fill), 2)
                                        if karaoke and karaoke_style == "plate"
                                        else None),
                karaoke_plate_stroke_off=bool(
                    karaoke and karaoke_style == "plate"
                    and KAR_KW["stroke_w"] > 0),
                # E4-57/U9 (+R51): авто-акцент CTA-плашки (pick_accent) — мин.
                # WCAG-контраст ИСХОДНОГО accent↔фон по кадрам, где рисовалась
                # плашка кодового слова (общий --cta ИЛИ встроенное CTA-слово
                # card), и сколько раз реально подменили цвет на кандидата из
                # accent_family. None — ни один из двух оверлеев не рисовался
                # (либо зона вырождена на всех кадрах); 0 подмен — либо семьи
                # нет, либо контраст и так был в норме на каждом кадре.
                accent_contrast_min=(round(accent_stats["min"], 2)
                                     if accent_stats["min"] is not None else None),
                accent_swaps=accent_stats["swaps"],
                # R45: время рендера и фактор к длительности ролика.
                render_secs=round(_render_secs, 2),
                render_ratio=(round(_render_secs / _dur, 2) if _dur else None),
                compare_head_ok=(cmp_head_ok / cmp_head_frames
                                 if cmp_head_frames else None),
                compare_head_frames=cmp_head_frames,
                compare_head_min_margin_px=cmp_head_min_margin,
                # R3: «голова в кадре» для нижней панели split (b-roll с людьми).
                split_head_ok=(split_head_ok / split_head_frames
                               if split_head_frames else None),
                split_head_frames=split_head_frames,
                split_head_min_margin_px=split_head_min_margin,
                # E4-22 (P1-1): метрики силуэта для checks.verify_talk (только
                # cutout/text-behind; иначе frames=0 → touch=None, проверка молчит).
                speaker_border_touch=(spk_border_touch / spk_border_frames
                                      if spk_border_frames else None),
                speaker_dropout=spk_border_drop,
                speaker_frames=spk_border_frames,
                # W1: дрожание кадра, само-baseline. По каждому месту кадрирования
                # {"before": ..., "after": ...}: позиция окна, посчитанная в ЭТОМ
                # ЖЕ рендере по сырой детекции и по сглаженной. d_mean_px —
                # средний межкадровый сдвиг (это и есть дрожание), d_var_px2 —
                # его дисперсия, d_max_px — максимальный скачок, clicks — число
                # скачков выше 8 px (щелчки). None — кадрирование по лицу не
                # работало (нет детектора / раскладка без лица).
                reframe_jitter=(smooth.report() if smooth is not None else None),
                # W2: разреженная детекция. det_calls — реальных вызовов
                # mediapipe, det_asks — сколько их было БЫ при покадровой
                # детекции, det_saved — доля сэкономленных; cuts — сколько раз
                # трек сбрасывался на монтажном стыке (F4). None — трек выключен
                # (нет детектора / KARUSEL_REFRAME_TRACK=0).
                reframe_track=(tracker.report() if tracker is not None else None))


class _PipeWriter:
    """R11: писатель кадров прямо в stdin ffmpeg (-f rawvideo bgr24) — интерфейс
    как у cv2.VideoWriter (write/release), чтобы цикл рендера не менялся.
    stderr ffmpeg уходит в файл (не в PIPE — иначе возможен дедлок буфера);
    при падении ffmpeg release() поднимает RuntimeError с хвостом лога."""

    def __init__(self, cmd, err_path):
        self._err_path = err_path
        self._err_f = open(err_path, "wb")
        self._released = False
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                      stdout=subprocess.DEVNULL,
                                      stderr=self._err_f)

    def write(self, frame_bgr):
        # ffmpeg мог закрыть stdin раньше нас и выйти штатно: с `-shortest` он
        # заканчивает по КОРОТКОМУ входу, а звук исходника бывает на доли секунды
        # короче видео. Тогда release() уже отработал (rc==0, ошибки нет), и
        # следующий write() в закрытый буфер ронял рендер ValueError'ом в самом
        # конце прогона (P1 критика, воспроизведено). После release() кадры
        # молча отбрасываем — ffmpeg их всё равно не примет.
        if self._released:
            return
        try:
            self._proc.stdin.write(np.ascontiguousarray(frame_bgr).tobytes())
        except (BrokenPipeError, ValueError, OSError):
            self.release()      # добраться до stderr и упасть с причиной

    def release(self):
        if self._released:
            return
        self._released = True
        if self._proc.stdin and not self._proc.stdin.closed:
            try:
                self._proc.stdin.close()
            except BrokenPipeError:
                pass
        rc = self._proc.wait()
        self._err_f.close()
        if rc != 0:
            try:
                with open(self._err_path, "rb") as f:
                    tail = f.read().decode("utf-8", "replace")[-2000:]
            except OSError:
                tail = ""
            raise RuntimeError("ffmpeg (pipe) упал:\n" + tail)


def _run(args):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg упал:\n" + (r.stderr or "")[-2000:])
    return r
