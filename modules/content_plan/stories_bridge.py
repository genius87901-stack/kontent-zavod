# -*- coding: utf-8 -*-
"""ШОВ К ДВИЖКУ СТОРИС. Собирает РЕАЛЬНЫЙ кадр сторис 1080×1920 из созревшего
слота плана существующим движком — так же, как carousel_bridge собирает слайды,
а reels_bridge — говорящий ролик.

ЗАЧЕМ ПОЯВИЛСЯ (28.08.2026). Планировщик умел ставить слоты формата `stories`
с самого начала, но исполнитель на них отвечал жёстко зашитой строкой «движка
сторис в заводе нет», и то же самое повторял клиенту доктор. Это была НЕПРАВДА:
движок сторис в заводе есть — `modules/story_publisher/story_builder.py`, он
собирает кадр 1080×1920 и им же пользуется CLI сторис. Не было ровно одного —
шва между планом и этим движком. Слоты сторис планировались и молча не
собирались: клиент видел их строчками плана и снимал сторис руками.

Тот же класс ошибки, что уже стоил денег дважды: наши собственные заметки в коде
объявляли возможность отсутствующей, никто не сверялся с источником. Источник —
соседний модуль, лежащий в той же коробке.

ВХОД НЕОБЯЗАТЕЛЕН, И ЭТО НЕ ПОБЛАЖКА. У каруселей нет фото — нет и черновика
(guard). У сторис не так: `story_builder` штатно умеет синтетический тёмный фон
и отдаёт валидный кадр без единого входного фото. Поэтому фото здесь — не
условие сборки, а улучшение: есть материал — ставим его фоном, нет — кадр всё
равно соберётся. Возвращать needs_input из-за отсутствия фото значило бы
выдумать ограничение, которого у движка нет.

ДВИЖОК НЕ ПЕРЕПИСЫВАЕТСЯ. Здесь только оркестрация: взять смыслы слота, выбрать
фон, позвать build_story, положить результат в очередь. Сам build_story внутри
зовёт engine_photo.cta — тот же движок слайдов, что и у каруселей.

ДВЕ ОСИ СТОРИС (28.08.2026). История бывает не только одиночным кадром:
  * СЕРИЯ («карусель в сторис») — несколько кадров подряд. Листаемой карусели
    внутри ОДНОЙ истории у площадок нет вовсе, и серия кадров — её штатный
    эквивалент. Включается `slot["story_frames"] = N`; смысл слота растягивается
    на N пунктов той же функцией `_split_points`, что и слайды карусели, а фото
    берутся по одному на кадр из того же разрешения фото.
  * ВИДЕО («рилс в сторис») — готовый вертикальный ролик 1080×1920. Размер у
    рилса и у сторис СОВПАДАЕТ, перекладывать нечего. Включается
    `slot["story_video"] = <путь>`. Путь спрашиваем ЯВНО и не угадываем:
    `slot["video"]` у reels-слота — это СЫРОЙ материал со спикером, а не готовый
    ролик, и выложить его историей значило бы опубликовать исходник.

ССЫЛКА НА ГОТОВЫЙ РИЛС ВМЕСТО РУЧНОГО ПУТИ (02.09.2026). Владелец описал беду
так: «сторис берёт уже опубликованный рилс и репостит его историей, а выходит
статичная картинка». Разбор показал, что репоста в заводе нет вовсе, а есть одно
поле `story_video`, которого НЕ ЗАПОЛНЯЛ НИКТО: ни планировщик, ни исполнитель,
ни витрина. Человек должен был вписать абсолютный путь руками, а если забывал —
мост тихо собирал ОБЫЧНЫЙ кадр, ставил ему фоном первое фото слота и отчитывался
успехом. Именно этот кадр и уезжал в ВК «обложкой видео».

Поэтому теперь у ветки видео два входа, и оба честные:
  * `slot["story_from_reels"] = "<id рилс-слота>"` — сказать НЕ ПУТЬ, а слот.
    Готовый ролик завода всегда лежит по одному адресу —
    content_plan/queue/<slot_id>/reels.mp4 (его туда кладёт reels_bridge,
    out_mp4), значит путь выводится однозначно и спрашивать его у человека
    незачем. `True` / `"self"` — взять ролик СВОЕГО же слота.
  * `slot["story_video"]` — как было: путь. Дополнительно принимает голый
    идентификатор слота (нет разделителей пути и нет расширения видео) и
    разворачивает его в тот же queue/<id>/reels.mp4.

И ГЛАВНОЕ — ТИХОЙ ПОДМЕНЫ БОЛЬШЕ НЕТ. Если из слота ВИДНО, что человек хотел
видео (заполнено любое из двух полей), а готового ролика нет — мост возвращает
needs_input с объяснением и НЕ собирает статичный кадр. Раньше пропавший путь
летел ValueError'ом (слот падал в FAILED без объяснения), а незаполненное поле не
замечалось вовсе. Формат отказа — общий с соседями (reels_bridge): ok=False +
needs_input. Слот, который И ЗАДУМАН картинкой (одиночный кадр, серия), обоих
полей не имеет и работает ровно как раньше — это основной путь.

ПРЕДОХРАНИТЕЛЬ: тут только ГЕНЕРАЦИЯ черновика. Ничего не публикуется, --send не
зовётся, платные API не трогаются. Публикация сторис — отдельный шаг
(story_publisher), и она по умолчанию тоже пробная.
"""
from __future__ import annotations

