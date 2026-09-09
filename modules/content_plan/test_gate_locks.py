# -*- coding: utf-8 -*-
"""Регресс-тест ЗАМКОВ публикации (gate._locks) — и прежде всего ПЯТОГО:
пока материалы собираются вхолостую, реальная отправка невозможна.

Зачем этот тест существует. Публикация (gate.REAL_PUBLISH_IMPLEMENTED) и сборка
материалов (executor / env CONTENT_PLAN_REAL_GEN) выключены ДВУМЯ РАЗНЫМИ
рубильниками. Худший исход проекта: сняли первый, забыв про второй — тогда в
НАСТОЯЩИЕ аккаунты клиентов уходят заглушки-описания вместо постов, а удалить
пост из чужой ленты уже нельзя. Пятый замок делает такой исход невозможным
механически, а не на словах в документации.

Ни одного сетевого запроса, ни одной публикации: гейт по устройству только
СОБИРАЕТ строку команды autopost, а тест вдобавок держит жёсткий стоп на месте.

Гоняется двумя способами:
    python modules/content_plan/test_gate_locks.py      # автономно
    pytest modules/content_plan/test_gate_locks.py      # если стоит pytest
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.content_plan import (config, executor, gate,  # noqa: E402
                                  model, paths)

#: «Сейчас» теста — заведомо позже времени выхода слота.
NOW = datetime(2030, 1, 3, 10, 0, 0)


@contextlib.contextmanager
def _patched(pairs):
    saved = [(obj, name, getattr(obj, name)) for obj, name, _ in pairs]
    for obj, name, value in pairs:
        setattr(obj, name, value)
    try:
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


@contextlib.contextmanager
def _sandbox(*, real_gen, live_publish=True):
    """Вымышленный завод: данные модуля — во временной папке, наружу ничего.

    real_gen      — включена ли РЕАЛЬНАЯ сборка материалов;
    live_publish  — «внутренний» конфиг content_plan_live_publish.
    Жёсткий стоп gate.REAL_PUBLISH_IMPLEMENTED НЕ трогаем ни в одном тесте.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        queue = root / "queue"
        queue.mkdir(parents=True, exist_ok=True)
        with _patched([
            (executor, "real_gen_enabled", lambda: real_gen),
            (config, "live_publish_enabled", lambda: live_publish),
            (paths, "ensure_dirs", lambda: None),
            (paths, "queue_dir", lambda: queue),
            (paths, "publish_log_path", lambda: root / "publish_log.jsonl"),
            (paths, "veto_board_path", lambda: root / "veto_board.md"),
        ]):
            yield queue


def _slot(sid="s1", fmt="carousel", status=model.APPROVED):
    return {"id": sid, "format": fmt, "platforms": ["instagram"],
            "publish_at": "2030-01-02T09:00:00",
            "veto_opens_at": "2030-01-01T21:00:00",
            "status": status, "rubric": "Кейсы", "topic": "история клиента",
            "hook": "первая фраза", "meaning": "смысл",
            "history": [{"at": "2030-01-01T00:00:00", "event": "status:x",
                         "note": ""}]}


def _plan(slots, approved=True):
    return {"version": 1, "approved": approved,
            "approved_at": "2030-01-01T00:00:00", "approved_by": "клиент",
            "brief": {"publish_approval_mode": "daily"}, "slots": slots}


def _put_artifact(queue, slot, *, dry_run, media=None, needs_input=None):
    """Кладёт в очередь файл материала — как это делает executor.generate_slot."""
    name = "%s.json" % slot["id"]
    art = {"slot_id": slot["id"], "format": slot.get("format"),
           "dry_run": bool(dry_run), "media": media}
    if needs_input:
        art["needs_input"] = needs_input
    (queue / name).write_text(json.dumps(art, ensure_ascii=False),
                              encoding="utf-8")
    slot["artifact"] = name
    return name


def _real_media(queue, slot):
    """«Настоящий» собранный материал: файлы слайдов рядом с описанием."""
    media = {"kind": "carousel", "files": ["01.png", "02.png", "03.png"]}
    return _put_artifact(queue, slot, dry_run=False, media=media)


# --- ГЛАВНОЕ: пятый замок ----------------------------------------------------

