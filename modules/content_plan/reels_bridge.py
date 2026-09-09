# -*- coding: utf-8 -*-
"""ШОВ К ДВИЖКУ №4 (РАЗГОВОРНЫЙ REELS). Собирает РЕАЛЬНЫЙ черновик говорящего
Reels из созревшего reels-слота существующим движком (render_reels →
talk_transcribe → talk_plan → talk_broll → engine_talk), тем же путём, что ручной
запуск `python render_reels.py ...`, только параметризованный слотом.

ВАЖНО: движок НЕ переписывается. Здесь только оркестрация: взять исходное
говорящее видео из слота, добыть транскрипт (готовый или авто-стадией
talk_transcribe), взять смысловые блоки, вызвать render_reels.build() и записать
готовый reels.mp4 в content_plan/queue/<slot_id>/. Ровно то, что делает человек
в CLI render_reels, но входы берём из слота плана.

ПРЕДОХРАНИТЕЛЬ: тут только ГЕНЕРАЦИЯ черновика. Ничего не публикуется, autopost
--send не зовётся. Перебивки — со СВОБОДНОГО стока Pixabay (video_type=film,
free) или из локального банка; выбор источника — параметр слота. Публикация —
отдельно, через штатный autopost по явному слову Валерии (жёсткий стоп
gate.REAL_PUBLISH_IMPLEMENTED на месте).

ЧЕСТНО ПРО ДЕНЬГИ (изменилось): ОДИН платный вызов здесь возможен — смысловая
разбивка речи моделью (talk_segment, ключ клиента ANTHROPIC_API_KEY). Он идёт
только при двойном согласии: ключ заведён клиентом И включён флаг реальной
сборки CONTENT_PLAN_REAL_GEN. Ключа нет — модель не зовётся ни разу, поведение
прежнее. Никаких других платных API (vidIQ и пр.) мост не трогает.

ТРИ ВХОДА REELS-СЛОТА и как guard на каждый ложится:
  * slot["video"]      — путь к снятому говорящему mp4/mov (ОБЯЗАТЕЛЕН). Нет —
                         не падаем и движок не зовём: needs_input("video").
  * slot["transcript"] — transcript.json (стадия 2, формат engine_talk). Если в
                         слоте есть готовый путь — используем; ЕСЛИ НЕТ — авто-
                         прогоняем talk_transcribe (WhisperX) из video сами. Это
                         НЕ платный API (локальные модели), поэтому делаем молча.
  * slot["blocks"]     — смысловые блоки речи + visual_query (стадия talk_segment,
                         §6 REELS_TALK_ARCH). ЭТО СМЫСЛОВАЯ СЕГМЕНТАЦИЯ = LLM-ШАГ.
                         Готовых блоков в слоте нет → пробуем ПОЛУЧИТЬ ИХ САМИ
                         (auto_blocks ниже). Получилось — слот собирается до
                         конца; нет ключа модели или модель не ответила →
                         needs_input("blocks") с точной причиной, слот остаётся
                         черновиком, остальные слоты плана идут дальше.

────────────────────────────────────────────────────────────────────────────────
АВТО-BLOCKS: ПОДКЛЮЧЕНО (auto_blocks() в этом файле).

    Раньше здесь стоял `return None` с описанием будущего хука: смысловую
    разбивку делал человек в живой сессии Claude, и слот утыкался в guard
    «нет блоков» ВСЕГДА — то есть автосборки рилса не существовало вовсе.
    Теперь ветка «блоков нет» зовёт стадию talk_segment:

        blocks = talk_segment.segment(transcript_path, topic=..., hook=...)

    ВНИМАНИЕ, историческая ловушка: функции `segment` в talk_segment.py тогда НЕ
    БЫЛО (там жил только segment_via_api(transcript_path, model) — сырой текст
    ответа модели, без разбора и валидации), и сигнатуры расходились. Свели так:
    в talk_segment заведена ПУБЛИЧНАЯ segment(transcript_path, topic, hook)
    поверх существующего кода — она и зовёт API, и разбирает ответ, и валидирует
    контракт §6. Альтернативу (звать segment_via_api прямо отсюда) отвергли:
    мосту пришлось бы держать у себя копию _parse_json_blocks + validate_blocks +
    вычисления длительности, и эта копия разъехалась бы с оригиналом при первой
    правке промпта. Мост остаётся ОРКЕСТРАТОРОМ: движок не переписываем.

    ДЕНЬГИ И ПРЕДОХРАНИТЕЛЬ. Ключ ANTHROPIC_API_KEY — клиентский и платный.
    Умолчание НЕ менялось: ключа нет → ни одного обращения к модели, поведение
    ровно прежнее (слот — честный черновик с пометкой «блоки готовит человек»).
    Сам вызов возможен только под флагом реальной сборки CONTENT_PLAN_REAL_GEN
    (executor.real_gen_enabled) — то есть по двойному согласию: клиент завёл ключ
    И завод переведён в режим реальной сборки черновиков.
────────────────────────────────────────────────────────────────────────────────

Движок (render_reels/talk_*/engine_talk) лежит в корне установки (рядом с
CONTRACT.md), не в пакете modules/. Импорт делаем лениво внутри build(), добавив
корень в sys.path — чтобы сам импорт пакета content_plan не тянул cv2/torch/
whisperx, когда реальная генерация выключена (обратная совместимость с dry-run).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import config, paths

# Расширения исходного говорящего видео, которые понимает движок (ffmpeg/cv2).
_VIDEO_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")

# Умолчания сборки. Все переопределяются полями слота (см. build()).
_DEFAULT_TEMPLATE = "split"        # экран пополам: спикер сверху, b-roll снизу
_DEFAULT_ACCENT = "lime"           # акцент субтитров (engine_talk)
_DEFAULT_PROVIDERS = ("pixabay",)  # СВОБОДНЫЙ сток; локальный банк — ("local",)
_DEFAULT_PLAN = True               # планировщик монтажа: доля b-roll ≤ 50%
_DEFAULT_TR_MODEL = "medium"       # размер WhisperX-модели авто-транскрипции
_DEFAULT_TR_LANG = "ru"            # язык речи для авто-транскрипции

# ОБНОВЛЕНО 22.08.2026: формат «СТАНДАРТ» (engine_video.render — хук текстом
# поверх обычного лайфстайл-клипа, БЕЗ говорящей головы) — тот же формат,
# которым владелец всегда собирает свои Reels (см. feedback_reels_format_standart
# в памяти проекта). У клиента он не требует транскрипта/blocks (LLM-шага) —
# только видео и хук, поэтому он единственный путь reels, который реально
# работает СЕГОДНЯ без подключения платной модели сегментации речи.
# Включается ЯВНО: slot["reels_template"] = "standart" (умолчание — прежнее,
# "split"/talk-конвейер, чтобы не менять поведение уже настроенных клиентов).
_STANDART_TEMPLATE = "standart"
_STANDART_NATIVE_SUB_DEFAULT = "в описании ↓"

# ── КЛИЕНТСКАЯ РАЗДАЧА ФОРМАТОВ (решение владельца 27.08.2026) ────────────────
# В работе оставлены РОВНО ТРИ формата: СТАНДАРТ, СУБТИТРЫ, ПОЛОВИНКИ. Остальные
# клиенту не выдаются «ни при каких условиях» — в том числе через поле слота
# reels_template, которое до сих пор уходило в render_reels.build как есть:
# плану достаточно было написать "pip", и снятый формат собирался в обход
# витрины. Это последняя дверь на витрину со стороны контент-плана.
#
# ЕДИНСТВЕННЫЙ ИСТОЧНИК ПРАВДЫ — reels_format.TEMPLATES_SHIPPED (корень завода
# разработчика). Импортировать его ОТСЮДА нельзя: этот модуль едет клиенту в
# коробке, а reels_format.py — дев-инструмент и в поставку не входит (аудит
# build_box.box_import_gaps уронил бы сборку). Поэтому здесь лежит КОПИЯ, а
# расхождение ловит на стороне разработчика reels_format.client_shipping_drift()
# — она читает именно это имя разбором исходника. Переименуете список —
# проверка скажет об этом вслух, а не промолчит.
#
# Значения тут двух природ, и это не небрежность: "standart" — id шаблона со
# своим движком (engine_video.py, раскладки у него нет вовсе), "split" и
# "card" — имена РАСКЛАДОК talk-конвейера.
# 28.08.2026 владелец развёл СУБТИТРЫ и ПОЛОВИНКИ по ГЛУБИНЕ вставки:
#   ПОЛОВИНКИ  -> split: вставка в нижней половине, спикер сверху всегда;
#   СУБТИТРЫ   -> card:  вставка перекрывает кадр целиком, спикера не видно.
# До этого оба ехали на split и отличались только кеглем подписи — при
# требовании «перебивок не меньше четырёх» картинка у них выходила одинаковая.
_TEMPLATES_SHIPPED = ("standart", "subs-plain", "split", "card")

#: ЧЕТВЁРТЫЙ ФОРМАТ ложится на ту же раскладку split, но БЕЗ единой вставки —
#: различает их только пресет (pace.max_broll=0). Поэтому у него собственное имя
#: в плане, а движку передаётся раскладка. Без этого «СУБТИТРЫ СТАНДАРТ» и
#: «ПОЛОВИНКИ» были бы для слота одним и тем же словом.
_TEMPLATE_ALIASES = {"subs-plain": "split"}

#: ПРЕСЕТ ОФОРМЛЕНИЯ НА КАЖДЫЙ ФОРМАТ (решение владельца 02.09.2026).
#: Без него клиент получал ДЕФОЛТ движка: подпись мельче, не по центру,
#: без белого свечения, строчными и с запятыми — то есть совсем не то,
#: что владелец утвердил на показе. Мост эти файлы не читает сам:
#: он передаёт путь в render_reels.build(style=...), а разбирает style.py.
_STYLE_BY_TEMPLATE = {
    "subs-plain": "styles/fmt_subs_plain.json",  # СУБТИТРЫ СТАНДАРТ (без врезок)
    "split":      "styles/fmt_subs2.json",       # ПОЛОВИНКИ
    "card":       "styles/fmt_subs1.json",       # СУБТИТРЫ С ПЕРЕБИВКОЙ
}

# Как разработчику посмотреть всё: одна переменная окружения на весь проект
# (та же, что читают reels_format.py и панель). По умолчанию — то есть у
# клиента — закрыто.
_DEV_ENV = "REELS_FORMAT_DEV"
_DEV_ON = ("1", "true", "yes", "on", "да")


def _dev_mode() -> bool:
    """Режим разработчика включён? По умолчанию НЕТ (клиентский режим)."""
    return str(os.environ.get(_DEV_ENV, "")).strip().lower() in _DEV_ON


def _shipping_refusal(template: str):
    """Отказ по формату, снятому с раздачи, или None, если формат разрешён.

    Возвращает готовое поле needs_input: слот остаётся черновиком с точной
    причиной — тем же способом, каким мост отвечает на «нет видео» и «нет
    блоков». Тихо подменять формат на разрешённый НЕЛЬЗЯ: человек увидел бы в
    плане одно, а в очереди другое.
    """
    if _dev_mode() or template in _TEMPLATES_SHIPPED:
        return None
    return {"ok": False,
            "needs_input": "reels_template: формат «%s» клиенту не выдаётся. "
                           "Решением владельца 27.08.2026 в работе оставлены "
                           "три формата: СТАНДАРТ, СУБТИТРЫ, ПОЛОВИНКИ. "
                           "Допустимые значения поля: %s («split» — ПОЛОВИНКИ, "
                           "вставка снизу; «card» — СУБТИТРЫ, вставка на весь "
                           "кадр). Уберите поле — соберётся умолчание «%s»."
                           % (template, ", ".join(_TEMPLATES_SHIPPED),
                              _DEFAULT_TEMPLATE)}


def _ensure_engine_on_path() -> None:
    """Корень установки (с движком) — в sys.path, лениво и без дублей."""
    root = str(config.install_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _abs_under_root(p: str) -> Path:
    path = Path(p)
    if not path.is_absolute():
        path = config.install_root() / path
    return path


def resolve_video(slot: dict):
    """Абсолютный путь к исходному говорящему видео слота или None.

    Приоритет: slot["video"] (относительный → от корня установки). Проверяем,
    что файл существует и имеет видео-расширение. Пусто/нет файла → None
    (сработает guard needs_input("video"))."""
    listed = slot.get("video")
    if not listed:
        return None
    path = _abs_under_root(str(listed))
    if path.is_file() and path.suffix.lower() in _VIDEO_EXT:
        return str(path)
    return None


def resolve_transcript(slot: dict, video: str, out_dir: Path) -> str:
    """Путь к transcript.json (формат engine_talk: голый список сегментов).

    1. slot["transcript"] — готовый путь (относительный → от корня). Есть файл →
       используем как есть (стадию 2 уже прогнали руками/раньше).
    2. Иначе — АВТО-ПРОГОН стадии 2 (talk_transcribe, WhisperX) из video в
       out_dir/"transcript.json". Это локальные модели, НЕ платный API.

    Размер модели и язык берём из слота (transcribe_model / language) с
    дефолтами _DEFAULT_TR_MODEL/_DEFAULT_TR_LANG."""
    given = slot.get("transcript")
    if given:
        p = _abs_under_root(str(given))
        if p.is_file():
            return str(p)
        # путь указан, но файла нет — не молчим: это ошибка входа, а не «нет входа».
        raise FileNotFoundError("slot['transcript'] указывает на отсутствующий файл: %s" % p)

    _ensure_engine_on_path()
    import talk_transcribe  # тяжёлый (whisperx/torch) — только когда реально нужен

    out_path = str(out_dir / "transcript.json")
    model_size = str(slot.get("transcribe_model") or _DEFAULT_TR_MODEL)
    language = str(slot.get("language") or _DEFAULT_TR_LANG)
    path, _segs = talk_transcribe.transcribe(video, out_path=out_path,
                                             model_size=model_size, language=language)
    return path


#: Блоков нет и ключа модели нет — ПРЕЖНЕЕ поведение завода, не поломка.
#: Формулировка для клиента: что именно не произошло, кто это делает вместо
#: машины и чем это включается. Про платность говорим прямо — деньги его.
_BLOCKS_MANUAL = (
    "blocks: у reels-слота нет смысловых блоков (blocks.json со списком "
    "{t0,t1,intent,visual_query}), а ключ модели (%s) не заведён — значит "
    "авторазбивку речи завод не делает и в платную модель не ходит. Разбивку "
    "делает человек в живой сессии Claude: python talk_segment.py <transcript> "
    "--print-prompt, ответ сохранить в файл, затем --from-answer <файл>. Чтобы "
    "ролики собирались сами, заполните ключ при установке (услуга платная, "
    "оплачивается вами)." % "ANTHROPIC_API_KEY")


def auto_blocks(slot: dict, transcript: str, out_dir: Path):
    """АВТОРАЗБИВКА речи на смысловые блоки стадией talk_segment.

    Возвращает пару:
      (путь_к_blocks.json, None) — блоки получены, слот собирается дальше;
      (None, причина)            — блоков нет; причина уходит в needs_input.

    Три случая и ровно три исхода — они же проверяются тестами:
      * ключа модели нет      → (None, _BLOCKS_MANUAL). Сети не касаемся вовсе:
        api_key() читает окружение и .env, до клиента anthropic дело не доходит.
        Это ПРЕЖНЕЕ поведение завода — ничего не ухудшилось;
      * ключ есть, модель ответила блоками → пишем out_dir/blocks.json;
      * ключ есть, но обращение не удалось (нет сети, кончилась квота, ответ не
        разбирается) → (None, причина с текстом ошибки). НЕ поднимаем исключение
        наверх: иначе исполнитель пометил бы слот FAILED, а один неудачный вызов
        модели не повод ронять прогон плана — остальные слоты идут дальше.

    Ловим (Exception, SystemExit): движковые модули завода привыкли выходить
    через SystemExit с внятным текстом, а он мимо except Exception. KeyboardInterrupt
    и прочий BaseException НЕ ловим — прерывание человеком должно прерывать."""
    _ensure_engine_on_path()
    try:
        import talk_segment  # лёгкий: anthropic грузится уже внутри вызова
    except Exception as ex:
        return None, ("blocks: стадия смысловой разбивки talk_segment не "
                      "загружается (%s). Разговорный Reels собрать нечем — "
                      "покажите эту строку человеку." % str(ex)[:200])

    if not talk_segment.api_key():
        return None, _BLOCKS_MANUAL

    warnings: list = []
    try:
        blocks = talk_segment.segment(transcript, topic=slot.get("topic"),
                                      hook=slot.get("hook"), warnings=warnings)
    except (Exception, SystemExit) as ex:
        return None, ("blocks: авторазбивка речи не удалась — модель не дала "
                      "пригодных блоков (%s: %s). Слот остался черновиком, "
                      "остальные слоты плана собираются дальше. Обычные причины: "
                      "нет интернета, исчерпан лимит оплаченного, ключ отозван. "
                      "Можно разбить речь руками: talk_segment.py --print-prompt "
                      "→ --from-answer." % (type(ex).__name__, str(ex)[:200]))

    if not blocks:
        return None, ("blocks: авторазбивка речи вернула пустой список блоков — "
                      "нарезать ролик не по чему. Слот остался черновиком.")

    out = out_dir / "blocks.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump({"blocks": blocks, "source": "talk_segment (авто)",
                   "warnings": warnings}, fh, ensure_ascii=False, indent=1)
    return str(out), None


def resolve_blocks(slot: dict, out_dir: Path, transcript: str = None):
    """Пара (путь_к_blocks.json, причина_отсутствия). Ровно одно из двух не None.

    Блоки принимаем в трёх видах:
      * slot["blocks"] / slot["blocks_path"] — строка-путь к готовому blocks.json
        (относительный → от корня установки);
      * slot["blocks"] — dict вида {"blocks":[...]} → пишем в out_dir/"blocks.json";
      * slot["blocks"] — список блоков → оборачиваем в {"blocks":[...]} и пишем.

    Готовых блоков в слоте нет → auto_blocks(): авторазбивка стадией talk_segment,
    если у клиента заведён ключ модели. Транскрипт для неё обязателен (это её
    единственный вход) — без него честно отвечаем «блоков нет»."""
    raw = slot.get("blocks")
    if raw is None:
        raw = slot.get("blocks_path")

    # 1. путь к готовому файлу
    if isinstance(raw, str) and raw.strip():
        p = _abs_under_root(raw.strip())
        if p.is_file():
            return str(p), None
        raise FileNotFoundError("slot['blocks'] указывает на отсутствующий файл: %s" % p)

    # 2. inline dict {"blocks":[...]} или 3. голый список блоков
    blocks_list = None
    if isinstance(raw, dict) and isinstance(raw.get("blocks"), list):
        blocks_list = raw["blocks"]
    elif isinstance(raw, list):
        blocks_list = raw
    if blocks_list:
        out = out_dir / "blocks.json"
        with out.open("w", encoding="utf-8") as fh:
            json.dump({"blocks": blocks_list}, fh, ensure_ascii=False, indent=1)
        return str(out), None

    # 4. блоков в слоте нет — авторазбивка (см. auto_blocks и докстринг модуля).
    if not transcript:
        return None, ("blocks: у reels-слота нет смысловых блоков, а транскрипт "
                      "речи не получен — авторазбивать нечего.")
    return auto_blocks(slot, transcript, out_dir)


def _build_standart(slot: dict, video: str, out_dir: Path) -> dict:
    """Формат «СТАНДАРТ»: хук текстом поверх обычного клипа, без разбора речи.

    Не требует transcript/blocks — движок engine_video.render() сам вырезает
    кусок клипа, кладёт хук и (опционально) native_sub-подсказку. Guard'ы этой
    ветки — те же по духу, что у остальных: нет хука → needs_input, движок
    сам поднимет ValueError на слишком коротком клипе (не глотаем)."""
    hook = str(slot.get("hook") or slot.get("topic") or "").strip()
    if not hook:
        return {"ok": False,
                "needs_input": "hook: формату «СТАНДАРТ» нужен текст хука "
                               "(slot['hook'] или slot['topic']) — движок "
                               "накладывает его поверх клипа."}

    _ensure_engine_on_path()
    import engine_video
    import engine4 as e
    import checks

    accent_name = str(slot.get("accent") or _DEFAULT_ACCENT)
    acc = e.ACCENTS.get(accent_name) or list(e.ACCENTS.values())[0]
    native_sub = slot.get("native_sub", _STANDART_NATIVE_SUB_DEFAULT)
    # Момент старта не задан — пусть движок решит сам (engine_video.auto_start):
    # у длинного клипа он пропустит голову, где человек ещё устраивается перед
    # камерой. Раньше здесь стоял жёсткий 0.0, и авто-правило не включалось
    # никогда. Явное значение в слоте по-прежнему главнее.
    start = slot.get("start")
    start = float(start) if start is not None else None
    out_mp4 = str(out_dir / "reels.mp4")

    meta = engine_video.render(video, hook, acc, out_mp4, start=start,
                               dur=slot.get("dur"), native_sub=native_sub)
    verify_problems = checks.verify_reel(meta, strict=False)

    media = {
        "kind": "reels",
        "dir": slot["id"],
        "video": os.path.basename(out_mp4),
        "template": _STANDART_TEMPLATE,
        "accent": accent_name,
        "duration_sec": round(float(meta.get("dur", 0.0)), 2),
        "verify_problems": verify_problems,
    }
    return {"ok": True, "media": media}


def build(slot: dict) -> dict:
    """Собирает РЕАЛЬНЫЙ черновик говорящего Reels в content_plan/queue/<slot_id>/.

    Возвращает результат-словарь того же формата, что carousel_bridge.build:
      {"ok": True,  "media": {...}}                — собрали настоящий reels.mp4;
      {"ok": False, "needs_input": "video: ..."}   — нет исходного видео → dry-run;
      {"ok": False, "needs_input": "blocks: ..."}  — блоков нет и авторазбивка
                                                     недоступна (нет ключа модели)
                                                     либо не удалась (сеть/квота).

    Guard — ТОЛЬКО для отсутствия входов, не для поломок движка. Исключения движка
    НЕ глотаем: их поймает исполнитель и пометит слот FAILED (как у каруселей).
    Единственное исключение — SystemExit движка «ни одной перебивки не набралось»
    (сеть/сток пусты): его переводим в needs_input, чтобы не ронять весь прогон
    планировщика (SystemExit не ловится общим except Exception исполнителя)."""
    slot_id = slot["id"]

    # Guard №0: РАЗДАЧА. Стоит ПЕРВЫМ и до всякой работы: формат, снятый с
    # раздачи, не должен ни собираться, ни даже добираться до транскрипции —
    # человек получит отказ раньше, чем машина потратит минуты.
    template = str(slot.get("reels_template") or _DEFAULT_TEMPLATE).lower()
    отказ = _shipping_refusal(template)
    if отказ:
        return отказ

    # Guard №1: нет исходного говорящего видео — движку нечего разбирать.
    video = resolve_video(slot)
    if not video:
        return {"ok": False,
                "needs_input": "video: у reels-слота нет исходного говорящего "
                               "mp4/mov (slot['video']). Это снятый спикером ролик — "
                               "без него разговорный Reels собрать нельзя."}

    out_dir = paths.queue_dir() / slot_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Формат «СТАНДАРТ» — отдельная, более простая ветка без transcript/blocks
    # (см. _build_standart). Включается явно slot["reels_template"]="standart".
    if template == _STANDART_TEMPLATE:
        return _build_standart(slot, video, out_dir)

    # Транскрипт: готовый из слота ИЛИ авто-прогон talk_transcribe (локальные модели).
    transcript = resolve_transcript(slot, video, out_dir)

    # Блоки: готовые из слота ИЛИ авторазбивка talk_segment (нужен ключ модели).
    # Нет блоков — не падаем: слот остаётся черновиком с точной причиной.
    blocks_path, blocks_why = resolve_blocks(slot, out_dir, transcript=transcript)
    if blocks_path is None:
        return {"ok": False, "needs_input": blocks_why}

    _ensure_engine_on_path()
    import render_reels
    import checks

    # Параметры сборки — из слота, с производственными дефолтами. Формат уже
    # прочитан и проверен Guard №0 выше (переменная template): второй раз из
    # слота его не читаем — иначе мимо проверки раздачи прошёл бы «Split» с
    # заглавной, и вся дверь закрывалась бы регистром буквы.
    accent = str(slot.get("accent") or _DEFAULT_ACCENT)
    providers = slot.get("broll_providers") or _DEFAULT_PROVIDERS
    if isinstance(providers, str):
        providers = tuple(p.strip() for p in providers.split(",") if p.strip())
    else:
        providers = tuple(providers)
    plan = bool(slot.get("reels_plan", _DEFAULT_PLAN))

    out_mp4 = str(out_dir / "reels.mp4")

    # РЕАЛЬНЫЙ вызов оркестратора (та же функция, что и CLI render_reels.py).
    # strict=False: черновик уходит человеку в окно-вето, а не в публикацию —
    # замечания проверок фиксируем, но сборку не роняем (как carousel checks.verify).
    try:
        # Пресет формата: если файл доехал патчем — берём его, иначе честно
        # собираем дефолтом движка (старое поведение), а не падаем.
        _style = _STYLE_BY_TEMPLATE.get(template)
        if _style:
            _sp = config.install_root() / _style
            _style = str(_sp) if _sp.exists() else None
            if _style is None:
                print("  ! пресет %s не найден — собираю дефолтом движка; "
                      "вид подписи будет отличаться от утверждённого"
                      % _STYLE_BY_TEMPLATE.get(template))
        # Сколько перебивок хочет клиент: сначала слот, потом общая
        # настройка завода, иначе — значение пресета. Число живёт в
        # config/factory.config.json, его пишет помощник по фразе клиента.
        _cnt = slot.get("broll_count")
        if _cnt is None:
            try:
                _cnt = config.config_value("broll_per_reel")
            except Exception:                                # noqa: BLE001
                _cnt = None
        try:
            _cnt = int(_cnt) if _cnt else None
        except (TypeError, ValueError):
            _cnt = None
        # Имя формата из плана -> раскладка движка. Для трёх из четырёх
        # они совпадают, у subs-plain различаются (см. _TEMPLATE_ALIASES).
        _engine_tpl = _TEMPLATE_ALIASES.get(template, template)
        meta = render_reels.build(video, transcript, blocks_path, out_mp4,
                                  acc=accent, template=_engine_tpl,
                                  providers=providers, plan=plan, strict=False,
                                  style=_style, broll_count=_cnt)
    except SystemExit as ex:
        # Движок бросает SystemExit, когда не набралось НИ ОДНОЙ перебивки
        # (пустой сток/нет сети). Это по сути «нет входа b-roll»: не роняем прогон,
        # оставляем слот на dry-run с понятной пометкой.
        return {"ok": False,
                "needs_input": "b-roll: не набралось ни одной перебивки со стоков "
                               "(%s) — проверь сеть/запросы или укажи локальный банк "
                               "(slot['broll_providers']=['local']). Движок: %s"
                               % (", ".join(providers), ex)}

    # Проверки перед показом — как у каруселей: строгими не делаем, замечания
    # кладём в артефакт для окна-вето (пусто = проверки пройдены).
    verify_problems = checks.verify_talk(meta, strict=False)

    media = {
        "kind": "reels",
        "dir": slot_id,                         # относительно content_plan/queue/
        "video": os.path.basename(out_mp4),     # reels.mp4
        "template": meta.get("template"),
        "accent": accent,
        "duration_sec": round(float(meta.get("dur", 0.0)), 2),
        "size_mb": round(meta.get("size_bytes", 0) / 1_000_000, 2),
        "broll_count": len(meta.get("broll", []) or []),
        "providers": list(providers),
        "transcript": ("slot" if slot.get("transcript") else "auto (talk_transcribe)"),
        # Откуда взялись смысловые блоки — это видно человеку в окне-вето:
        # авторазбивку модели он смотрит внимательнее, чем свою же ручную.
        "blocks": ("slot" if (slot.get("blocks") or slot.get("blocks_path"))
                   else "auto (talk_segment)"),
        "sheet": os.path.basename(meta["sheet"]) if meta.get("sheet") else None,
        "verify_problems": verify_problems,     # пусто = проверки talk пройдены
    }
    return {"ok": True, "media": media}
