# -*- coding: utf-8 -*-
"""Ручной прогон сценариста. Всё через argparse — никакого input() (CONTRACT.md
§3): значения приходят аргументами или из brief.json.

Подкоманды:
    questions                       показать вопросы под сценарий
    example-brief                   записать brief.example.json (шаблон)
    write  --rubric R --topic T ... собрать сценарий и записать .md/.txt/.json
    write  --brief FILE             то же, но входные данные из brief.json
    show   --rubric R --topic T     собрать и напечатать в консоль (без файлов)
"""
from __future__ import annotations

import argparse
import json
import sys

from . import paths, questions, render, writer


def _load_brief(path_str: str) -> dict:
    from pathlib import Path
    p = Path(path_str)
    if not p.is_absolute():
        from .config import install_root
        p = install_root() / p
    with p.open(encoding="utf-8-sig") as fh:
        return json.load(fh)


def _brief_from_args(a) -> dict:
    if getattr(a, "brief", None):
        return _load_brief(a.brief)
    brief = {}
    for key in ("rubric", "topic", "hook", "code_word", "platform"):
        val = getattr(a, key, None)
        if val:
            brief[key] = val
    if getattr(a, "duration", None):
        brief["duration_sec"] = a.duration
    if getattr(a, "beats", None):
        brief["beats"] = a.beats
    return brief


def cmd_questions(a):
    print("=== Вопросы под сценарий (задаёт Claude Code, не модуль) ===")
    print(questions.render())
    return 0


def cmd_example_brief(a):
    paths.ensure_dirs()
    sample = {
        "rubric": "Точки роста",
        "topic": "История клиента: с чем пришёл и чем закончилось",
        "hook": "",
        "duration_sec": 45,
        "beats": 4,
        "code_word": "",
        "platform": "instagram",
        "tone": "на «ты», конкретика, без инфоцыганщины",
    }
    path = paths.brief_example_path()
    with path.open("w", encoding="utf-8") as fh:
        json.dump(sample, fh, ensure_ascii=False, indent=2)
    print("Шаблон брифа записан: %s" % path)
    print("Собери сценарий: python -m script_writer.cli write "
          "--brief script_writer/brief.example.json")
    return 0


def _print_how_it_was_made(script):
    """Сказать вслух, ИЗ ЧЕГО собран сценарий, а не только что он собран.

    ЗАЧЕМ. «Умный» шаг (writer.llm_script) — заявленная заглушка: модель не
    зовётся никогда, сценарий собирается детерминированно из книги смыслов и
    нейтральных заготовок. Печаталось при этом только «Сценарий собран», и
    человек мог решить, что текст написан ИИ под него. Плюс числом называем
    главное: сколько дублей действительно из его книги смыслов, а сколько —
    заготовки, которые придётся переписать своим голосом.
    """
    ind = script.get("individual") or {}
    pct = int(ind.get("percent") or 0)
    print("Собран движком правил — без ИИ и без обращений в сеть: ваша книга "
          "смыслов + заготовки модуля, разложенные по дублям.")
    if pct >= 80:
        print("Из вашей книги смыслов: %d%% дублей (%s из %s)."
              % (pct, ind.get("from_client", 0), ind.get("shots_total", 0)))
    elif pct > 0:
        print("Из вашей книги смыслов: только %d%% дублей (%s из %s) — остальное "
              "нейтральные заготовки, они звучат обобщённо и их стоит переписать "
              "своими словами." % (pct, ind.get("from_client", 0),
                                   ind.get("shots_total", 0)))
    else:
        print("Из вашей книги смыслов: 0% — это КАРКАС на нейтральных "
              "заготовках, а не ваш текст. Подключите книгу смыслов: "
              "python bible_setup.py --install")
    if ind.get("todo"):
        print("Мест-заданий, которые нужно дописать руками перед съёмкой: %s "
              "(кейс и/или кодовое слово — чужое сюда не подставляется)."
              % ind["todo"])


def cmd_write(a):
    brief = _brief_from_args(a)
    script = writer.build_script(brief)
    out = render.write_all(script)
    print("Сценарий собран: %d дублей, ~%d сек, рубрика «%s»."
          % (len(script["shots"]), script["total_duration_sec"],
             script["source"]["rubric"]))
    _print_how_it_was_made(script)
    print("  для съёмки : %s" % out["txt"])
    print("  разбор     : %s" % out["md"])
    print("  машинный   : %s" % out["json"])
    return 0


def cmd_show(a):
    brief = _brief_from_args(a)
    script = writer.build_script(brief)
    print(render.to_text(script))
    print("")
    _print_how_it_was_made(script)
    return 0


def _add_brief_args(p):
    p.add_argument("--brief", help="путь к brief.json (переопределяет остальные)")
    p.add_argument("--rubric", help="рубрика")
    p.add_argument("--topic", help="тема ролика")
    p.add_argument("--hook", help="готовая первая фраза (опц.)")
    p.add_argument("--duration", type=int, help="длительность, сек")
    p.add_argument("--beats", type=int, help="смысловых битов 3–5")
    p.add_argument("--code-word", dest="code_word", help="кодовое слово CTA (опц.)")
    p.add_argument("--platform", help="площадка")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Генератор сценариев съёмки (Reels).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("questions", help="показать вопросы под сценарий")
    sub.add_parser("example-brief", help="записать шаблон brief.example.json")

    p_w = sub.add_parser("write", help="собрать сценарий и записать файлы")
    _add_brief_args(p_w)

    p_s = sub.add_parser("show", help="собрать и напечатать в консоль")
    _add_brief_args(p_s)

    a = ap.parse_args(argv)
    handlers = {
        "questions": cmd_questions,
        "example-brief": cmd_example_brief, "write": cmd_write, "show": cmd_show,
    }
    return handlers[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
