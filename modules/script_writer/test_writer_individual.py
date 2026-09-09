# -*- coding: utf-8 -*-
"""Регресс-тест сценариста: в сценарии клиента не должно быть контента другого
эксперта (BUG-2026-08-08, вторая волна — баг-репорт Елены Сокольской).

Первая волна починки закрыла ПАРСЕР книги смыслов. Замер после неё показал, что
подмена жила слоем выше: банк реплик самого сценариста был написан под одну нишу
и разложен по семи рубрикам одного эксперта. У клиента другой ниши совпадало имя
рубрики («Продажи» есть у всех) — и в сценарий дословно вставала чужая продающая
реплика, а бит «кейс» приносил чужой факт («136 точек роста»). Кодовое слово и
финальный призыв тоже были чужими.

Инвариант, который проверяем: сценарий собирается ЛИБО из данных клиента, ЛИБО
из нейтральной заготовки, ЛИБО остаётся честной пометкой-заданием. Третьего —
чужой конкретики — быть не может ни в одном поле.

    python3 modules/script_writer/test_writer_individual.py
    pytest modules/script_writer/test_writer_individual.py
"""
from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from modules.script_writer import writer  # noqa: E402

# Маркеры чужой конкретики. Список ровно из того, что реально утекало клиентам.
FOREIGN = ("136", "500 000", "РОСТ", "Валери", "Чиркова",
           "предприним", "entrepreneur", "business")

ELENA_BIBLE = """# Книга смыслов — Елена Сокольская (астрология)

Кодовое слово: напишите «ЗВЕЗДА», и я пришлю разбор натальной карты.

# Темы для постов

## Продажи
1. Почему консультация по натальной карте стоит своих денег
2. Как выбрать астролога и не нарваться на гадалку

## Кармические сценарии
1. Родовые программы, которые мешают строить отношения
2. Почему повторяется один и тот же тип партнёра
"""


def _write(tmpdir, text):
    p = os.path.join(tmpdir, "bible_client.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _foreign_hits(script: dict) -> list:
    flat = json.dumps(script, ensure_ascii=False)
    return [m for m in FOREIGN if m in flat]


def test_other_niche_script_has_no_foreign_content():
    """Рубрика «Продажи» есть и у астролога, и у бизнес-эксперта. Раньше именно
    на этом совпадении имён в чужой сценарий вставала чужая реплика."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, ELENA_BIBLE)
        for rubric in ("Продажи", "Кармические сценарии"):
            s = writer.build_script(
                {"rubric": rubric, "topic": "Почему повторяется один и тот же партнёр"},
                bible_path=path)
            hits = _foreign_hits(s)
            assert not hits, "чужое в сценарии (%s): %s" % (rubric, hits)


def test_no_bible_at_all_produces_tasks_not_foreign_data():
    """Состояние коробки до первого запуска bible_setup.py — в нём приезжают ВСЕ
    клиенты. Раньше здесь подставлялись кодовое слово и кейс другого эксперта."""
    s = writer.build_script({"rubric": "Продажи", "topic": "Как выбрать специалиста"},
                            bible_path="/no/such/file.md")
    assert not _foreign_hits(s), _foreign_hits(s)
    assert s["code_word"] == ""
    says = " ".join(sh["say"] for sh in s["shots"])
    assert "[ЗДЕСЬ ТВОЙ КЕЙС]" in says, "кейс обязан остаться пометкой, а не выдумкой"
    assert "[ЗДЕСЬ ТВОЁ КОДОВОЕ СЛОВО]" in says
    assert s["individual"]["percent"] == 0


def test_case_beat_never_invents_a_fact():
    """Доказательство обязано быть настоящим: без якорного кейса клиента бит
    «кейс» — пометка-задание, и никогда чужая или выдуманная история."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, ELENA_BIBLE)      # кейса в книге нет
        s = writer.build_script({"rubric": "Продажи", "topic": "Цена консультации"},
                                bible_path=path)
        case = [sh for sh in s["shots"] if sh["stage"] == "case"]
        assert case and case[0]["say"].startswith("[ЗДЕСЬ ТВОЙ КЕЙС]")


def test_individual_percent_is_reported():
    """Доля собранного из данных клиента должна быть в сценарии — именно её
    отсутствие позволило дефекту прожить полгода за зелёным «Модуль готов»."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, ELENA_BIBLE)
        s = writer.build_script({"rubric": "Кармические сценарии",
                                 "topic": "Родовые программы"}, bible_path=path)
        ind = s["individual"]
        assert ind["shots_total"] == len(s["shots"])
        assert ind["from_client"] >= 2, ind
        assert ind["percent"] == int(round(100.0 * ind["from_client"] / ind["shots_total"]))


def test_donor_file_is_not_broken_by_the_fix():
    """Регресс живого пользователя: у эксперта, чья книга смыслов заполнена
    полностью, сценарий обязан собираться ЦЕЛИКОМ из его же данных."""
    real = os.path.join(_REPO_ROOT, "CONTENT_BIBLE_Valeria_Chirkova.md")
    if not os.path.exists(real):
        return  # файла донора нет в этом окружении — тест не применим
    s = writer.build_script({"rubric": "Точки роста бизнеса",
                             "topic": "Где лежат дополнительные деньги"},
                            bible_path=real)
    assert s["individual"]["percent"] == 100, s["individual"]
    assert s["code_word"], "кодовое слово из книги потерялось"


def _run_all():
    test_other_niche_script_has_no_foreign_content()
    test_no_bible_at_all_produces_tasks_not_foreign_data()
    test_case_beat_never_invents_a_fact()
    test_individual_percent_is_reported()
    test_donor_file_is_not_broken_by_the_fix()


if __name__ == "__main__":
    _run_all()
    print("OK writer.py: сценарий собирается из данных клиента, "
          "чужой конкретики нет ни в одном поле")
