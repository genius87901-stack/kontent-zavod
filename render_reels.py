# -*- coding: utf-8 -*-
"""ОРКЕСТРАТОР движка №4: говорящее видео → готовый разговорный Reels (Split).

Единая точка входа завода. Собирает конвейер из готовых стадий:
    transcript.json  (talk_transcribe, стадия 2)
    blocks.json      (смысловые блоки + визуальный запрос — «Claude в цикле»)
        │
        ├─ talk_broll.broll_for_blocks() → inserts со стоков (Pixabay/Pexels)
        ├─ engine_talk.render(template="split") → mp4
        └─ контактный лист (замер, не вывод — как review_sheet у каруселей)

ПРЕДОХРАНИТЕЛЬ: ничего не публикует. Готовит только mp4 и лист; публикация —
отдельно через autopost.py по явному слову Валерии (как у каруселей и Reels).

blocks.json (формат стадии talk_segment, §6 REELS_TALK_ARCH):
    {"blocks":[{"t0":..,"t1":..,"visual_query":"business conference",...}, ...]}
Пока визуальные запросы пишет Claude при сборке (без API-ключа). После первых
оплат — стадия talk_segment зовёт модель сама (полный автомат).

ЗАПУСК:
    python render_reels.py --video talking/IMG_8138.MP4 \
        --transcript out_talk/IMG_8138_transcript.json \
        --blocks out_talk/IMG_8138_blocks.json \
        --out out_talk/reels_8138.mp4
"""
import argparse
import hashlib
import json
import os
import sys
import time

import cv2
from PIL import Image, ImageDraw

import checks
import engine4 as e
import engine_talk
import style as style_mod           # R30: json-пресет стиля клиента (--style)
import talk_broll
import talk_plan

HERE = os.path.dirname(os.path.abspath(__file__))