def test_dry_run_gen_blocks_publish_even_with_everything_else_open():
    """Сборка вхолостую — публикации нет, даже когда открыты ВСЕ прочие замки.

    План утверждён, выпуск утверждён, время вышло, конфиг live_publish=true,
    запуск с --arm. Единственная выключенная вещь — сборка материалов.
    """
    with _sandbox(real_gen=False) as queue:
        slot = _slot()
        _put_artifact(queue, slot, dry_run=True)   # заглушка-описание в очереди
        plan = _plan([slot])
        ok, reasons = gate._locks(plan, slot, NOW, armed=True)

    assert not ok, ("публикация прошла бы при сборке вхолостую — это и есть "
                    "худший исход проекта: заглушка ушла бы в живой аккаунт")
    assert len(reasons) == 1, ("должен держать ровно пятый замок, остальные "
                               "открыты, а получено: %s" % reasons)
    assert "вхолостую" in reasons[0], reasons
    assert "Публиковать нечего" in reasons[0], reasons


def test_dry_run_gen_blocks_even_without_stop_and_with_real_material_file():
    """Флаг сборки выключен — не спасает даже настоящий файл материала в очереди.

    Замок смотрит на РУБИЛЬНИК сборки, а не только на содержимое очереди: если
    сборка выключена, доверять очереди нельзя (файл мог остаться от прошлого).
    """
    with _sandbox(real_gen=False) as queue:
        slot = _slot()
        _real_media(queue, slot)
        plan = _plan([slot])
        ok, reasons = gate._locks(plan, slot, NOW, armed=True)
    assert not ok and "вхолостую" in reasons[0], reasons


def test_run_holds_slot_and_publishes_nothing_when_gen_is_dry_run():
    """Полный прогон гейта: слот созрел и утверждён — но ничего не ушло."""
    with _sandbox(real_gen=False) as queue:
        slot = _slot()
        _put_artifact(queue, slot, dry_run=True)
        plan = _plan([slot])
        report = gate.run(plan, now=NOW, armed=True)

    assert report["published"] == [], "наружу ушло — предохранитель пробит"
    assert report["held"], "слот обязан быть удержан и объяснён"
    why = "; ".join(report["held"][0]["reasons"])
    assert "вхолостую" in why, why
    assert "REAL_PUBLISH_IMPLEMENTED=False" in why, why
    assert slot["status"] == model.APPROVED, "статус не должен стать published"


def test_stories_slot_is_held_by_material_lock():
    """Сторис: движка сторис в заводе нет — слот вечно с needs_input.

    Даже при ВКЛЮЧЁННОЙ сборке материалов такой слот не имеет права уйти:
    в autopost.py вообще нет режима сторис (только --dir и --video), и
    команда для него собралась бы враньём про папку слайдов.
    """
    with _sandbox(real_gen=True) as queue:
        slot = _slot(sid="s-stories", fmt="stories")
        slot["needs_input"] = ("сторис: движка сторис в заводе нет — слот "
                               "остаётся черновиком плана")
        _put_artifact(queue, slot, dry_run=True,
                      needs_input=slot["needs_input"])
        plan = _plan([slot])
        ok, reasons = gate._locks(plan, slot, NOW, armed=True)
        report = gate.run(plan, now=NOW, armed=True)

    assert not ok, "пустой сторис-слот прошёл бы в публикацию"
    assert "не хватает входа" in reasons[0], reasons
    assert report["published"] == []
    assert report["held"], "сторис-слот должен удерживаться с объяснением"


def test_stale_dry_run_artifact_is_held_after_gen_switched_on():
    """Сборку включили ПОСЛЕ того, как слот собрался вхолостую.

    Флаг уже True, но в очереди по-прежнему заглушка — публиковать её нельзя,
    слот надо пересобрать.
    """
    with _sandbox(real_gen=True) as queue:
        slot = _slot(sid="s-stale")
        _put_artifact(queue, slot, dry_run=True)
        plan = _plan([slot])
        ok, reasons = gate._locks(plan, slot, NOW, armed=True)
    assert not ok, "заглушка из очереди ушла бы в живой аккаунт"
    assert "ЗАГЛУШКА" in reasons[0], reasons