from . import paths
from .carousel_bridge import resolve_photos, _first_sentence, _split_points

# Подпись под хуком, когда у слота нет своего смысла. Та же по смыслу заглушка,
# что `cta_sub` у каруселей: сторис без второй строки выглядит оборванной.
_DEFAULT_SUB = "Если откликнулось — напиши в личку."


def _load_builder():
    """Вернуть функцию build_story соседнего модуля или None, если его нет.

    Импорт ОПЦИОНАЛЬНЫЙ и ленивый — ровно по той же причине, что у
    talk_broll._load_bench(): модуль сторис ставится не в каждую копию (коробка
    старой сборки, ветка без story_publisher), а жёсткий импорт наверху файла
    ронял бы весь content_plan там, где сторис не нужны вовсе. Нет модуля —
    честный needs_input, а не падение прогона плана.

    Оба модуля лежат рядом в modules/, поэтому имя абсолютное: content_plan
    зовётся с modules/ на PYTHONPATH (см. клиентский CLAUDE.md), значит и
    story_publisher оттуда виден.
    """
    try:
        from story_publisher.story_builder import build_story
    except Exception:
        return None
    return build_story


def _background_for(slot):
    """Путь к фон-кадру или None (тогда движок нарисует синтетический фон).

    Переиспользуем разрешение фото каруселей (slot.photos → slot.photos_dir →
    каталог из конфига → банк движка) — заводить второй, свой порядок поиска
    тех же самых фото значило бы развести две правды об одном.
    """
    photos = resolve_photos(slot)
    return photos[0] if photos else None


def build(slot: dict) -> dict:
    """Собирает РЕАЛЬНЫЙ кадр сторис в content_plan/queue/<slot_id>/story.png.

    Возвращает результат-словарь того же формата, что carousel_bridge.build:
      {"ok": True,  "media": {...}}              — собрали настоящий кадр;
      {"ok": False, "needs_input": "смыслы: …"}  — слоту нечего показывать;
      {"ok": False, "needs_input": "модуль: …"}  — в этой копии нет story_publisher.

    Исключения движка НЕ глотаем: их поймает исполнитель и пометит слот FAILED —
    ровно как у каруселей и reels. Guard — только для отсутствия входов.
    """
    slot_id = slot["id"]
    topic = (slot.get("topic") or "").strip()
    hook = (slot.get("hook") or "").strip()
    meaning = (slot.get("meaning") or "").strip()

    # Guard №1: нет смыслов — на кадре нечего писать. Сторис без заголовка
    # build_story и сам не соберёт (бросает ValueError), но объяснить причину
    # человеку лучше здесь: он читает план, а не трассировку.
    if not (hook or topic):
        return {"ok": False,
                "needs_input": "смыслы: у сторис-слота нет ни хука, ни темы — "
                               "на кадре нечего писать"}

    # Guard №2: модуль сторис в этой копии не установлен.
    build_story = _load_builder()
    if build_story is None:
        return {"ok": False,
                "needs_input": "модуль: в этой копии завода нет story_publisher — "
                               "кадр сторис собирать нечем. Обновите завод до "
                               "версии со сторис."}

    out_dir = paths.queue_dir() / slot_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # ВЕТКА «РИЛС В СТОРИС»: готовый вертикальный ролик берём как есть.
    # Отказ (refusal) отдаём ДО сборки кадра: если человек просил видео, а
    # ролика нет, статичная картинка вместо него — та самая тихая подмена,
    # из-за которой в ВК уезжала «обложка».
    video, refusal = _resolve_video(slot)
    if refusal is not None:
        return refusal
    if video is not None:
        return _video_media(video, slot_id)

    background = _background_for(slot)

    # ВЕТКА «КАРУСЕЛЬ В СТОРИС»: серия кадров подряд.
    frames = _frames_count(slot)
    if frames > 1:
        return _series_media(slot, build_story, out_dir, slot_id, frames,
                             hook or topic, meaning)

    out_path = out_dir / "story.png"

    # Кадр сторис короткий: крупный хук + одна строка смысла + плашка кодового
    # слова. Второй строкой берём ПЕРВОЕ предложение смысла (той же функцией,
    # что обложка карусели), а не весь абзац: на 1080×1920 абзац не читается.
    info = build_story(
        hook=hook or topic,
        text=_first_sentence(meaning) or _DEFAULT_SUB,
        code=(slot.get("code") or "").strip() or None,
        background=background,
        out=str(out_path),
    )

    media = {
        # `kind` читает gate._artifact_target, когда собирает команду выкладки.
        # Без него сторис уехала бы командой обычного поста в ленту — то есть
        # человеку сказали бы опубликовать историю как пост.
        "kind": "story",
        "dir": slot_id,                   # относительно content_plan/queue/
        "story_kind": "single",
        "file": "story.png",
        "width": info["width"],
        "height": info["height"],
        "engine": info["engine"],         # engine_photo | fallback
        "background": info["background"],  # material | synthetic
        "photo_used": background,          # None = фон синтетический
        "code": info["code"],
    }
    return {"ok": True, "media": media}