def _gate_enforced():
    """Клиентская ли это сборка (жёсткий режим активации).

    Дубль правила `activation_gate.enforced` НАРОЧНО: если гейта нет вовсе,
    спросить его о режиме не у кого, а решать что-то надо. Правило одно и то же:
    штамп config/.client_build (ставит установщик), config/activation.enforce
    или env KZ_ENFORCE_ACTIVATION=1.
    """
    if os.environ.get("KZ_ENFORCE_ACTIVATION", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return True
    return any(os.path.exists(os.path.join(HERE, "config", m))
               for m in (".client_build", "activation.enforce"))


# R9: караоке-субтитры — ДЕФОЛТ для говорящих раскладок (отраслевая практика:
# пословная подсветка держит взгляд; AUDIT фактор 6 «караоке не дефолт»).
# demo/compare сюда НЕ входят: их подписи из --text, поведение прежнее.
KARAOKE_DEFAULT_TEMPLATES = ("split", "cutout", "text-behind", "pip", "card",
                             "lower-third")


def karaoke_default(template):
    """Караоке по умолчанию? True для говорящих раскладок (R9)."""
    return template in KARAOKE_DEFAULT_TEMPLATES


def _draw_zones(im, meta=None):
    """R49: рамки безопасных зон Instagram (SAFE_* движка) и фактическая рамка
    субтитра (meta['sub_box']) НА кадре листа. Координаты зон заданы в холсте
    VW×VH engine_talk — масштабируем в размер миниатюры."""
    kx = im.width / float(engine_talk.VW)
    ky = im.height / float(engine_talk.VH)
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    # зоны UI Instagram (шапка / ник+подпись+кнопки / колонка действий) —
    # красноватая заливка: контент сюда попадать не должен
    zones = [(0, 0, engine_talk.VW, engine_talk.SAFE_TOP),
             (0, engine_talk.VH - engine_talk.SAFE_BOTTOM,
              engine_talk.VW, engine_talk.VH),
             (engine_talk.VW - engine_talk.SAFE_RIGHT, 0,
              engine_talk.VW, engine_talk.VH)]
    for x0, y0, x1, y1 in zones:
        d.rectangle((x0 * kx, y0 * ky, x1 * kx - 1, y1 * ky - 1),
                    fill=(226, 61, 61, 42), outline=(226, 61, 61, 150), width=1)
    # sub_box из meta (единая рамка субтитров контракта checks) — зелёная рамка:
    # видно, что субтитр сидит в контентной зоне
    sub = (meta or {}).get("sub_box")
    if sub:
        x0, y0, x1, y1 = sub
        d.rectangle((x0 * kx, y0 * ky, x1 * kx, y1 * ky),
                    outline=(70, 190, 110, 230), width=2)
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def contact_sheet(video, out_jpg, n=16, cols=4, overlay_zones=False, meta=None):
    """Кадры по всей длине готового ролика на одном листе — показываем Валерии
    картинкой, а не рапортом «успех» (правило завода «замер, не вывод»).

    R49: `overlay_zones=True` (НЕ дефолт) — на каждый кадр наносятся рамки
    безопасных зон Instagram (SAFE_TOP/SAFE_BOTTOM/SAFE_RIGHT из engine_talk)
    и рамка субтитра `meta['sub_box']`: на листе сразу видно, что текст и
    плашки не заезжают под UI. Без флага лист байт-в-байт прежний."""
    tw, th, pad, lab = 340, 604, 12, 34
    c = cv2.VideoCapture(video)
    fps = c.get(cv2.CAP_PROP_FPS) or 30
    dur = (c.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps
    cards = []
    for i in range(n):
        c.set(cv2.CAP_PROP_POS_MSEC, dur * (i + 0.5) / n * 1000.0)
        ok, fr = c.read()
        if not ok:
            continue
        img = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
        r = min(tw / img.width, th / img.height)
        im = img.resize((int(img.width * r), int(img.height * r)), Image.LANCZOS)
        if overlay_zones:
            im = _draw_zones(im, meta)
        cv = Image.new("RGB", (tw, th), (24, 24, 26))
        cv.paste(im, ((tw - im.width) // 2, (th - im.height) // 2))
        cards.append((dur * (i + 0.5) / n, cv))
    c.release()
    rows = (len(cards) + cols - 1) // cols
    W = cols * tw + (cols + 1) * pad
    H = rows * (th + lab) + (rows + 1) * pad
    sheet = Image.new("RGB", (W, H), (245, 244, 242))
    d = ImageDraw.Draw(sheet)
    f = e.font("SemiBold", 21)
    for k, (t, thumb) in enumerate(cards):
        x = pad + (k % cols) * (tw + pad)
        y = pad + (k // cols) * (th + lab + pad)
        sheet.paste(thumb, (x, y))
        d.text((x + 4, y + th + 6), "%.1f сек" % t, font=f, fill=e.INK)
    sheet.save(out_jpg, quality=90)
    return out_jpg


def choice_sheet(blocks, out_jpg, providers=("pixabay",), per=3, template=None):
    """Лист выбора: на каждый блок — до `per` кандидатов с номерами «блок.вариант».
    Превью — РОВНО тот вырез нижней половины, что попадёт в сплит (кадр из
    середины ролика, прогнанный через тот же b-roll-кроп): что видно на листе,
    то и в кадре. Показываем Валерии, она называет номера замен (как ALL_PHOTOS)."""
    from PIL import Image as I, ImageDraw as D
    import numpy as np
    import mediapipe as mp
    det = mp.solutions.face_detection.FaceDetection(
        model_selection=1, min_detection_confidence=0.5)

    groups = talk_broll.candidates_for_blocks(blocks, providers, per=per,
                                              template=template)
    # ячейка в пропорции нижней половины сплита (1080×960 = 1.125:1)
    tw, th, pad, hh, lab = 300, 267, 16, 40, 26
    W = per * tw + (per + 1) * pad
    H = pad + len(groups) * (hh + th + lab + pad)
    sheet = I.new("RGB", (W, H), (245, 244, 242))
    d = D.Draw(sheet)
    fh = e.font("Black", 24)
    fl = e.font("SemiBold", 20)
    y = pad
    for i, g in enumerate(groups):
        b = g["block"]
        d.text((pad, y + 6), "Блок %d  —  %s" % (i + 1, b.get("topic", "")),
               font=fh, fill=e.INK)
        y += hh
        for j in range(per):
            x = pad + j * (tw + pad)
            cell = I.new("RGB", (tw, th), (222, 220, 217))
            if j < len(g["cands"]):
                mp_path = talk_broll.clip_midframe(g["cands"][j])
                if mp_path:
                    bgr = cv2.imread(mp_path)
                    if bgr is not None:
                        # тот же вырез b-roll, что в движке (нижняя половина)
                        crop = engine_talk._cover_face(bgr, engine_talk.VW,
                                                       engine_talk.VH // 2, det,
                                                       bias=0.4)
                        im = I.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                        im = im.resize((tw, th), I.LANCZOS)
                        cell.paste(im, (0, 0))
                sheet.paste(cell, (x, y))
                d.rectangle([x, y, x + 58, y + 30], fill=e.ACCENTS["lime"])
                d.text((x + 8, y + 3), "%d.%d" % (i + 1, j + 1), font=fh, fill=e.INK)
            else:
                sheet.paste(cell, (x, y))
                d.text((x + 8, y + th // 2), "нет варианта", font=fl, fill=(150, 60, 40))
        y += th + lab
    sheet.save(out_jpg, quality=90)
    return out_jpg


def write_metrics(meta, t_start):
    """R43: <out>_metrics.json — ВСЕ счётчики meta движка (sub_*, speaker_*,
    compare_*, broll с w/h, dur, sub_px, размер...) + время сборки. Правило
    завода «замер, не вывод»: числа лежат файлом рядом с роликом, а не тонут в
    консоли. Чистая сериализация того, что рендер уже посчитал.

    Несериализуемые значения (пути PosixPath и пр.) уходят через default=str —
    метрики не должны уметь ронять сборку."""
    path = os.path.splitext(meta["out"])[0] + "_metrics.json"
    data = dict(meta)
    data["build_seconds"] = round(time.time() - t_start, 1)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    meta["metrics"] = path
    return path


def flashes_from_blocks(blocks):
    """Формат 3 (слово-вспышка): окна из блоков с полем `flash_word`. Блок без
    поля — не вспыхивает. Окно: `flash_at`:[t0,t1], если задан, иначе короткая
    вспышка от старта блока (≈1.4 с, но не длиннее самого блока)."""
    out = []
    for b in blocks:
        word = str(b.get("flash_word", "") or "").strip()
        if not word:
            continue
        at = b.get("flash_at")
        if isinstance(at, (list, tuple)) and len(at) == 2:
            t0, t1 = float(at[0]), float(at[1])
        else:
            t0 = float(b["t0"])
            t1 = min(float(b["t1"]), t0 + 1.4)
        if t1 > t0:
            out.append({"t0": t0, "t1": t1, "text": word})
    return out


def items_from_blocks(blocks):
    """Формат 8 (список-счётчик): окна из блоков с разметкой пунктов. Контракт
    блока: `item_no` (номер пункта) + опц. `item_title` (заголовок) + опц.
    `items_total` (всего; если не задан — берётся макс. item_no по всем блокам).
    Блок без `item_no` — не размечен, счётчик на нём не рисуется."""
    nums = [int(b["item_no"]) for b in blocks if b.get("item_no") is not None]
    if not nums:
        return []
    total_auto = max(nums)
    out = []
    for b in blocks:
        if b.get("item_no") is None:
            continue
        out.append({"t0": float(b["t0"]), "t1": float(b["t1"]),
                    "no": int(b["item_no"]),
                    "total": int(b.get("items_total", total_auto)),
                    "title": str(b.get("item_title", "") or "")})
    return out


def _probe_dur(path):
    """Длительность клипа в секундах (для равномерных таймингов подписей из
    --text). cv2, без внешнего ffprobe."""
    c = cv2.VideoCapture(path)
    fps = c.get(cv2.CAP_PROP_FPS) or 0
    n = c.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    c.release()
    return (n / fps) if fps else 0.0


def _read_text_arg(text):
    """--text: путь к файлу ИЛИ сам текст (строки/с метками). Возвращает сырой
    текст либо None."""
    if not text:
        return None
    if os.path.exists(text):
        with open(text, encoding="utf-8") as f:
            return f.read()
    return text


def _pick_music_hook(video, bible_path=None):
    """Хук+призыв для template="music" — из книги смыслов клиента, если не
    заданы явно (--hook/--hook-sub).

    Выбор ДЕТЕРМИНИРОВАННЫЙ: md5 от имени файла ролика, НЕ random/hash() (тот
    рандомизирован PYTHONHASHSEED между запусками — та же дисциплина, что уже
    принята в modules/script_writer/writer.py). Одно и то же имя ролика всегда
    даёт один и тот же хук — повторный рендер того же исходника не меняет
    текст исподтишка.

    Призыв по умолчанию — кодовое слово книги смыслов («пиши «{code_word}» в
    директ»), а не жёсткая «в описании ↓» референса (та ссылка-в-био — канал,
    не проверенный на всех клиентах; референсный вариант доступен явным
    --hook-sub)."""
    import modules.script_writer.bible as bible_mod
    b = bible_mod.load_bible(bible_path)
    if not b["hooks"]:
        raise SystemExit(
            "template=\"music\": книга смыслов не даёт хуков (%s). Подключите "
            "книгу (python bible_setup.py --install) или передайте --hook "
            "явно." % ("; ".join(b["warnings"]) or "хуков нет в файле"))
    idx = int(hashlib.md5(os.path.basename(video).encode("utf-8")).hexdigest(),
              16) % len(b["hooks"])
    hook = b["hooks"][idx]
    hook_sub = "пиши «%s» в директ" % b["code_word"]
    return hook, hook_sub


def _verify(meta, strict):
    """Проверки перед показом — и ЧЕСТНЫЙ ответ на них (TAIL-06).

    `checks.verify_talk` сообщает о беде ДВУМЯ разными способами, и второй до
    сих пор терялся:
      • обычные проблемы в строгом режиме — исключением `CheckError`;
      • а САМЫЙ тяжёлый случай, «файла ролика нет вовсе», она не бросает, а
        молча ВОЗВРАЩАЕТ списком — и оба сборщика этот список выкидывали.
        Ролика нет, а `build()` отдавал `meta` ровно как при успехе, и всё
        снаружи считало провал успехом.
    Поэтому список тут не выкидывается: в строгом режиме он — отказ, в мягком
    (`--soft`) остаётся замечанием и едет в `meta['check_problems']`, чтобы CLI
    отдал за него свой код возврата.
    """
    problems = list(checks.verify_talk(meta, strict=strict) or [])
    meta["check_problems"] = problems
    if problems and strict:
        raise checks.CheckError(
            "РОЛИК НЕ ПРОШЁЛ ПРОВЕРКИ ПЕРЕД ПОКАЗОМ — вот что не так:\n"
            + "\n".join("   - " + p for p in problems))
    return problems


def build_textmode(video, out, template, text=None, acc=None, karaoke=False,
                   karaoke_style=None, music=None, music_query=None,
                   music_variants=3, video2=None,
                   compare_labels=None, strict=True, overlays=None,
                   grade=None, progress_bar=False, sheet_zones=False,
                   style=None, hook=None, hook_sub=None, bible_path=None):
    """ВХОД-АГНОСТИК (E4-24/E4-23/P4): demo/compare/music — БЕЗ речи, транскрипта,
    лица и b-roll. demo/compare берут подписи из --text (равномерно или по
    меткам); music (референс владельца IMG_4731.MP4) — статичный хук+призыв на
    весь ролик, из --hook/--hook-sub или авто-подбором из книги смыслов
    (--hook не задан → modules/script_writer/bible.load_bible()). Звук — из
    локального банка музыки (--music) ИЛИ из Openverse по запросу (--music-query,
    BOX-39: у клиента банк обычно пуст, а --music тогда падал с ошибкой). Выход
    1080×1920, те же проверки перед показом, что и у говорящих форматов."""
    if template == "compare" and not video2:
        raise SystemExit("--template compare требует второй клип (--video2)")

    if template == "compare":
        # Предохранитель смысла (не рендера): перепутать video/video2 местами
        # молча не проверяется никаким гейтом — оба клипа одинаково валидны
        # геометрически, ролик просто скажет клиенту противоположное тому, что
        # задумано. Это НЕ блокирует сборку — рендер тут черновик для показа,
        # не публикация; но перед показом клиенту оператор обязан свериться.
        print("⚠️ COMPARE: перед публикацией покажи клиенту оба клипа и получи "
              "явное 'да', какой сверху (НЕ ПРАВИЛЬНО), какой снизу "
              "(ПРАВИЛЬНО) — перепутанные клипы меняют смысл ролика на "
              "противоположный.", flush=True)

    t_start = time.time()                       # R43: время сборки в метрики
    hook_text = hook_cta = None
    if template == "music":
        # MUSIC: подпись — не --text/cues (та система по-репличная, музыке не
        # подходит — см. engine_talk._music_caption). hook явный → используем
        # как есть; иначе авто-подбор из книги смыслов, детерминированный.
        if hook:
            hook_text = hook
            hook_cta = hook_sub if hook_sub is not None else "пиши в директ"
        else:
            hook_text, hook_cta = _pick_music_hook(video, bible_path)
            if hook_sub is not None:
                hook_cta = hook_sub
        cues = []
        print("1/3 music: хук «%s» (%s), призыв «%s»"
              % (hook_text, "явно" if hook else "книга смыслов", hook_cta),
              flush=True)
    else:
        dur = _probe_dur(video)
        raw = _read_text_arg(text)
        cues = engine_talk.cues_from_text(raw, dur, karaoke=karaoke) if raw else None
        print("1/3 %s: подписи из --text — %d шт. (речь/транскрипт/b-roll не нужны)"
              % (template, len(cues or [])), flush=True)

    print("2/3 рендер (%s%s)..."
          % (template, ", караоке" if karaoke else ""), flush=True)
    # sub_scale=1.0 (отмена E4-25, owner 16.08.2026): demo/compare возвращены к
    # обычному кеглю субтитра (100px) — как у всех остальных форматов. Раньше
    # (E4-25) тут стояло sub_scale=3.0 — субтитр раздували ×3 (74→222px) с
    # переносом строк, т.к. в demo/compare нет говорящей головы и субтитр был
    # единственным контентом в кадре. Решение отменено: крупный текст читался
    # тяжеловесно. Это ЕДИНСТВЕННЫЙ путь этих трёх форматов (build_textmode);
    # общий build()→render() sub_scale не передаёт → 1.0 → split/cutout/pip/
    # card/lower-third/text-behind остаются байт-в-байт. music не рисует cues
    # вовсе (hook_text/hook_sub — отдельный статичный слой, см. engine_talk).
    meta = engine_talk.render(video, None, out, acc_name=acc, template=template,
                              inserts=[], cues=cues, karaoke=karaoke,
                              karaoke_style=karaoke_style, video2=video2,
                              compare_labels=compare_labels, overlays=overlays,
                              hook_text=hook_text, hook_sub=hook_cta,
                              grade=grade, progress_bar=progress_bar,
                              sub_scale=1.0, style=style)

    # МУЗЫКА. Два независимых пути (BOX-39):
    #   --music        — локальный банк music/ (без сети, без атрибуции —
    #                    Pixabay её не требует). У клиента банк обычно ПУСТ:
    #                    раньше это падало с "нет аудиофайлов" и музыка молча
    #                    не работала вовсе — теперь честная подсказка вместо
    #                    падения, и путь ниже, который реально работает.
    #   --music-query  — Openverse (сеть, CC-BY, атрибуция ОБЯЗАТЕЛЬНА — см.
    #                    meta["music_variants"][i]["attribution"]). Рендерит
    #                    music_variants РАЗНЫХ треков, не один — человек
    #                    выбирает, какой публиковать, движок не решает за него.
    if music_query:
        import music as musicmod
        variants_dir = os.path.splitext(out)[0] + "_music_variants"
        try:
            choices = musicmod.score_choices(out, music_query, variants_dir,
                                             n=max(1, music_variants))
        except Exception as e:                    # noqa: BLE001 — сеть/API не наши
            raise SystemExit("Openverse недоступен или не ответил (%s). "
                             "Проверьте интернет и повторите, или используйте "
                             "--music с наполненным банком." % e)
        if not choices:
            raise SystemExit("по запросу %r в Openverse ничего не нашлось. "
                             "Пробуйте ОДНО слово ('corporate', не 'cinematic "
                             "corporate' — условия идут по И)." % music_query)
        best = choices[0]
        import shutil
        shutil.copy2(best["out"], out)             # основной out = первый вариант
        meta["music"] = best["title"]
        meta["music_variants"] = [
            {"n": c["n"], "file": c["out"], "title": c["title"],
             "author": c["author"], "attribution": c["attribution"]}
            for c in choices]
        meta["size_bytes"] = os.path.getsize(out)
        print("   музыка: %d вариант(ов) в %s, в out — «%s»"
              % (len(choices), variants_dir, best["title"]), flush=True)
        for c in choices:
            print("     #%d %s — %s" % (c["n"], c["title"], c["attribution"]),
                  flush=True)
        print("   ⚠ CC-BY требует указать автора: строка атрибуции выбранного "
              "варианта — в конец подписи или в первый комментарий.", flush=True)
    elif music:
        import shutil
        import music as musicmod
        if music in ("bank", "random", "1", "yes", ""):
            try:
                track = musicmod.pick_bank(seed=0)   # seed=0 — воспроизводимый выбор
            except RuntimeError as e:
                raise SystemExit(
                    "%s\nБанк пуст — это НОРМА для клиентской коробки, не "
                    "поломка. Два рабочих пути: (1) положить свои mp3 в music/ "
                    "и повторить; (2) --music-query \"<слово>\" — музыка через "
                    "Openverse, сеть, без ручной подготовки банка." % e)
        else:
            track = music
            if not os.path.exists(track):
                raise SystemExit("трек музыки не найден: %s" % track)
        tmp = out + ".withmusic.mp4"
        musicmod.add_music(out, track, tmp)      # -c:v copy → видео байт в байт
        shutil.move(tmp, out)
        meta["music"] = os.path.basename(track)
        meta["size_bytes"] = os.path.getsize(out)
        print("   музыка: %s" % meta["music"], flush=True)

    print("3/3 контактный лист + проверки...", flush=True)
    sheet = contact_sheet(out, os.path.splitext(out)[0] + "_SHEET.jpg",
                          overlay_zones=sheet_zones, meta=meta)
    meta["sheet"] = sheet
    meta["broll"] = []
    _verify(meta, strict)                       # TAIL-06: провал ≠ успех
    write_metrics(meta, t_start)                # R43: после проверок
    return meta


#: короче этого кусок спикера джамп-катом не режем.
#: ЗАМЕР 28.08.2026, тот же ролик, сцены 1.4-4.0:
#:   планировщик БЕЗ джамп-катов -> 20.84 склейки/мин (реф 20.08) — попадание;
#:   планировщик С джамп-катами на каждом стыке -> 27.78 — перебор.
#: Вывод: темп референса набирается СМЕНОЙ ВРЕЗОК, а не резкой говорящей головы.
#: Поэтому порог высокий: режем только очень долгие куски, где спикер иначе
#: зависает. Механизм рабочий, но включается редко.
JUMP_MIN_HOLD = 4.5


def build(video, transcript, blocks_path, out, acc=None, template="split",
          providers=("pixabay",), picks=None, per=4, plan=False, strict=True,
          overlays=None, karaoke=None, karaoke_style=None,
          word_flash=False, progress_bar=False, list_counter=False, grade=None,
          punch_in=False, text=None, music=None, music_query=None,
          music_variants=3, video2=None,
          compare_labels=None, exclude_ids=None, wide=False,
          matte_backend="mediapipe", pace=None, tighten=None,
          tighten_silence=True,
          sheet_zones=False, from_lock=None, style=None,
          broll_count=None,
          hook=None, hook_sub=None, bible_path=None):
    """Собрать разговорный Reels. `plan=True` — пропустить блоки через
    планировщик монтажа (Стадия 6): он решает, где сплит, а где спикер на весь
    кадр, и держит долю b-roll ≤ 50%. Без него b-roll накрывает ВЕСЬ ролик.

    `template="text-behind"` (формат 4): КРУПНЫЙ заголовок из поля `title` блока
    ложится за силуэт спикера. Данные (title) пишет Claude в blocks.json.
    `punch_in=True` (формат 7): микро-наезд на лицо на хуке и эмоциональных
    пиках (блоки intent hook/emotion) — аддитивно поверх любой раскладки.

    ⚠️ `picks` нумерует блоки ПОСЛЕ планировщика: он оставляет не все, и старые
    номера с `plan=True` указывают не туда. Пересобирать листы выбора заново.

    Накладные форматы (word_flash/progress_bar/list_counter/grade) — оверлеи
    поверх любой раскладки; их данные (flash_word, item_no…) читаются из ИСХОДНЫХ
    блоков ДО планировщика, чтобы тайминги не сбились при перекладке сцен.

    demo/compare/music (E4-24/E4-23/P4) — вход-агностик, без речи/транскрипта/
    блоков/b-roll: demo/compare берут подписи из `text`; music — статичный
    хук+призыв из `hook`/`hook_sub` (без `hook` — авто-подбор из книги
    смыслов, `bible_path` переопределяет путь к ней). Звук — из `music`,
    второй клип демо-сравнения — `video2`. Уходят в build_textmode отдельной
    веткой (общий конвейер их не касается).

    `karaoke=None` — авто (R9): включено для говорящих раскладок
    (KARAOKE_DEFAULT_TEMPLATES), выключено для demo/compare. Явные True/False
    работают как раньше (обратная совместимость вызовов).

    `style` (R30) — json-пресет визуального стиля клиента (styles/*.json) или
    готовый style.Style. None → дефолт движка, картинка байт-в-байт. Приоритет:
    дефолт < style < явные аргументы (acc/karaoke_style приходят из CLI как
    None, пока флаг не задан — тогда значение берёт стиль).

    `pace` (E4-49) живёт по ТОМУ ЖЕ правилу: None (флаг не задан) → пороги
    планировщика берутся из style.pace целиком; "calm"/"dynamic" → пресет
    перекрывает у стиля min_scene/max_scene. Раньше значение по умолчанию было
    "calm", и стиль по темпу не спрашивали вообще.
    """
    t_start = time.time()                       # R43: время сборки в метрики
    # R30: стиль разбираем ОДИН раз — здесь он нужен оркестратору (PUNCH_HOLD,
    # подпись акцента в timeline, строка лога). В движок уходит исходный
    # аргумент: разрешение «дефолт < style < флаг» живёт в engine_talk.render.
    _st = (style if isinstance(style, style_mod.Style)
           else style_mod.load(style))
    acc_label = acc or (_st.accent if isinstance(_st.accent, str) else _st.name)
    if karaoke is None:
        karaoke = karaoke_default(template)
    if template in ("demo", "compare", "music"):
        return build_textmode(video, out, template, text=text, acc=acc,
                              karaoke=karaoke, karaoke_style=karaoke_style,
                              music=music, music_query=music_query,
                              music_variants=music_variants, video2=video2,
                              compare_labels=compare_labels, strict=strict,
                              overlays=overlays, grade=grade,
                              progress_bar=progress_bar,
                              sheet_zones=sheet_zones, style=style,
                              hook=hook, hook_sub=hook_sub,
                              bible_path=bible_path)

    # R13: стадия TIGHTEN — ДО всего остального. Паузы речи вырезаются
    # (jump-cut), дальше конвейер живёт в ужатой шкале: video и transcript
    # подменяются на <out>_tight.*; blocks.json ОБЯЗАН быть построен по
    # ужатому транскрипту (тайминги сырого не совпадут).
    # tighten=None — «авто»: подрезаем, когда есть транскрипт. Раньше было
    # False, и подрезка не включалась НИКОГДА, хотя ради неё написан целый
    # модуль. Дефолтный режим — по звуку (tighten_silence): подрезка по зазорам
    # транскрипта на материале whisperx не работает вовсе, форс-выравнивание
    # растягивает слово до соседнего и тишина прячется ВНУТРИ границ слов.
    # Замер: 17.4% хронометража — тишина, 96% её внутри границ.
    do_tighten = bool(transcript) if tighten is None else bool(tighten)
    if tighten and not transcript:
        raise SystemExit("--tighten требует --transcript: паузы режутся "
                         "по словам transcript.json")
    tight_dur = None
    if do_tighten:
        import talk_tighten
        t_out = os.path.splitext(out)[0] + "_tight.mp4"
        st = talk_tighten.tighten(video, transcript, t_out,
                                  silence=tighten_silence)
        talk_tighten.report(st)
        tight_dur = st["dur_after"]
        video, transcript = t_out, st["transcript"]

    with open(blocks_path, encoding="utf-8") as f:
        blocks = json.load(f)["blocks"]
    # Конец ролика запоминаем СРАЗУ: ниже `blocks` переприсваивается на
    # p["broll_blocks"] (только блоки с врезками), и её последний t1 —
    # это конец последней врезки, а не ролика. На этом я уже ошибся:
    # потолок разрыва считался от 41.3 c вместо настоящих 44.9.
    _reel_end = max([float(b["t1"]) for b in blocks] or [0.0])

    # ПРЕДОХРАНИТЕЛЬ ПОСЛЕ ПОДРЕЗКИ. Блоки размечены по шкале ИСХОДНОГО ролика;
    # после подрезки шкала другая, и старые t0/t1 указывают не туда. Раньше
    # здесь печаталось предупреждение, а сборка шла дальше — то есть ролик
    # молча собирался с разъехавшимися сценами, и увидеть это можно было только
    # глазами на готовом видео. Теперь останавливаемся: блоки, не влезающие в
    # ужатый хронометраж, размечены по сырому транскрипту заведомо.
    if tight_dur is not None and blocks:
        последний = max(float(b.get("t1") or 0) for b in blocks)
        if последний > tight_dur + 0.5:
            raise SystemExit(
                ("blocks.json размечен по СЫРОМУ ролику, а он подрезан: "
                 "последний блок кончается на %.2f c, ужатый ролик длится "
                 "%.2f c." % (последний, tight_dur))
                + " Пересобери блоки по " + str(transcript)
                + " — или собери без подрезки ключом --no-tighten.")

    # данные накладных форматов — из исходных блоков (тайминги в шкале ролика,
    # планировщик их не меняет, только режет/снимает сцены b-roll).
    flashes = flashes_from_blocks(blocks) if word_flash else None
    list_items = items_from_blocks(blocks) if list_counter else None
    # ЗАГОЛОВКИ формата 4 и ОКНА зума формата 7 берём из ИСХОДНЫХ блоков (до
    # планировщика: он схлопывает блоки в broll_blocks и теряет title/intent).
    titles = ([{"t0": b["t0"], "t1": b["t1"], "text": b["title"]}
               for b in blocks if str(b.get("title", "")).strip()]
              if template == "text-behind" else None)
    # ПЛАШКИ формата 5 (lower-third): число/тезис из поля `lower_third` блока —
    # живой спикер на весь кадр + лента снизу. Данные пишет Claude в blocks.json.
    # Из ИСХОДНЫХ блоков (тайминги в шкале ролика), b-roll формату не нужен.
    lt_items = ([{"t0": b["t0"], "t1": b["t1"], "text": str(b["lower_third"]).strip()}
                 for b in blocks if str(b.get("lower_third", "")).strip()]
                if template == "lower-third" else None)
    # хук/пик = микро-наезд у начала сцены (не весь блок: удар должен быть
    # коротким), длительность ограничиваем PUNCH_HOLD, дальше кадр «отпускает».
    PUNCH_HOLD = _st.punch_hold                 # R30: дефолт стиля = прежние 1.8
    punch = None
    if punch_in:
        punch = [{"t0": b["t0"], "t1": min(b["t1"], b["t0"] + PUNCH_HOLD)}
                 for b in blocks
                 if str(b.get("intent", "")).lower() in ("hook", "emotion")]
        if not punch:
            print("  ! punch-in: в блоках нет intent hook/emotion — зум не заданы",
                  flush=True)

    # окно показа кодового слова (E4-11): по умолчанию — блоки с intent CTA.
    # Регистр не важен (R2): промпт/валидатор эмитят "cta" строчными
    # (talk_segment.py:26,111), сравнение по .lower() — как в talk_plan.py:70.
    # Иначе окно не находилось, cta_at=None и слово висело ВЕСЬ ролик.
    if overlays and overlays.get("cta") and not overlays.get("cta_at"):
        cta = [b for b in blocks if str(b.get("intent", "")).lower() == "cta"]
        if cta:
            overlays = dict(overlays, cta_at=(cta[0]["t0"], cta[-1]["t1"]))

    p = None
    if plan:
        # E4-49: ПРИОРИТЕТ ТЕМПА — «дефолт < style < явный флаг», то же правило,
        # что у --acc и --karaoke-style (ФОРМАТЫ_РАЗГОВОРНЫХ_REELS.md). До этого
        # --pace имел default="calm", поэтому «флаг не задан» и «задан calm» были
        # неразличимы, а поля style.pace (min_scene/max_scene/broll_share/
        # max_series) не читал никто — слой стиля по темпу был мёртв у всех шести
        # клиентских пресетов. Теперь база — стиль (его дефолт 4.0/8.0/0.5/2 равен
        # talk_plan.MIN_SCENE/MAX_SCENE/BROLL_SHARE/MAX_SERIES, то есть без
        # --style и без --pace план байт-в-байт прежний), а флаг, если задан,
        # перекрывает пороги сцены своим пресетом.
        pk = dict(min_scene=_st.pace_min_scene, max_scene=_st.pace_max_scene,
                  broll_share=_st.pace_broll_share,
                  max_series=_st.pace_max_series)
        if pace is not None:
            # R27: пресет темпа задаёт ТОЛЬКО пороги сцены (calm 4/8, dynamic
            # 2.5/5); бюджет b-roll и длина серии остаются за стилем. R28:
            # динамичный темп дополнительно включает микро-врезки 1-2 c на
            # слово-акцент (flash_word).
            pk.update(talk_plan.pace_kwargs(pace))
        print("0/4 планировщик монтажа (темп: %s — сцены %.1f-%.1f с, b-roll "
              "≤%d%%, серия ≤%d)..."
              % (pace if pace is not None else "стиль «%s»" % _st.name,
                 pk["min_scene"], pk["max_scene"],
                 round(pk["broll_share"] * 100), pk["max_series"]), flush=True)
        if picks:
            print("  ! picks заданы вместе с --plan: номера относятся к сценам "
                  "ПОСЛЕ планирования, не к исходным блокам", flush=True)
        p = talk_plan.plan(blocks, micro=(pace == "dynamic"), **pk)
        # пол числа врезок живёт только в проверке: плану он не нужен,
        # а plan() лишнего ключа не примет (правило П1).
        for w in talk_plan.validate(p, min_broll=_st.pace_min_broll, **pk):
            print("  ! " + w, flush=True)
        print(talk_plan.ascii_plan(p), flush=True)
        blocks = p["broll_blocks"]

    if template == "lower-third":
        # lower-third: спикер живой на весь кадр, снизу — плашка-лента из блоков.
        # b-roll НЕ нужен: вставки формата 5 несут текст сами (движок рисует
        # _lower_third по it["text"]). Нет размеченных плашек — просто живой спикер.
        inserts = lt_items or []
        print("1/4 lower-third: %d плашек из блоков (b-roll не нужен)"
              % len(inserts), flush=True)
        if not inserts:
            print("  ! в блоках нет поля lower_third — плашки не заданы, спикер "
                  "на весь кадр", flush=True)
    elif _st.pace_max_broll == 0:
        # ФОРМАТ БЕЗ ПЕРЕБИВОК (владелец 04.09.2026), «СУБТИТРЫ СТАНДАРТ»:
        # только видео клиента и подпись, вставок нет вовсе. Сток не опрашиваем
        # совсем — это быстрее, не жжёт запросы и не может подсунуть случайный
        # кадр. Пустой список здесь НОРМА, а не сбой: проверка «ни одной вставки
        # не набралось» живёт в ветке подбора ниже и сюда не достаёт.
        inserts = []
        print("1/4 перебивки не нужны: формат «%s» собирается без них"
              % _st.name, flush=True)
    else:
        print("1/4 подбор b-roll со стоков (%s%s)..."
              % (", ".join(providers), ", ДАЛЬНИЙ план" if wide else ""), flush=True)
        # R38: from_lock — повтор вставок из лока (без сети); иначе подбор с
        # автозаписью лока рядом с blocks (воспроизводимость ролика).
        inserts = talk_broll.broll_for_blocks(
            blocks, providers, picks=picks, per=per,
            exclude_ids=exclude_ids, wide=wide, template=template,
            from_lock=from_lock,
            max_inserts=_st.pace_max_broll,
            lock_out=(None if from_lock else talk_broll.lock_path_for(blocks_path)))
        # Пол числа врезок проверяем ЗДЕСЬ, а не по плану: планировщик
        # может насчитать десять b-roll-сцен, а до кадра дойти три —
        # часть запросов не находит клипа, часть срезает потолок. Пол,
        # проверенный по плану, был бы декоративным (правило П1).
        # РАВНОМЕРНОСТЬ ВРЕЗОК (владелец 02.09.2026). Порога по числу мало:
        # четыре врезки, сбитые в один конец, формально проходят, а зритель
        # первую половину ролика смотрит говорящую голову без картинки.
        if inserts and _st.pace_max_gap < 1.0:
            # Длина РОЛИКА, а не конец последней врезки: иначе потолок
            # занижается, а хвост после последней врезки не считается вовсе
            # (замер: 19.8 c разрыва против «потолка» 18.6 при настоящих 20.2).
            _dur = max(_reel_end, max(float(i["t1"]) for i in inserts))
            _edges = [0.0] + sorted(float(i["t0"]) for i in inserts) + [_dur]
            _gap = max(_edges[k + 1] - _edges[k] for k in range(len(_edges) - 1))
            if _gap > _st.pace_max_gap * _dur:
                raise SystemExit(
                    "ВРЕЗКИ СБИТЫ В ОДИН КОНЕЦ: самый длинный кусок без врезки "
                    "%.1f c при потолке %.1f c (%.0f%% хронометража). Формат "
                    "«%s» требует равномерного распределения. Что делать: "
                    "добавить смысловые блоки с visual_query в пустой половине "
                    "либо ослабить pace.max_gap."
                    % (_gap, _st.pace_max_gap * _dur, _st.pace_max_gap * 100,
                       _st.name))
        # СКОЛЬКО ПЕРЕБИВОК ХОЧЕТ КЛИЕНТ (решение владельца 03.09.2026).
        # Пресет задаёт значение по умолчанию, клиент может попросить своё —
        # «делай по шесть перебивок». Потолок поднимаем вместе с полом, иначе
        # запрошенное число упрётся в старую крышку и клиент не поймёт, почему
        # его просьбу не выполнили.
        _min_broll = _st.pace_min_broll
        if broll_count:
            _min_broll = int(broll_count)
            _st.pace_max_broll = max(_st.pace_max_broll, _min_broll)
            print("  перебивок по просьбе клиента: не меньше %d" % _min_broll,
                  flush=True)
        if _min_broll and len(inserts) < _min_broll:
            raise SystemExit(
                "врезок в кадре %d, а нужно не меньше %d (формат «%s»). "
                "Причина обычно одна из двух: запросы не нашли клипа в "
                "источниках (%s) или сработал потолок pace.max_broll=%d. "
                "Что делать — docs/ПРАВИЛА_ПЕРЕБИВОК.md, правило П1."
                % (len(inserts), _min_broll, _st.name,
                   ", ".join(providers), _st.pace_max_broll))
        if not inserts:
            # text-behind несёт заголовок сам (фон может быть тёмным), поэтому без
            # b-roll он ВАЛИДЕН. Остальным раскладкам b-roll обязателен.
            if template == "text-behind" and titles:
                print("  ! b-roll не набрался — text-behind на тёмном фоне "
                      "(заголовок + силуэт)", flush=True)
                inserts = []
            else:
                raise SystemExit("Ни одной вставки не набралось — проверь запросы/сеть.")

    # ВХОД-АГНОСТИК (E4-24): если задан --text, субтитры берутся из него, а не из
    # транскрипта (речи может не быть). Без --text (text=None) cues=None и путь
    # остаётся прежним — байт в байт (транскрипт читается в движке как раньше).
    cues = None
    if text:
        raw = _read_text_arg(text)
        cues = engine_talk.cues_from_text(raw, _probe_dur(video), karaoke=karaoke)

    print("2/4 рендер (%s%s%s%s)..."
          % (template,
             ", караоке:" + (karaoke_style or _st.karaoke_style) if karaoke else "",
             ", зум-удар" if punch else "",
             ", стиль:%s/%s" % (_st.name, _st.font_name) if style else ""),
          flush=True)
    # ДЖАМП-КАТЫ (28.08.2026). Планировщик уже нарезал ролик на сцены, но
    # движок про них не знал: он видел только врезки. Отдаём ему границы
    # сцен СПИКЕРА — на них меняется крупность кадра, и стык читается как
    # рез. Материал при этом не режется: длительность ролика не меняется.
    # Без плана (p is None) или без переадрирования лица — прежний путь.
    speaker_cuts = None
    if p is not None and _st.speaker_face_y > 0:
        # Режем не каждый стык, а только ДОЛГИЕ куски спикера. Замер
        # 28.08: стык на каждой сцене дал 27.8 склейки/мин при 20.1 у
        # референса — перебор. Референс возвращает спикера на 1.4-3.2 c
        # и режет его, только когда он держит кадр дольше.
        speaker_cuts = [round(sc["t0"], 3) for sc in p["scenes"][1:]
                        if sc["kind"] == "speaker"
                        and sc["t1"] - sc["t0"] >= JUMP_MIN_HOLD]
        if speaker_cuts:
            print("  джамп-каты: %d стыков на сценах спикера"
                  % len(speaker_cuts), flush=True)
    meta = engine_talk.render(video, transcript, out, acc_name=acc,
                              template=template, inserts=inserts, overlays=overlays,
                              karaoke=karaoke, karaoke_style=karaoke_style,
                              flashes=flashes, progress_bar=progress_bar,
                              list_items=list_items, grade=grade,
                              titles=titles, punch=punch, cues=cues,
                              matte_backend=matte_backend, style=style,
                              speaker_cuts=speaker_cuts)

    print("3/4 контактный лист...", flush=True)
    sheet = contact_sheet(out, os.path.splitext(out)[0] + "_SHEET.jpg",
                          overlay_zones=sheet_zones, meta=meta)

    meta["sheet"] = sheet
    meta["broll"] = [{"t0": i["t0"], "t1": i["t1"], "query": i.get("query", ""),
                      "cand": i.get("cand", "")} for i in inserts]

    if p is not None:                       # артефакт §6 архитектуры
        tl = os.path.splitext(out)[0] + "_timeline.json"
        with open(tl, "w", encoding="utf-8") as f:
            json.dump(talk_plan.to_timeline(p, inserts, template=template,
                                            acc=acc_label),
                      f, ensure_ascii=False, indent=1)
        meta["timeline"] = tl

    # ПРОВЕРКИ ПЕРЕД ПОКАЗОМ — как у каруселей checks.verify(). Раньше их тут не
    # было вовсе: оркестратор рисовал контактный лист и отчитывался «готово», а
    # геометрию, звук, флаг поворота, субтитр на лице и шов сплита никто не
    # смотрел. Лист рисуем ДО проверки — чтобы при падении было на что глядеть.
    print("4/4 проверки перед показом...", flush=True)
    # ДРОЖАНИЕ ГОТОВОГО РОЛИКА (владелец 02.09.2026: «плавно, без дрожания»).
    # Предфильтр по исходнику занижает тряску в 1.2-3 раза, и множитель
    # зависит от содержимого — гарантию даёт только замер СОБРАННОГО ролика.
    # Порог 2.0 px по p90: у спокойных врезок 0.5-1.2, у дрожащей было 3.66.
    try:
        import tools_fmt_shake as _sh
        _bad = []
        for _it in (inserts or []):
            _r = _sh.measure(out, float(_it["t0"]) + 0.2, float(_it["t1"]) - 0.2)
            if _r and _r["p90"] > 2.0:
                _bad.append((str(_it.get("cand") or _it.get("id")),
                             float(_it["t0"]), float(_it["t1"]), _r["p90"]))
        if _bad and not strict:
            # У КЛИЕНТА strict=False: черновик уходит человеку в окно-вето, и
            # ронять сборку исключением нельзя — мост ловит только SystemExit,
            # а CheckError прошёл бы наружу и уронил бы весь слот плана.
            # Поэтому в мягком режиме — замечание в meta, как у остальных
            # проверок (см. _verify).
            meta.setdefault("check_problems", []).append(
                "ДРОЖАНИЕ ВРЕЗКИ: "
                + "; ".join("%s (%.1f-%.1f) p90 %.2f px" % b for b in _bad))
            print("  ! дрожание врезки — замечание (мягкий режим): %s"
                  % "; ".join("%s p90 %.2f" % (b[0], b[3]) for b in _bad),
                  flush=True)
            _bad = []
        if _bad:
            raise checks.CheckError(
                "ДРОЖАНИЕ ВРЕЗКИ — ролик не выпускаю: "
                + "; ".join("%s (%.1f-%.1f) p90 %.2f px" % b for b in _bad)
                + ". Порог 2.0. Что делать: добавь id клипа в "
                  "talk_broll.BANNED_CLIP_IDS либо смени запрос блока.")
    except ImportError:
        # МОЛЧАТЬ ЗДЕСЬ НЕЛЬЗЯ. Раньше стояло `pass`, и у клиента это выглядело
        # так: сборка отчитывается «проверки пройдены», а сторож дрожания не
        # выполнялся ВООБЩЕ — модуль не ехал в коробку. Владелец просил гарантию
        # прямо («плавно, без дрожания»), и тихий пропуск отменял её незаметно.
        # Тот же класс, что весь урок недели: отказ, выглядящий как успех.
        # Ронять сборку при этом нельзя: ролик собран и годен, не проверена ОДНА
        # характеристика. Поэтому громкое замечание и след в meta — человек видит
        # строку, а внешний вызывающий может прочитать признак.
        meta["shake_checked"] = False
        print("  ! СТОРОЖ ДРОЖАНИЯ НЕ ВЫПОЛНЕН: модуль tools_fmt_shake не найден.",
              flush=True)
        print("    Ролик собран, но на дрожание врезок НЕ проверен — глянь глазами.",
              flush=True)
        print("    Если это клиентская коробка, модуль не доехал: скажи разработчику.",
              flush=True)
    else:
        meta["shake_checked"] = True
    _verify(meta, strict)                       # TAIL-06: провал ≠ успех
    write_metrics(meta, t_start)                # R43: после проверок
    return meta


# ── КОДЫ ВОЗВРАТА СБОРЩИКА (TAIL-06) ─────────────────────────────────────────
# Схема — из CONTRACT.md §2 («код возврата это СТЕПЕНЬ БЕДЫ, а не да/нет»),
# пересказанная на задачу сборщика. Снаружи (планировщик, контент-план, агент в
# коробке) спрашивают ровно одно: «показывать это клиенту можно?»
#   0 — ролик собран и годен к показу. Предупреждения проверок («! …») сюда же:
#       они и задуманы как «глянь глазами», ронять на них нормальную работу
#       нельзя — это было бы хуже исходной болезни.
#   2 — ролик СОБРАН, но проверки его завернули, а падать запретил сам человек
#       флагом --soft. Файл есть, показывать нельзя — тот самый «урезанный
#       режим» контракта. Не 1: бросать всё и чинить не нужно, --soft просят
#       осознанно и для отладки подбора.
#   1 — ролика НЕТ: конвейер оборвался, движок упал, файл не записан. Красное.
# Код 2 уже занят гейтом активации («рендер не разрешён») — по смыслу это тот
# же «сделано не всё, но паниковать не о чем», так что схема не расходится.
EXIT_OK = 0
EXIT_SOFT = 2
EXIT_FAIL = 1


def _say_failed(reason):
    """Провал сборки — словами человека, а не стектрейсом (CONTRACT.md §2)."""
    print("\nРОЛИК НЕ СОБРАН — готового файла нет, публиковать нечего.")
    print("Причина: %s" % reason)
    print("Что делать: посмотрите выше по выводу первую строку с ошибкой — в "
          "ней сказано, чего не хватило (исходный файл, сеть, вставки, "
          "проверки перед показом). Если непонятно — покажите эти строчки "
          "тому, кто ставил завод.")


def main():
    ap = argparse.ArgumentParser(
        description="Говорящее видео → разговорный Reels (Split, b-roll со стоков)")
    ap.add_argument("--video", required=True, help="исходный клип (говорящее видео "
                    "ИЛИ процесс-клип для demo; для compare — верхний «плохо»)")
    ap.add_argument("--transcript", help="transcript.json (стадия 2). Не нужен для "
                    "demo/compare и когда субтитры заданы через --text")
    ap.add_argument("--blocks", help="blocks.json (смысл + запросы). Не нужен для "
                    "demo/compare")
    ap.add_argument("--out", required=True, help="куда писать готовый mp4")
    ap.add_argument("--acc", default=None,
                    help="акцент (по умолчанию — из --style, иначе lime)")
    ap.add_argument("--style", default=None, metavar="PATH",
                    help="json-пресет визуального стиля клиента (styles/*.json, "
                         "R30): гарнитура, кегль и вид субтитра, акцент, позиции "
                         "подписи, караоке, безопасная зона. Без флага — дефолт "
                         "движка байт-в-байт. Явные флаги (--acc, --karaoke-style…) "
                         "сильнее стиля")
    # ЕДИНСТВЕННЫЙ ИСТОЧНИК ПРАВДЫ по раскладкам — reels_format.py (LAYOUTS /
    # LAYOUTS_DISABLED / LAYOUTS_CLI). Импортировать его ОТСЮДА нельзя: этот
    # файл едет клиенту в коробке, а reels_format.py — дев-инструмент и в
    # поставку не входит, аудит сборки (build_box.box_import_gaps) уронил бы
    # сборку. Поэтому список ниже сверяется с реестром автоматически, разбором
    # исходника: reels_format.cli_choices_drift() — её зовёт
    # scripts/regress_formats.py перед каждым слиянием. Раньше на этом же месте
    # лежала ТРЕТЬЯ копия знания «какие раскладки сняты с раздачи» — прямо в
    # тексте справки, и она уже успела разойтись (про music не говорила ничего).
    ap.add_argument("--template", default="split",
                    choices=["split", "pip", "card", "lower-third",
                             "demo", "compare", "music"],
                    help="split — экран пополам; "
                         "pip — голова маленькой в углу, вставка на весь экран "
                         "(композиция A); card — голова крупно, вставка "
                         "полноэкранной перебивкой (композиция B); lower-third — "
                         "спикер живой, снизу плашка-лента с числом/тезисом из блока "
                         "(денежный формат, поверх спикера, не на весь экран); "
                         "demo — процесс-клип (руки/предмет) + подписи из --text + "
                         "музыка (NAVIDUU Ф5, без речи/лица); compare — два клипа "
                         "стопкой «НЕ ПРАВИЛЬНО ✗»/«ПРАВИЛЬНО ✓» (NAVIDUU Ф3, "
                         "второй клип через --video2); music — видео-эстетика без "
                         "говорящей головы под музыку, статичный хук+призыв на весь "
                         "ролик (P4, референс владельца) — текст из --hook/--hook-sub "
                         "или авто-подбор из книги смыслов. Раскладки, временно "
                         "снятые с клиентской раздачи, в этом списке не показаны — "
                         "их перечень и причины см. ФОРМАТЫ_РАЗГОВОРНЫХ_REELS.md")
    ap.add_argument("--hook", help="template music: хук-заголовок (3 строки макс, "
                    "статичный на весь ролик). Без флага — авто-подбор из книги "
                    "смыслов (детерминированно по имени файла --video)")
    ap.add_argument("--hook-sub", dest="hook_sub",
                    help="template music: подпись-призыв под хуком (кегль "
                    "0.43-0.45× от хука). Без флага — «пиши «{кодовое слово}» в "
                    "директ» из книги смыслов")
    ap.add_argument("--bible", dest="bible_path",
                    help="template music: путь к книге смыслов для авто-подбора "
                    "хука (без флага — путь по умолчанию установки)")
    ap.add_argument("--text", help="субтитры/подписи из ЗАДАННОГО текста (файл или "
                    "строки; метки [t0-t1]/[t0] опц.) — когда речи нет (E4-24). "
                    "Заменяет транскрипт")
    ap.add_argument("--music", nargs="?", const="bank",
                    help="музыка из локального банка (без значения — случайный трек "
                    "банка seed=0; или путь к mp3). Заменяет звук — для demo/compare. "
                    "Банк у клиента обычно пуст — см. --music-query")
    ap.add_argument("--music-query", dest="music_query",
                    help="музыка через Openverse (сеть, CC-BY) по поисковой фразе — "
                    "одно слово надёжнее ('corporate', не 'cinematic corporate': "
                    "Openverse ищет по И). Независимо от --music. Рендерит "
                    "--music-variants готовых файлов на выбор, не один")
    ap.add_argument("--music-variants", dest="music_variants", type=int, default=3,
                    help="сколько вариантов трека предложить при --music-query "
                    "(по умолчанию 3, разные треки — не варианты одного)")
    ap.add_argument("--video2", help="второй клип для --template compare (нижний "
                    "«ПРАВИЛЬНО»)")
    ap.add_argument("--compare-bad", dest="compare_bad", default=None,
                    help="текст верхней плашки compare (по умолч. «НЕ ПРАВИЛЬНО ✗»)")
    ap.add_argument("--compare-good", dest="compare_good", default=None,
                    help="текст нижней плашки compare (по умолч. «ПРАВИЛЬНО ✓»)")
    ap.add_argument("--providers", default="pixabay",
                    help="источники через запятую: local,pixabay,pexels")
    ap.add_argument("--plan", action="store_true",
                    help="планировщик монтажа (Стадия 6): доля b-roll ≤ 50%%, "
                         "спикер-only на хуке/призыве/пике")
    ap.add_argument("--pace", choices=sorted(talk_plan.PACES), default=None,
                    help="R27: темп планировщика (работает с --plan). calm — "
                         "сцены 4-8 с; dynamic — 2.5-5 с. E4-49: БЕЗ флага темп "
                         "берётся из стиля (style.pace), а не молча из calm; "
                         "явный флаг сильнее стиля")
    ap.add_argument("--no-tighten", dest="tighten", action="store_false",
                    default=None,
                    help="собрать БЕЗ подрезки тишины. Нужен, когда blocks.json "
                         "размечен по сырому ролику и пересобирать его сейчас "
                         "не хочется")
    ap.add_argument("--tighten", dest="tighten", action="store_true",
                    help="R13: ПЕРЕД сборкой вырезать паузы речи (jump-cut, "
                         "talk_tighten): рядом с --out появятся <out>_tight.mp4 "
                         "и <out>_tight_transcript.json, конвейер идёт по ним. "
                         "ВНИМАНИЕ: blocks.json строить по УЖАТОМУ транскрипту — "
                         "тайминги блоков от сырого не совпадут. БЕЗ флага "
                         "подрезка всё равно идёт, когда есть --transcript: "
                         "выключить можно --no-tighten")
    # ⚠️ default=None ОБЯЗАТЕЛЕН, это не косметика. С обычным store_true парсер
    # всегда присылал явный False, и он затирал внутрифункциональный дефолт
    # (tighten_silence=True): вырезка пауз у клиента не работала, хотя в коде
    # была включена. store_true без default=None не отличает «человек не просил»
    # от «человек попросил выключить» — а разница здесь ровно в том, режем мы
    # 9-12% хронометража или ноль. Выключение осталось явным: --no-tighten-silence.
    ap.add_argument("--tighten-silence", dest="tighten_silence",
                    action="store_true", default=None,
                    help="E4-54: вместе с --tighten искать паузы ПО ЗВУКУ, а не "
                         "по зазорам транскрипта. Зазоры на материале whisperx "
                         "не работают вовсе (форс-выравнивание растягивает "
                         "слово до соседнего, замер: 0.00%% вырезано на 5 живых "
                         "роликах), звук возвращает 9-12%% хронометража. "
                         "Заодно подрезает границы слов по звуку, отчего "
                         "субтитр встаёт точнее, чем стоял ДО вырезки")
    ap.add_argument("--no-tighten-silence", dest="tighten_silence",
                    action="store_false",
                    help="E4-54: вернуть СТАРЫЙ поиск пауз по зазорам "
                         "транскрипта. Замер: на живом материале он не режет "
                         "ничего (0.00%%), поэтому нужен разве что для сверки")
    ap.add_argument("--sheet-zones", dest="sheet_zones", action="store_true",
                    help="R49: на кадры контактного листа нанести рамки "
                         "безопасных зон Instagram (SAFE_* движка) и рамку "
                         "субтитра sub_box из meta — видно, что текст не "
                         "заезжает под UI. Без флага лист прежний")
    ap.add_argument("--from-lock", dest="from_lock",
                    help="R38: собрать вставки ИЗ ЛОКА (<blocks>_stock.lock.json) "
                         "без сети — точный повтор подбора прошлой сборки. Без "
                         "флага подбор обычный, лок пишется автоматически")
    ap.add_argument("--soft", action="store_true",
                    help="не ронять сборку на проблемах проверок (печатать и идти "
                         "дальше) — для отладки подбора, не для показа Валерии")
    ap.add_argument("--cta", help="кодовое слово в CTA-блоке, напр. РОСТ (E4-11)")
    ap.add_argument("--cta-sub", default="пиши в личку",
                    help="подпись под кодовым словом")
    ap.add_argument("--hint", help="подсказка-стикер вверху, напр. «смотри до конца»")
    ap.add_argument("--punch-in", dest="punch_in", action="store_true",
                    help="зум-удар (формат 7): микро-наезд на лицо на хуке и "
                         "эмоциональных пиках (блоки intent hook/emotion), рампа "
                         "4–6 кадров — аддитивно поверх любой раскладки")
    ap.add_argument("--karaoke", action="store_true", default=None,
                    help="караоке-субтитры: строка из нескольких слов, активное "
                         "слово подсвечено по таймингам WhisperX (+scale-pop). "
                         "R9: для говорящих раскладок (split/cutout/text-behind/"
                         "pip/card/lower-third) включено ПО УМОЛЧАНИЮ; флаг нужен "
                         "только demo/compare. Старые команды с --karaoke работают "
                         "как раньше")
    ap.add_argument("--no-karaoke", dest="no_karaoke", action="store_true",
                    help="выключить караоке (вернуть короткие реплики 1-2 слова); "
                         "перекрывает --karaoke и дефолт R9")
    ap.add_argument("--karaoke-style", default=None,
                    choices=["accent", "plate", "reveal"],
                    help="стиль караоке: accent — перекраска активного слова "
                         "(по умолч. — или значение из --style); plate — "
                         "подсветка-плашка за словом; reveal — пословное появление")
    ap.add_argument("--word-flash", action="store_true",
                    help="формат 3: в блоках с полем flash_word поверх спикера "
                         "короткой вспышкой всплывает крупное слово-акцент")
    ap.add_argument("--progress-bar", action="store_true",
                    help="формат 6: тонкая полоса-досмотр t/dur вверху")
    ap.add_argument("--list-counter", action="store_true",
                    help="формат 8: счётчик «N / M» + заголовок пункта из "
                         "разметки блоков (item_no/item_title/items_total)")
    ap.add_argument("--grade", choices=sorted(engine_talk.GRADES),
                    help="формат 9: единый грейд одним ffmpeg-фильтром "
                         "(warm — тёплый; duotone — лёгкий сплит-тон)")
    a = ap.parse_args()

    # Гейт активации (BOX-29): рендер только на активированной клиентской копии.
    # ВТОРАЯ ДЫРА fail-open (закрыта): раньше `except ImportError: pass` означал,
    # что защита снимается удалением activation_gate.py. Теперь отсутствие гейта
    # трактуется по режиму: на клиентской сборке — стоп, у нас — предупреждение.
    try:
        import activation_gate
    except ImportError:
        if _gate_enforced():
            raise SystemExit(
                "\n[активация] РЕНДЕР ОСТАНОВЛЕН: модуля проверки активации нет.\n"
                "Сборка повреждена — переустановите завод.")
        print("[активация] гейт недоступен — рендер не блокируется [мягкий режим]")
    else:
        if not activation_gate.allow("рендер"):
            raise SystemExit(2)

    # demo/compare/music — вход-агностик (без транскрипта/блоков). Остальным
    # форматам транскрипт и блоки по-прежнему обязательны (без --text транскрипт
    # нужен).
    if a.template not in ("demo", "compare", "music"):
        if not a.blocks:
            ap.error("--blocks обязателен для формата %s" % a.template)
        if not a.transcript and not a.text:
            ap.error("нужен --transcript или --text для формата %s" % a.template)

    ov = None
    if a.cta or a.hint:
        ov = {"cta": a.cta, "cta_sub": a.cta_sub, "hint": a.hint}

    cmp_labels = None
    if a.compare_bad or a.compare_good:
        cmp_labels = {}
        if a.compare_bad:
            cmp_labels["bad"] = a.compare_bad
        if a.compare_good:
            cmp_labels["good"] = a.compare_good

    # R9: тройка значений караоке — True (--karaoke), False (--no-karaoke),
    # None (флагов нет -> build() решает по раскладке: говорящим включено).
    karaoke = False if a.no_karaoke else a.karaoke

    # Исходник может ЛЕЖАТЬ НА БОКУ без флага поворота — метаданных, по которым
    # это видно, тогда нет вовсе, и завод соберёт ролик с лежащим спикером молча.
    # Проверка необязательная и никогда не роняет сборку: подсказка, не запрет.
    try:
        import checks as _checks
        _tilt = _checks.detect_tilt(a.video)
        if _tilt:
            print(_checks.tilt_message(_tilt), file=sys.stderr, flush=True)
    except Exception:                                    # noqa: BLE001
        pass

    try:
        meta = build(a.video, a.transcript, a.blocks, a.out, acc=a.acc,
                     template=a.template, providers=tuple(a.providers.split(",")),
                     plan=a.plan, strict=not a.soft, overlays=ov, karaoke=karaoke,
                     karaoke_style=a.karaoke_style, word_flash=a.word_flash,
                     progress_bar=a.progress_bar, list_counter=a.list_counter,
                     grade=a.grade, punch_in=a.punch_in, text=a.text, music=a.music,
                     music_query=a.music_query, music_variants=a.music_variants,
                     video2=a.video2, compare_labels=cmp_labels, pace=a.pace,
                     tighten=a.tighten, tighten_silence=a.tighten_silence,
                     sheet_zones=a.sheet_zones,
                     from_lock=a.from_lock, style=a.style,
                     hook=a.hook, hook_sub=a.hook_sub, bible_path=a.bible_path)
    except SystemExit as exc:                  # отказ конвейера с готовым текстом
        code = exc.code
        if isinstance(code, int):              # чужой код (гейт) не переписываем
            return code
        _say_failed(str(code) if code else "конвейер остановился без объяснения")
        return EXIT_FAIL
    except KeyboardInterrupt:                  # прервал человек — не поломка
        raise
    except Exception as exc:                   # noqa: BLE001 — наружу текст
        _say_failed("%s: %s" % (type(exc).__name__, exc))
        return EXIT_FAIL

    print("\nГОТОВО (НЕ опубликовано — предохранитель):")
    print("  mp4:  ", meta["out"], "(%d МБ)" % (meta["size_bytes"] // 1_000_000))
    print("  лист: ", meta["sheet"])
    print("  вставок:", len(meta["broll"]))
    if meta.get("music"):
        print("  музыка:", meta["music"])
    if meta.get("music_variants"):
        print("  варианты музыки (в mp4 сейчас — первый, #1):")
        for v in meta["music_variants"]:
            print("    #%d %s → %s" % (v["n"], v["file"], v["attribution"]))
    if meta.get("timeline"):
        print("  план: ", meta["timeline"])

    # TAIL-06: файл есть, но проверки его завернули, а падать запретил --soft.
    # Это не провал (ролик собран) и не успех (показывать нельзя) — код 2.
    problems = meta.get("check_problems") or []
    if problems:
        print("\n⚠ РОЛИК СОБРАН, НО ПРОВЕРКИ ПЕРЕД ПОКАЗОМ ЕГО НЕ ПРОПУСТИЛИ "
              "(держит флаг --soft).")
        print("Показывать клиенту в таком виде нельзя — сначала почините то, "
              "что перечислено выше в «ЗАМЕЧАНИЯ ПРОВЕРОК», и пересоберите.")
        return EXIT_SOFT
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
