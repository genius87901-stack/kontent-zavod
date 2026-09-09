# -*- coding: utf-8 -*-
"""Регресс-тест кодов возврата doctor'а модуля analytics (CONTRACT.md §2, BOX-51).

Код возврата доктора — это СТЕПЕНЬ БЕДЫ, а не «да/нет»:
    0 — модуль полностью работоспособен;
    2 — УРЕЗАННЫЙ РЕЖИМ (советы считаются по образцу, а не по постам клиента);
    1 — НЕ ГОТОВ (анализировать нечего) либо сама проверка не отработала.

Почему тест нужен именно здесь: до 11.08.2026 кода 2 не было. Доктор печатал
человеку «Это НЕ поломка — модуль работает на образце» и всё равно уходил с
кодом 1, а `health_report.phase()` считает код 1 на пройденном этапе поломкой и
шлёт владельцу красное «🔴 СРОЧНО». Клиенты получали тревогу на фразу «это не
поломка» в их же выводе. Тест ловит откат этой правки.

Гоняется двумя способами:
    python modules/analytics/test_doctor.py      # автономно
    pytest modules/analytics/test_doctor.py      # если стоит pytest
"""
from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.analytics import (cache, config, doctor, live,  # noqa: E402
                               providers, upload_post)


# --- вспомогательное ---------------------------------------------------------

@contextlib.contextmanager
def _patched(pairs):
    """Временно подменяет атрибуты: [(модуль, имя, значение), …]."""
    saved = [(obj, name, getattr(obj, name)) for obj, name, _ in pairs]
    for obj, name, value in pairs:
        setattr(obj, name, value)
    try:
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


def _run():
    """Запускает доктора молча. Возвращает (код возврата, весь его вывод)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = doctor.main()
    return code, buf.getvalue()


def _live_ready(*_a, **_kw):
    return True, ""


def _live_absent(*_a, **_kw):
    return False, "нет ключа UPLOAD_POST_API_KEY (проверочная подмена)"


def _no_stacktrace(out):
    assert "Traceback" not in out and "  File \"" not in out, (
        "клиенту показали стектрейс — CONTRACT.md §2 запрещает:\n%s" % out)


# --- код 0: живой сбор настроен, метрики берутся у самого клиента ------------

def test_live_source_configured_returns_0():
    with _patched([
        (config, "metrics_source", lambda *a, **k: "upload_post"),
        (upload_post, "has_key", lambda: True),
        (live.LiveSession, "available", _live_ready),
        (cache, "last_success", lambda *a, **k: ""),
    ]):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 0, "полностью настроенный модуль обязан дать 0, а дал %s:\n%s" % (code, out)
    assert "Модуль готов" in out


# --- код 2: урезанный режим ---------------------------------------------------

def test_sample_source_returns_2_not_1():
    """Источник = встроенный образец: советы есть, но не по постам клиента.

    Это ровно тот случай, ради которого в контракте появился код 2: человеку не
    нужно бросать всё и чинить прямо сейчас.
    """
    with _patched([
        (config, "metrics_source", lambda *a, **k: "sample"),
        (upload_post, "has_key", lambda: False),
        (live.LiveSession, "available", _live_absent),
        (cache, "last_success", lambda *a, **k: ""),
    ]):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 2, ("образец вместо живых метрик — это урезанный режим (2), "
                       "а доктор дал %s. Код 1 уходит владельцу красной "
                       "тревогой:\n%s" % (code, out))
    assert "урезанном режиме" in out
    assert "ОБРАЗЦА" in out or "образц" in out


def test_live_requested_without_key_returns_2():
    """Живой сбор попросили, ключа нет — незаполненный ключ всегда код 2."""
    with _patched([
        (config, "metrics_source", lambda *a, **k: "upload_post"),
        (upload_post, "has_key", lambda: False),
        (live.LiveSession, "available", _live_absent),
        (cache, "last_success", lambda *a, **k: ""),
    ]):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 2, "незаполненный ключ площадки — это 2, а не %s:\n%s" % (code, out)
    assert "не настроен" in out


# --- код 1: модуль не готов ---------------------------------------------------

def test_no_sample_data_returns_1():
    """Анализировать нечего: образец не читается, живого сбора нет → 1."""
    with _patched([
        (config, "metrics_source", lambda *a, **k: "sample"),
        (upload_post, "has_key", lambda: False),
        (live.LiveSession, "available", _live_absent),
        (cache, "last_success", lambda *a, **k: ""),
        (providers, "load_sample", lambda *a, **k: []),
    ]):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 1, "без единого источника данных модуль не готов (1), а дал %s:\n%s" % (code, out)
    assert "анализировать нечего" in out


def test_check_crash_returns_1_without_stacktrace():
    """Сама проверка упала → код 1 и человеческая строка, а не стектрейс."""
    def boom():
        raise RuntimeError("проверочный сбой")

    with _patched([(doctor, "check", boom)]):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 1, "сбой самой проверки — это 1, а не %s" % code
    assert "не смогла отработать" in out


def _run_all():
    test_live_source_configured_returns_0()
    test_sample_source_returns_2_not_1()
    test_live_requested_without_key_returns_2()
    test_no_sample_data_returns_1()
    test_check_crash_returns_1_without_stacktrace()


if __name__ == "__main__":
    _run_all()
    print("OK analytics/doctor.py: 0 — живой сбор настроен; 2 — работа по "
          "образцу и незаполненный ключ (не красная тревога); 1 — данных нет "
          "вовсе и сбой самой проверки. Стектрейсов клиенту нет.")
