# -*- coding: utf-8 -*-
"""Регресс-тест кодов возврата doctor'а модуля content_plan (CONTRACT.md §2).

Код возврата — СТЕПЕНЬ БЕДЫ (BOX-51):
    0 — модуль полностью работоспособен: план собирается, черновики собираются
        по-настоящему, публикация идёт штатным путём autopost --send;
    2 — УРЕЗАННЫЙ РЕЖИМ. В прототипе это ШТАТНОЕ состояние: жёсткий стоп
        публикации (gate.REAL_PUBLISH_IMPLEMENTED=False) и dry-run сборки
        черновиков (env CONTENT_PLAN_REAL_GEN не задан) означают, что план и
        расписание живые, а сами посты клиент собирает и публикует руками.
        Сюда же: банк тем на заготовках модуля, нехватка сырья, недоступная
        витрина, слоты сторис в копии БЕЗ модуля story_publisher (сам по себе
        формат сторис с 28.08.2026 собирается штатно и урезанным режимом не
        считается);
    1 — НЕ ГОТОВ: план повреждён/невалиден, стоп-темы просочились, публикация
        включена в обход штатного autopost, либо проверка не отработала.

Почему тест нужен именно здесь: доктор был бинарным и на полностью штатном
прототипе печатал «Модуль готов» с кодом 0 — владелец видел зелёный модуль,
который по факту ничего не публикует и собирает черновики вхолостую.

Гоняется двумя способами:
    python modules/content_plan/test_doctor.py      # автономно
    pytest modules/content_plan/test_doctor.py      # если стоит pytest
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.content_plan import (doctor, executor, gate,  # noqa: E402
                                  model, showcase, topics)


# --- вспомогательное ---------------------------------------------------------

#: Банк тем собран из книги смыслов клиента — деградации по этой линии нет.
_BANK_OK = ({}, {"client_bible_ok": True, "source": {"Кейсы": "client"},
                 "warnings": []})
#: Книга смыслов не читается — темы берутся с заготовок модуля.
_BANK_DEFAULT = ({}, {"client_bible_ok": False, "source": {}, "warnings": []})


def _slot(sid="s1", fmt="carousel"):
    return {"id": sid, "format": fmt, "platforms": ["instagram"],
            "publish_at": "2030-01-02T09:00:00",
            "veto_opens_at": "2030-01-01T21:00:00",
            "status": model.PLANNED, "rubric": "Кейсы",
            "topic": "история клиента", "hook": "первая фраза",
            "meaning": "смысл"}


def _plan(slots):
    return {"version": 1, "approved": False, "approved_at": None,
            "brief": {"horizon_days": 30, "posts_per_week": 4,
                      "veto_hours": 12, "forbidden": [],
                      "publish_approval_mode": "daily",
                      "post_time": "09:00", "post_timezone": "Europe/Moscow"},
            "topic_bank_meta": _BANK_OK[1], "slots": slots}


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
def _factory(*, publish_on, real_gen, plan=None, bank=_BANK_OK,
             showcase_ready=True):
    """Вымышленное состояние завода. Ни одного файла и ни одного вызова наружу."""
    def _load_plan(*_a, **_kw):
        if isinstance(plan, Exception):
            raise plan
        return plan if plan is not None else model.empty_plan()

    with _patched([
        (gate, "REAL_PUBLISH_IMPLEMENTED", publish_on),
        (executor, "real_gen_enabled", lambda: real_gen),
        (model, "load_plan", _load_plan),
        (topics, "load_topic_bank", lambda *a, **k: bank),
        (showcase, "provider_status",
         lambda *a, **k: (True, None) if showcase_ready
         else (False, "нет токена бота (проверочная подмена).")),
        (showcase, "pending_revisions", lambda *a, **k: []),
        (showcase, "package_readiness", lambda *a, **k: {"ready": []}),
    ]):
        yield


def _run():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = doctor.main()
    return code, buf.getvalue()


def _no_stacktrace(out):
    assert "Traceback" not in out and "  File \"" not in out, (
        "клиенту показали стектрейс — CONTRACT.md §2 запрещает:\n%s" % out)


# --- код 0 --------------------------------------------------------------------

def test_publishing_and_real_gen_on_returns_0():
    """Будущее штатное состояние: стоп снят, сборка реальная, путь — autopost.

    Проверка формы команды публикации настоящая: doctor._autopost_path_intact()
    собирает команду для вымышленного слота и убеждается, что гейт по-прежнему
    зовёт autopost.py с его собственным предохранителем --send.
    """
    with _factory(publish_on=True, real_gen=True):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 0, "полностью рабочее состояние — это 0, а не %s:\n%s" % (code, out)
    assert "Модуль готов" in out
    assert "ШТАТНЫМ путём" in out


# --- код 2: урезанный режим ---------------------------------------------------

def test_prototype_hard_stop_returns_2_not_0():
    """Штатный прототип: стоп публикации + dry-run сборки → урезанный режим.

    Это главный случай: раньше доктор печатал тут «Модуль готов» и уходил с 0.
    """
    with _factory(publish_on=False, real_gen=False):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 2, ("жёсткий стоп публикации и dry-run сборки — урезанный "
                       "режим (2), а доктор дал %s:\n%s" % (code, out))
    assert "урезанном режиме" in out
    assert "Автопубликация по плану ВЫКЛЮЧЕНА" in out
    assert "ВХОЛОСТУЮ" in out


def test_default_topic_bank_returns_2():
    """Книга смыслов не читается — план собирается на заготовках модуля."""
    with _factory(publish_on=True, real_gen=True, bank=_BANK_DEFAULT):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 2, "банк тем на заготовках — это 2, а не %s:\n%s" % (code, out)
    assert "ЗАГОТОВКАХ МОДУЛЯ" in out


def test_stories_slots_ok_when_engine_present():
    """СТОРИС СОБИРАЮТСЯ (28.08.2026). Раньше здесь стояло обратное: тест
    требовал кода 2 и слов «движка сборки сторис нет». Это закрепляло НЕПРАВДУ —
    движок лежал в соседнем модуле той же коробки (story_publisher), не было
    только шва. Теперь шов есть (executor → stories_bridge), и жаловаться
    доктору не на что: слоты сторис — обычные рабочие слоты."""
    with _factory(publish_on=True, real_gen=True,
                  plan=_plan([_slot("s1", "stories")])):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 0, "сторис собираются — это 0, а не %s:\n%s" % (code, out)
    assert "движка сборки сторис" not in out, (
        "доктор снова говорит, что движка сторис нет:\n" + out)


def test_stories_slots_returns_2_without_module():
    """А вот если модуля сторис в копии ДЕЙСТВИТЕЛЬНО нет (старая коробка,
    собранная до его появления) — доктор обязан сказать это вслух и назвать
    лечение. Проверяем по диску, а не по нашей памяти о составе коробки."""
    пусто = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__нет_такого__")
    with _factory(publish_on=True, real_gen=True,
                  plan=_plan([_slot("s1", "stories")])):
        with _patched([(doctor.config, "install_root", lambda: Path(пусто))]):
            code, out = _run()
    _no_stacktrace(out)
    assert code == 2, "нет модуля сторис — это 2, а не %s:\n%s" % (code, out)
    assert "модуля сборки сторис" in out, out
    assert "обновлен" in out.lower(), "жалоба обязана называть лечение:\n" + out


def test_showcase_unavailable_returns_2():
    """Витрина без токена бота — незаполненный ключ, всегда 2."""
    with _factory(publish_on=True, real_gen=True, showcase_ready=False,
                  plan=_plan([_slot("s1", "reels")])):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 2, "недоступная витрина — это 2, а не %s:\n%s" % (code, out)
    assert "Витрина" in out


# --- код 1: модуль не готов ---------------------------------------------------

def test_broken_plan_file_returns_1():
    """Файл плана повреждён — расписание выпуска прочитать нельзя."""
    with _factory(publish_on=True, real_gen=True,
                  plan=ValueError("content_plan.json повреждён")):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 1, "повреждённый план — это 1, а не %s:\n%s" % (code, out)
    assert "повреждён" in out


def test_publishing_without_autopost_path_returns_1():
    """Стоп снят, а штатный путь autopost --send не подтверждается — тревога."""
    def boom(*_a, **_kw):
        raise RuntimeError("штатная команда не собирается")

    with _factory(publish_on=True, real_gen=True):
        with _patched([(gate, "build_autopost_command", boom)]):
            code, out = _run()
    _no_stacktrace(out)
    assert code == 1, ("публикация в обход штатного предохранителя — это 1, "
                       "а не %s:\n%s" % (code, out))
    assert "в обход" in out


def test_forbidden_topic_leak_returns_1():
    """Стоп-тема клиента просочилась в план — контент нарушает его запрет."""
    plan = _plan([_slot("s1", "carousel")])
    plan["brief"]["forbidden"] = ["история клиента"]
    with _factory(publish_on=True, real_gen=True, plan=plan):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 1, "стоп-темы в плане — это 1, а не %s:\n%s" % (code, out)
    assert "Стоп-темы просочились" in out


def test_check_crash_returns_1_without_stacktrace():
    def boom():
        raise RuntimeError("проверочный сбой")

    with _patched([(doctor, "check", boom)]):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 1
    assert "не смогла отработать" in out


def _run_all():
    test_publishing_and_real_gen_on_returns_0()
    test_prototype_hard_stop_returns_2_not_0()
    test_default_topic_bank_returns_2()
    test_stories_slots_ok_when_engine_present()
    test_stories_slots_returns_2_without_module()
    test_showcase_unavailable_returns_2()
    test_broken_plan_file_returns_1()
    test_publishing_without_autopost_path_returns_1()
    test_forbidden_topic_leak_returns_1()
    test_check_crash_returns_1_without_stacktrace()


if __name__ == "__main__":
    _run_all()
    print("OK content_plan/doctor.py: 0 — публикация штатным путём и реальная "
          "сборка; 2 — жёсткий стоп прототипа, dry-run, заготовки вместо книги "
          "смыслов, сторис без движка, витрина без токена; 1 — повреждённый "
          "план, стоп-темы, обход autopost и сбой самой проверки.")
