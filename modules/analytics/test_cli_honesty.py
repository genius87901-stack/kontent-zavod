# -*- coding: utf-8 -*-
"""Регресс-тест: аналитика обязана сказать, ЧЬИ это цифры, прежде чем советовать.

ЧТО ЛОВИМ. Источник метрик по умолчанию — `sample`, встроенный демо-образец
(config.DEFAULT_METRICS_SOURCE). При этом команды печатали шапки «Метрики
опубликованных постов» и «Рекомендации по опубликованным постам», ни словом не
оговариваясь, что посты не клиента. Человек читал разбор чужих демо-чисел как
разбор своего аккаунта — и принимал по нему решения о контенте.

Второе: советы даёт детерминированный движок правил, «умный» шаг
(recommender.llm_advice) намеренно оставлен заглушкой. Клиент должен понимать,
что перед ним пороговые правила, а не мнение модели.

Гоняется двумя способами:
    python modules/analytics/test_cli_honesty.py      # автономно
    pytest modules/analytics/test_cli_honesty.py      # если стоит pytest
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

from modules.analytics import cli, model, paths  # noqa: E402


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


def _record(post_id, source):
    """Одна запись хранилища с заданным источником цифр."""
    rec = model.new_record(
        {"post_id": post_id, "platform": "instagram", "format": "reels",
         "rubric": "Кейсы", "topic": "история клиента",
         "raw": {"reach": 1000, "impressions": 1200, "views": 900,
                 "likes": 50, "saves": 10, "comments": 3, "shares": 2,
                 "profile_visits": 20}},
        {"reach": 1000, "impressions": 1200, "views": 900, "likes": 50,
         "saves": 10, "comments": 3, "shares": 2, "profile_visits": 20},
        source)
    return rec


def _store(*sources):
    store = model.empty_store()
    for i, src in enumerate(sources, 1):
        model.upsert(store, _record("post%d" % i, src))
    return store


@contextlib.contextmanager
def _sandbox(store):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with _patched([
            (model, "load_store", lambda *a, **k: store),
            (paths, "ensure_dirs", lambda: None),
            (paths, "recs_path", lambda: root / "recs.json"),
            (paths, "feedback_path", lambda: root / "feedback.json"),
        ]):
            yield


def _run(fn, **kw):
    args = argparse.Namespace(post=None, json=False, **kw)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(args)
    return buf.getvalue()


def test_list_says_numbers_are_demo():
    """`list` на образце обязан предупредить, что это не посты клиента."""
    with _sandbox(_store("sample", "sample")):
        out = _run(cli.cmd_list)
    assert "ДЕМО-ОБРАЗЕЦ" in out, (
        "таблица метрик выдаёт демонстрационные числа за посты клиента:\n%s" % out)


def test_recommend_says_numbers_are_demo():
    """`recommend` на образце обязан предупредить о том же."""
    with _sandbox(_store("sample")):
        out = _run(cli.cmd_recommend)
    assert "ДЕМО-ОБРАЗЕЦ" in out, (
        "советы по демо-числам выданы как разбор аккаунта клиента:\n%s" % out)
    assert "не про ваш аккаунт" in out


def test_recommend_names_the_rules_engine():
    """Клиент должен видеть, что совет собран правилами, а не «умной» моделью."""
    with _sandbox(_store("sample")):
        out = _run(cli.cmd_recommend)
    assert "движком правил" in out and "без ИИ" in out, (
        "не сказано, из чего собран совет — человек примет правила за ИИ:\n%s"
        % out)


def test_live_numbers_are_not_called_demo():
    """Живые цифры демо-предупреждением не помечаются — иначе врём наоборот."""
    with _sandbox(_store("upload_post", "upload_post")):
        out = _run(cli.cmd_list)
    assert "ДЕМО-ОБРАЗЕЦ" not in out, (
        "живой сбор объявлен демо-образцом — клиент перестанет верить "
        "настоящим цифрам:\n%s" % out)
    assert "Цифры ваши" in out


def test_mixed_sources_are_counted_honestly():
    """Смешанное хранилище: сказано, сколько именно записей демонстрационные."""
    with _sandbox(_store("sample", "upload_post")):
        out = _run(cli.cmd_list)
    assert "ЧАСТЬ ЦИФР" in out and "1 записей из 2" in out, (
        "смесь живых и демо-чисел показана как однородная:\n%s" % out)


_TESTS = [
    test_list_says_numbers_are_demo,
    test_recommend_says_numbers_are_demo,
    test_recommend_names_the_rules_engine,
    test_live_numbers_are_not_called_demo,
    test_mixed_sources_are_counted_honestly,
]


if __name__ == "__main__":
    for fn in _TESTS:
        fn()
        print("  ok — %s" % (fn.__doc__ or fn.__name__).strip().splitlines()[0])
    print("")
    print("OK analytics/cli.py: перед каждым разбором названо, чьи это числа, "
          "и сказано, что совет собран движком правил, а не ИИ.")
