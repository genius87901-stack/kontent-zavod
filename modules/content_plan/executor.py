# -*- coding: utf-8 -*-
"""Автономный исполнитель: по утверждённому плану генерит созревшие слоты и
кладёт готовое в очередь content_plan/queue/.

ПРЕДОХРАНИТЕЛЬ: генерация идёт ТОЛЬКО если план утверждён (plan.approved). Без
утверждения — слово человека на период не дано, исполнитель ничего не делает.

Генерация в прототипе — dry-run: реальные движки (engine*, render_reels,
talk_broll) НЕ запускаются и API не тратятся. `generate_slot` — тонкий шов:
в проде здесь вызывается движок под формат, в прототипе кладётся заглушка-описание
того, каким путём слот собрался бы.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from . import model, paths

# За сколько дней до публикации начинаем генерить слот (запас на вето + правки).
DEFAULT_LEAD_DAYS = 2

# Env-флаг реальной генерации. По умолчанию (не задан) — прежний dry-run:
# движки не зовутся, API не тратятся. Снять предохранитель ПУБЛИКАЦИИ он НЕ
# может — это только про сборку черновика (gate.REAL_PUBLISH_IMPLEMENTED отдельно).
REAL_GEN_ENV = "CONTENT_PLAN_REAL_GEN"


def real_gen_enabled() -> bool:
    """Включена ли РЕАЛЬНАЯ сборка черновиков (env CONTENT_PLAN_REAL_GEN).

    Дефолт — False (dry-run), обратная совместимость. Публикацию не трогает."""
    val = os.environ.get(REAL_GEN_ENV)
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "да", "on")


def _now(now=None) -> datetime:
    return now or datetime.now()


def _due_for_generation(slot: dict, now: datetime, lead_days: int) -> bool:
    if slot.get("status") != model.PLANNED:
        return False
    try:
        publish_at = datetime.strptime(slot["publish_at"], "%Y-%m-%dT%H:%M:%S")
    except (KeyError, ValueError):
        return False
    return now >= (publish_at - timedelta(days=lead_days))


def _engine_hint(slot: dict) -> str:
    """Каким путём слот собрался бы в проде (для описания в dry-run)."""
    fmt = slot.get("format")
    if fmt == "reels":
        return ("render_reels.py: расшифровка речи talk_transcribe → смысловые "
                "блоки talk_segment → монтаж talk_plan → перебивки talk_broll → "
                "сборка engine_talk (хук на обложку, karaoke-субтитры)")
    if fmt == "carousel":
        return ("engine_photo.py / engine4.py: обложка-крючок + слайды смысла, "
                "единый тёмный грейд, акцент по ротации")
    if fmt == "stories":
        return ("story_publisher/story_builder.py: кадр 9:16 (хук + строка "
                "смысла + плашка кодового слова) поверх фото-материала или "
                "синтетического тёмного фона, рендер — тем же engine_photo, "
                "что и у каруселей")
    return "движок под формат %r не определён" % fmt


def _real_media_for(slot: dict):
    """Зовёт движок под формат и возвращает результат-словарь bridge:
      {"ok": True,  "media": {...}}            — собрано настоящее медиа;
      {"ok": False, "needs_input": "..."}      — входа нет, останемся на dry-run.

    Движки зовём ТОЛЬКО отсюда (как оркестратор, не переписывая их). carousel →
    carousel_bridge (engine_photo); reels → reels_bridge (render_reels:
    talk_transcribe→talk_plan→talk_broll→engine_talk); stories → stories_bridge
    (story_publisher.story_builder, кадр 9:16 тем же engine_photo).
    Все три возвращают единый контракт ok/needs_input и все три за флагом
    реальной генерации — без исходных входов (фото / говорящее видео+блоки /
    смыслы) отдают needs_input, и слот остаётся на dry-run.

    СТОРИС ПОДКЛЮЧЕНЫ 28.08.2026. До этого здесь стояла жёсткая строка «движка
    сторис в заводе нет», и слоты сторис не собирались никогда — при том что
    движок лежал в соседнем модуле той же коробки. Единственный формат, которому
    для черновика хватает и пустого входа: фото нет — фон рисуется синтетический.
    Исключения движка НЕ глотаем: их поймает run() и пометит слот FAILED.

    ДЕНЬГИ. Единственный платный вызов на этом пути — смысловая разбивка речи
    моделью внутри reels_bridge (ключ клиента ANTHROPIC_API_KEY). Он возможен
    только здесь, под флагом реальной сборки, и только если клиент сам завёл
    ключ. Ключа нет — модель не зовётся, слот честно остаётся черновиком с
    пометкой needs_input («блоки готовит человек»). Неудачный вызов модели тоже
    приходит сюда как needs_input, а не как исключение: один упавший слот не
    должен ронять прогон плана."""
    fmt = slot.get("format")
    if fmt == "carousel":
        from . import carousel_bridge
        return carousel_bridge.build(slot)
    if fmt == "reels":
        from . import reels_bridge
        return reels_bridge.build(slot)
    if fmt == "stories":
        from . import stories_bridge
        return stories_bridge.build(slot)
    return {"ok": False, "needs_input": "движок под формат %r не определён" % fmt}


def generate_slot(slot: dict, dry_run: bool = True) -> dict:
    """ШОВ К ДВИЖКАМ. При dry_run=True (дефолт) движки НЕ запускаются: кладём
    заглушку-описание в очередь. При dry_run=False — реальный вызов движка под
    формат (carousel и reels собираются по-настоящему: carousel_bridge → слайды,
    reels_bridge → говорящий reels.mp4) с записью настоящего медиа в очередь
    content_plan/queue/.

    Guard: если движок реальной генерации сообщает об отсутствии входов
    (needs_input) — слот НЕ падает: кладём dry-run-артефакт с пометкой, какой
    вход нужен. Публикацию это не трогает — только сборка черновика.

    Возвращает artifact dict (что положили в очередь)."""
    paths.ensure_dirs()
    artifact = {
        "slot_id": slot["id"],
        "format": slot.get("format"),
        "topic": slot.get("topic"),
        "hook": slot.get("hook"),
        "meaning": slot.get("meaning"),
        "platforms": slot.get("platforms"),
        "engine_path": _engine_hint(slot),
        "dry_run": bool(dry_run),
        "created_at": model.now_iso(),
    }

    if dry_run:
        artifact["media"] = None
        artifact["note"] = ("DRY-RUN: движок НЕ запускался, API не тратились. "
                            "Реальная сборка — под env %s=1." % REAL_GEN_ENV)
    else:
        result = _real_media_for(slot)
        if result.get("ok"):
            # Настоящий черновик собран движком.
            artifact["dry_run"] = False
            artifact["media"] = result["media"]
            artifact["note"] = ("РЕАЛЬНАЯ СБОРКА: движок собрал черновик. "
                                "Публикации НЕ было — только медиа в очереди.")
        else:
            # Guard: входов нет — не падаем, откатываемся на dry-run c пометкой.
            artifact["dry_run"] = True
            artifact["media"] = None
            artifact["needs_input"] = result.get("needs_input", "неизвестный вход")
            artifact["note"] = ("DRY-RUN (нужен вход): реальная сборка запрошена, "
                                "но не хватает входа — движок не запускался.")

    out = paths.queue_dir() / ("%s.json" % slot["id"])
    with out.open("w", encoding="utf-8") as fh:
        json.dump(artifact, fh, ensure_ascii=False, indent=2)
    artifact["queue_file"] = out.name
    return artifact


def run(plan: dict, now: datetime = None, lead_days: int = DEFAULT_LEAD_DAYS,
        dry_run: bool = True, limit: int = None) -> dict:
    """Прогон исполнителя по плану. Меняет plan на месте (статусы слотов).

    Возвращает сводку: что сгенерено, что пропущено и почему.
    """
    now = _now(now)
    report = {"generated": [], "skipped_not_approved": False,
              "due": 0, "errors": []}

    if not model.is_approved(plan):
        # Слово человека на период не дано — не генерим ничего.
        report["skipped_not_approved"] = True
        return report

    made = 0
    for slot in plan.get("slots", []):
        if not _due_for_generation(slot, now, lead_days):
            continue
        report["due"] += 1
        if limit is not None and made >= limit:
            continue
        try:
            artifact = generate_slot(slot, dry_run=dry_run)
            real = not artifact.get("dry_run")
            need = artifact.get("needs_input")
            if need:
                note = "сгенерено (dry-run, нужен вход: %s)" % need
                slot["needs_input"] = need
            else:
                note = "сгенерено исполнителем (%s)" % (
                    "РЕАЛЬНО" if real else "dry-run")
                slot.pop("needs_input", None)
            model.set_status(slot, model.GENERATED, note)
            slot["generated_at"] = model.now_iso()
            slot["artifact"] = artifact.get("queue_file")
            report["generated"].append(slot["id"])
            made += 1
        except Exception as ex:  # ошибка генерации гасит только свой слот
            model.set_status(slot, model.FAILED, "ошибка генерации: %s" % ex)
            report["errors"].append({"slot": slot["id"], "error": str(ex)})

    return report