def test_missing_artifact_file_is_held():
    """Материал слота потёрли/не записали — публиковать нечего."""
    with _sandbox(real_gen=True) as queue:
        slot = _slot(sid="s-nofile")
        slot["artifact"] = "s-nofile.json"      # файла в очереди нет
        plan = _plan([slot])
        ok, reasons = gate._locks(plan, slot, NOW, armed=True)
    assert not ok and "не читается" in reasons[0], reasons

    with _sandbox(real_gen=True):
        slot = _slot(sid="s-noartifact")        # слот вообще не проходил сборку
        ok, reasons = gate._locks(_plan([slot]), slot, NOW, armed=True)
    assert not ok and "не найден в очереди" in reasons[0], reasons


# --- замок не должен ломать будущее ------------------------------------------

def test_all_five_locks_open_when_material_is_real():
    """Когда всё честно готово — пятый замок открывается и не мешает.

    Именно так это должно выглядеть в день, когда владелец включит публикацию:
    замки открыты, и держит только жёсткий стоп (снимается вручную, отдельно).
    """
    with _sandbox(real_gen=True) as queue:
        slot = _slot(sid="s-real")
        _real_media(queue, slot)
        plan = _plan([slot])
        ok, reasons = gate._locks(plan, slot, NOW, armed=True)
        # Команда собрана на НАСТОЯЩИЙ материал из очереди, а не на плейсхолдер:
        # иначе в день включения публикации autopost получил бы несуществующий путь.
        cmd = gate.build_autopost_command(slot)
        report = gate.run(plan, now=NOW, armed=True)

    assert ok, "пятый замок держит готовый материал — так нельзя: %s" % reasons
    assert reasons == []
    assert "content_plan/queue/s-real" in cmd, cmd
    assert "<" not in cmd, "в команду попал плейсхолдер вместо пути: %s" % cmd
    assert cmd.strip().endswith("--send"), cmd
    # И всё равно наружу ничего не ушло — поверх пяти замков жёсткий стоп.
    assert report["published"] == [], "жёсткий стоп обязан держать даже здесь"
    assert "REAL_PUBLISH_IMPLEMENTED=False" in "; ".join(
        report["held"][0]["reasons"])


def test_other_locks_still_work_with_real_material():
    """Пятый замок ничего не отменяет: прочие четыре продолжают держать."""
    with _sandbox(real_gen=True, live_publish=False) as queue:
        slot = _slot(sid="s-locks")
        _real_media(queue, slot)

        ok, reasons = gate._locks(_plan([slot], approved=False), slot, NOW,
                                  armed=True)
        assert not ok and any("план не утверждён" in r for r in reasons), reasons

        ok, reasons = gate._locks(_plan([slot]), slot,
                                  datetime(2030, 1, 1, 0, 0, 0), armed=True)
        assert not ok and any("не время публикации" in r for r in reasons), reasons

        ok, reasons = gate._locks(_plan([slot]), slot, NOW, armed=False)
        assert not ok and any("не вооружена" in r for r in reasons), reasons

        held = _slot(sid="s-held", status=model.GENERATED)
        _real_media(queue, held)
        ok, reasons = gate._locks(_plan([held]), held, NOW, armed=True)
        assert not ok and any("выпуск не утверждён" in r for r in reasons), reasons


def test_hard_stop_constant_untouched():
    """Жёсткий стоп — решение владельца. Тест ловит его случайное снятие."""
    assert gate.REAL_PUBLISH_IMPLEMENTED is False, (
        "gate.REAL_PUBLISH_IMPLEMENTED снят кодом, а не человеком — "
        "это запрещено: включение публикации принимается отдельно и вручную")


def _run_all():
    test_dry_run_gen_blocks_publish_even_with_everything_else_open()
    test_dry_run_gen_blocks_even_without_stop_and_with_real_material_file()
    test_run_holds_slot_and_publishes_nothing_when_gen_is_dry_run()
    test_stories_slot_is_held_by_material_lock()
    test_stale_dry_run_artifact_is_held_after_gen_switched_on()
    test_missing_artifact_file_is_held()
    test_all_five_locks_open_when_material_is_real()
    test_other_locks_still_work_with_real_material()
    test_hard_stop_constant_untouched()


if __name__ == "__main__":
    _run_all()
    print("OK content_plan/gate.py: пятый замок держит публикацию, пока "
          "материалы собираются вхолостую (dry-run, заглушка в очереди, слот "
          "сторис без входа); при настоящем материале замок открывается, а "
          "наружу всё равно ничего не уходит — жёсткий стоп на месте.")
