# -*- coding: utf-8 -*-
"""Регресс-тест: сценарист говорит, ИЗ ЧЕГО собран сценарий, а не только что собран.

ЧТО ЛОВИМ. «Умный» шаг (writer.llm_script) — заявленная заглушка: модель не
зовётся никогда, сценарий собирается детерминированно из книги смыслов и
нейтральных заготовок. При этом cli печатал ровно одну строку — «Сценарий
собран: N дублей» — и человек мог решить, что текст написан ИИ под него. Хуже
того, молчали и о главном числе: сколько дублей действительно из его книги
смыслов, а сколько — заготовки, которые придётся переписать своим голосом.
Доктор об этом говорил, но доктора запускают отдельно и редко.

Гоняется двумя способами:
    python modules/script_writer/test_cli_honesty.py      # автономно
    pytest modules/script_writer/test_cli_honesty.py      # если стоит pytest
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.script_writer import cli, render, writer  # noqa: E402


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


def _script(percent, todo=0):
    """Сценарий с заданной долей «своего» — остальное неважно для теста."""
    base = writer.build_script({"rubric": "Кейсы", "topic": "история клиента"})
    total = len(base["shots"])
    base["individual"] = {"percent": percent, "shots_total": total,
                          "from_client": round(total * percent / 100),
                          "todo": todo}
    return base


def _run_write(script):
    with tempfile.TemporaryDirectory() as tmp:
        out = {"md": Path(tmp) / "s.md", "txt": Path(tmp) / "s.txt",
               "json": Path(tmp) / "s.json"}
        with _patched([(writer, "build_script", lambda *a, **k: script),
                       (render, "write_all", lambda *a, **k: out)]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.cmd_write(argparse.Namespace(
                    brief=None, rubric="Кейсы", topic="тема", hook=None,
                    code_word=None, platform=None, duration=None, beats=None))
            return buf.getvalue()


def test_write_says_it_is_not_ai():
    """Клиенту сказано, что текст собран движком правил, а не моделью."""
    out = _run_write(_script(90))
    assert "без ИИ" in out and "движком правил" in out, (
        "вывод не отличается от «за вас подумал ИИ» — а модель не звалась:\n%s"
        % out)


def test_write_names_individuality_share():
    """Названо число: сколько дублей действительно из книги смыслов клиента."""
    out = _run_write(_script(40))
    assert "40%" in out, "доля своего контента не названа:\n%s" % out
    assert "переписать" in out, (
        "не сказано, что заготовки придётся переписать своими словами:\n%s" % out)


def test_zero_individuality_is_called_a_skeleton():
    """0% — это КАРКАС, и слово должно прозвучать прямо."""
    out = _run_write(_script(0))
    assert "КАРКАС" in out, (
        "сценарий целиком из чужих заготовок выдан за контент клиента:\n%s" % out)
    assert "bible_setup.py" in out, "не подсказан способ подключить книгу смыслов"


def test_todo_places_are_named():
    """Мест-заданий, которые надо дописать руками, названо число."""
    out = _run_write(_script(90, todo=2))
    assert "Мест-заданий" in out and "2" in out, (
        "незаполненные места в сценарии не названы — человек придёт на съёмку "
        "с пустыми строками:\n%s" % out)


def test_high_individuality_is_not_scolded():
    """При 90% своего лишних упрёков нет — честность работает в обе стороны."""
    out = _run_write(_script(90))
    assert "КАРКАС" not in out and "переписать" not in out, (
        "хороший сценарий обруган как каркас:\n%s" % out)


_TESTS = [
    test_write_says_it_is_not_ai,
    test_write_names_individuality_share,
    test_zero_individuality_is_called_a_skeleton,
    test_todo_places_are_named,
    test_high_individuality_is_not_scolded,
]


if __name__ == "__main__":
    for fn in _TESTS:
        fn()
        print("  ok — %s" % (fn.__doc__ or fn.__name__).strip().splitlines()[0])
    print("")
    print("OK script_writer/cli.py: после сборки сказано, что сценарий собран "
          "правилами без ИИ, и названо число — сколько в нём своего.")