#: Сколько кадров максимум ставим в одну серию. Восемь историй подряд человек
#: ещё досматривает; дальше он их пролистывает, и смысл серии теряется. То же
#: число, что слайдов в карусели завода (_N в carousel_bridge).
_MAX_FRAMES = 8

_VIDEO_EXT = (".mp4", ".mov")


def _frames_count(slot):
    """Сколько кадров просит слот. Мусор и отрицательные числа → 1."""
    try:
        n = int(slot.get("story_frames") or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(n, _MAX_FRAMES))


#: Имя готового ролика в очереди плана. Один адрес на весь завод: его пишет
#: reels_bridge (out_mp4 = queue/<slot_id>/reels.mp4), его же читает gate,
#: собирая команду выкладки. Заводить здесь второе имя значило бы развести две
#: правды об одном файле.
_REELS_FILE = "reels.mp4"


def _looks_like_slot_id(raw: str) -> bool:
    """Похоже ли значение на ИДЕНТИФИКАТОР СЛОТА, а не на путь.

    Идентификатор слота в плане — короткое имя без разделителей пути и без
    расширения видео (`r1`, `s3`, `2026-09-02-reels`). Всё остальное — путь.
    Граница нарочно грубая и в пользу пути: ошибиться в эту сторону безопаснее,
    потому что несуществующий путь мы всё равно назовём вслух."""
    if not raw or raw.lower().endswith(_VIDEO_EXT):
        return False
    return not any(sep in raw for sep in ("/", "\\", ":"))


def _reels_of_slot(slot_id: str):
    """Путь к готовому ролику слота `slot_id` в очереди плана."""
    return paths.queue_dir() / slot_id / _REELS_FILE


def _no_reels(where: str, ref_id: str, path) -> dict:
    """Отказ «человек просил видео, а ролика нет».

    Человеческое объяснение, а не трассировка: клиент читает план, и ему нужно
    знать ЧТО сделать — собрать рилс-слот или убрать поле. Молча собрать вместо
    ролика картинку (как было до 02.09.2026) нельзя: именно так в ВК уезжала
    статичная обложка вместо видео."""
    return {"ok": False,
            "needs_input": ("%s: слот просит выложить историей готовый рилс "
                            "слота «%s», но ролика ещё нет: %s. Сначала соберите "
                            "рилс этого слота (он ляжет в очередь сам), потом "
                            "собирайте сторис. Если история задумана КАРТИНКОЙ "
                            "— уберите из слота поле %s."
                            % (where, ref_id, path, where))}


