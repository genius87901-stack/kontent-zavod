# -*- coding: utf-8 -*-
"""Регресс-тест кодов возврата doctor'а модуля script_writer (CONTRACT.md §2).

Код возврата — СТЕПЕНЬ БЕДЫ (BOX-51):
    0 — сценарий собирается ИЗ КНИГИ СМЫСЛОВ КЛИЕНТА, кодовое слово на месте;
    2 — УРЕЗАННЫЙ РЕЖИМ: сценарий соберётся, но каркасом на нейтральных
        заготовках (нет книги смыслов / она неполная / нет кодового слова);
    1 — НЕ ГОТОВ: сценарий не собирается вовсе либо проверка не отработала.

Почему тест нужен именно здесь: доктор был бинарным (0 или 1). «Книга смыслов
не найдена» — это не повод для красного «🔴 СРОЧНО» владельцу: ролик снять
можно, просто он будет звучать обобщённо. А вот молча ставить 0 тоже нельзя —
клиент должен видеть, что получает каркас, а не свой контент.

Гоняется двумя способами:
    python modules/script_writer/test_doctor.py      # автономно
    pytest modules/script_writer/test_doctor.py      # если стоит pytest
"""
from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.script_writer import bible, doctor, model, writer  # noqa: E402


# --- вспомогательное ---------------------------------------------------------

_BIBLE_OK = {"ok": True, "code_word": "СЛОВО", "hooks": ["хук"],
             "themes": {"Кейсы": ["тема"]}, "warnings": []}
_BIBLE_MISSING = {"ok": False}


def _script(percent=100, todo=0, code_word="СЛОВО", shots=True):
    """Валидный по схеме сценарий-пустышка с нужной «индивидуальностью»."""
    return {
        "arc": "classic",
        "code_word": code_word,
        "individual": {"percent": percent, "todo": todo,
                       "from_client": 2 if percent else 0, "shots_total": 2},
        "shots": [
            {"intent": model.INTENT_HOOK, "say": "первая фраза",
             "show": "крупный план", "duration_sec": 5},
            {"intent": model.INTENT_CTA, "say": "напишите слово",
             "show": "средний план", "duration_sec": 5},
        ] if shots else [],
    }


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
def _writer(bib, build):
    with _patched([
        (bible, "load_bible", lambda *a, **k: bib),
        (writer, "build_script", build),
        (writer, "_niche_hooks", lambda *a, **k: []),
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

def test_client_bible_and_code_word_returns_0():
    with _writer(_BIBLE_OK, lambda *a, **k: _script()):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 0, "книга смыслов на месте, 100%% своих дублей — это 0, а не %s:\n%s" % (code, out)
    assert "Модуль готов" in out


# --- код 2: урезанный режим ---------------------------------------------------

def test_no_bible_returns_2_not_1():
    """Нет книги смыслов — каркас вместо контента клиента. Это 2, не 1."""
    with _writer(_BIBLE_MISSING, lambda *a, **k: _script(percent=0)):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 2, ("отсутствие книги смыслов — урезанный режим (2), а доктор "
                       "дал %s. Код 1 ушёл бы владельцу красной тревогой:\n%s"
                       % (code, out))
    assert "урезанном режиме" in out
    assert "Книга смыслов" in out


def test_partial_individuality_returns_2():
    """Половина дублей — нейтральные заготовки: клиент дописывает руками."""
    with _writer(_BIBLE_OK, lambda *a, **k: _script(percent=50)):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 2, "неполная индивидуальность — это 2, а не %s:\n%s" % (code, out)
    assert "Индивидуальность" in out


def test_no_code_word_returns_2():
    """Без кодового слова заявки по ролику не придут — но снимать можно."""
    with _writer(_BIBLE_OK, lambda *a, **k: _script(code_word="", todo=1)):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 2, "пустое кодовое слово — это 2, а не %s:\n%s" % (code, out)
    assert "Кодовое слово" in out


# --- код 1: модуль не готов ---------------------------------------------------

def test_build_crash_returns_1():
    def boom(*_a, **_kw):
        raise RuntimeError("сборка сценария сломалась")

    with _writer(_BIBLE_OK, boom):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 1, "сценарий не собирается — это 1, а не %s:\n%s" % (code, out)
    assert "Не работает" in out


def test_invalid_schema_returns_1():
    """Сценарий без дублей — главного пути нет, это 1."""
    with _writer(_BIBLE_OK, lambda *a, **k: _script(shots=False)):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 1, "невалидный сценарий — это 1, а не %s:\n%s" % (code, out)


def test_check_crash_returns_1_without_stacktrace():
    def boom():
        raise RuntimeError("проверочный сбой")

    with _patched([(doctor, "check", boom)]):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 1
    assert "не смогла отработать" in out


def _run_all():
    test_client_bible_and_code_word_returns_0()
    test_no_bible_returns_2_not_1()
    test_partial_individuality_returns_2()
    test_no_code_word_returns_2()
    test_build_crash_returns_1()
    test_invalid_schema_returns_1()
    test_check_crash_returns_1_without_stacktrace()


if __name__ == "__main__":
    _run_all()
    print("OK script_writer/doctor.py: 0 — сценарий из книги смыслов клиента; "
          "2 — каркас на заготовках, неполная индивидуальность, пустое кодовое "
          "слово; 1 — сценарий не собирается и сбой самой проверки.")