def _resolve_video(slot):
    """(путь_к_ролику | None, отказ | None) — ветка «рилс в сторис».

    Возвращает ПАРУ, а не только путь, потому что «видео не нашлось» — это
    теперь не исключение и не молчание, а честный needs_input того же формата,
    что у reels_bridge.

    Два входа (см. шапку модуля):
      * `story_from_reels` — идентификатор рилс-слота (или True/"self" = свой
        же слот); путь выводится однозначно: queue/<id>/reels.mp4;
      * `story_video` — путь (как было) либо голый идентификатор слота.

    Угадывать по-прежнему нельзя: `slot["video"]` у reels-слота — сырой материал
    со спикером, и подстановка его сюда выложила бы историей исходник. Поэтому
    сигналом «хочу видео» считаем ТОЛЬКО эти два поля.
    """
    from pathlib import Path

    from . import config

    # 1) Ссылка на слот с готовым рилсом.
    ref = slot.get("story_from_reels")
    if ref is not None and ref is not False and str(ref).strip() != "":
        ref_id = str(ref).strip()
        # True / "self" / "сам" — взять ролик своего же слота.
        if ref is True or ref_id.lower() in ("self", "true", "сам", "свой"):
            ref_id = slot["id"]
        p = _reels_of_slot(ref_id)
        if not p.is_file():
            return None, _no_reels("story_from_reels", ref_id, p)
        return p, None

    # 2) Явное поле пути (оно же принимает идентификатор слота).
    raw = (slot.get("story_video") or "").strip()
    if not raw:
        return None, None

    if _looks_like_slot_id(raw):
        p = _reels_of_slot(raw)
        if not p.is_file():
            return None, _no_reels("story_video", raw, p)
        return p, None

    p = Path(raw)
    if not p.is_absolute():
        p = Path(config.install_root()) / p
    if not p.exists() or not p.is_file():
        return None, {"ok": False,
                      "needs_input": ("story_video: слот просит выложить "
                                      "историей ролик, но файла нет: %s. "
                                      "Проверьте путь или уберите поле, если "
                                      "история задумана картинкой." % p)}
    if p.suffix.lower() not in _VIDEO_EXT:
        return None, {"ok": False,
                      "needs_input": ("story_video: это не видео (%s): %s. "
                                      "Историей-видео уходит только готовый "
                                      "вертикальный ролик .mp4/.mov."
                                      % (p.suffix, p))}
    return p, None


def _video_media(video, slot_id):
    """Результат-словарь для истории-видео. Ролик НЕ копируем и НЕ пережимаем:
    он уже готов, а лишняя перекодировка только испортила бы качество."""
    size = None
    try:
        import cv2
        cap = cv2.VideoCapture(str(video))
        if cap.isOpened():
            size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        cap.release()
    except Exception:
        size = None

    media = {
        "kind": "story",
        "dir": slot_id,
        "file": str(video),
        "story_kind": "video",
        "width": size[0] if size else None,
        "height": size[1] if size else None,
        "engine": "готовый ролик (не пересобирался)",
    }
    # Вертикальность проверяем и ГОВОРИМ вслух, но не запрещаем: обрезать чужой
    # ролик молча хуже, чем предупредить. Решение — за человеком.
    if size and size[0] > size[1]:
        media["warning"] = ("ролик ГОРИЗОНТАЛЬНЫЙ (%d×%d) — история его обрежет "
                            "по бокам. Для сторис нужен вертикальный 1080×1920."
                            % size)
    return {"ok": True, "media": media}


def _series_media(slot, build_story, out_dir, slot_id, frames, hook, meaning):
    """Серия кадров подряд: по одному пункту смысла и по одному фото на кадр."""
    points = _split_points(meaning, frames, fallback=_DEFAULT_SUB)
    photos = resolve_photos(slot)
    code = (slot.get("code") or "").strip() or None

    files, infos = [], []
    for i in range(frames):
        out_path = out_dir / ("story_%02d.png" % (i + 1))
        info = build_story(
            # Хук — только на ПЕРВОМ кадре: повторять его на каждой истории
            # значит показать человеку один и тот же заголовок восемь раз.
            hook=hook if i == 0 else (points[i] or hook),
            text=(points[i] if i == 0 else "") or _DEFAULT_SUB,
            # Плашка кодового слова — только на ПОСЛЕДНЕМ кадре: это призыв,
            # его место в конце серии, а не на каждом кадре.
            code=code if i == frames - 1 else None,
            background=(photos[i % len(photos)] if photos else None),
            out=str(out_path),
        )
        files.append(out_path.name)
        infos.append(info)

    media = {
        "kind": "story",
        "dir": slot_id,
        "story_kind": "series",
        "files": files,
        "count": len(files),
        "width": infos[0]["width"],
        "height": infos[0]["height"],
        "engine": infos[0]["engine"],
        "background": infos[0]["background"],
        "photos_used": len(photos),
        "code": bool(code),
    }
    return {"ok": True, "media": media}
