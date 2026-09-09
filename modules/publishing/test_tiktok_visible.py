# -*- coding: utf-8 -*-
"""Тесты проверки «видят ли ролик в TikTok посторонние» (publishing/tiktok_visible.py).

ЧТО ЗДЕСЬ ЛОВИТСЯ (замер 28.08.2026). Сервис публикации отвечает успехом и
отдаёт настоящую ссылку, а посторонний человек ролик не открывает. Проверка
существует ровно для того, чтобы отчёт об успехе перестал быть единственным
доказательством. Поэтому под охраной три вещи:

    1. карточка ролика пришла → «виден»; отказ → «не виден»;
    2. ОБРЫВ СЕТИ — это ТРЕТИЙ исход, а не «не виден». Если оборванная связь
       начнёт молча превращаться в приговор ролику, мы поменяем одну ложь на
       другую, и этот тест — единственное, что тут стоит на пути;
    3. человеку в отчёте не показываются коды и английские слова: отчёт читает
       владелец завода, а не программист.

НИКАКОЙ СЕТИ. Транспорт подменяется целиком (`set_transport`), ни одного
реального запроса отсюда не уходит.

Гоняется двумя способами:
    python modules/publishing/test_tiktok_visible.py    # автономно
    pytest modules/publishing/test_tiktok_visible.py    # если стоит pytest
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.publishing import tiktok_visible as tv  # noqa: E402

CHUZHOI = "https://www.tiktok.com/@dan.ragan/video/7294996907393371435"
NASH_1 = "https://www.tiktok.com/@chirkova.valeria/video/7678378480605711633"
NASH_2 = "https://www.tiktok.com/@chirkova.valeria/video/7677504879962623249"

#: Ответ живого эмбеда на ЧУЖОЙ публичный ролик — сокращённый до полей, по
#: которым мы судим (замер 28.08.2026).
KARTOCHKA = json.dumps({
    "title": "как я перестала бояться камеры",
    "author_name": "dan.ragan",
    "thumbnail_url": "https://p16.tiktokcdn.com/obj/oembed.jpeg",
    "provider_name": "TikTok",
}, ensure_ascii=False)


class _Stend:
    """Заглушка сети: по ссылке отдаёт заранее записанный ответ.

    Значение — либо пара (код, тело), либо исключение, которое надо бросить.
    Заодно считает запросы и паузы: вежливость к чужому сервису тоже проверяем."""

    def __init__(self, otvety):
        self.otvety = otvety
        self.zaprosy = []

    def __call__(self, url, timeout):
        self.zaprosy.append(url)
        otvet = self.otvety[url]
        if isinstance(otvet, BaseException):
            raise otvet
        return otvet


def _s_transportom(stend, fn):
    prev = tv.set_transport(stend)
    try:
        return fn()
    finally:
        tv.set_transport(prev)


def test_kartochka_znachit_viden():
    """Карточка ролика пришла — значит посторонний его открывает."""
    stend = _Stend({CHUZHOI: (200, KARTOCHKA)})
    got = _s_transportom(stend, lambda: tv.check(CHUZHOI))
    assert got["result"] == tv.VIDEN, got
    assert stend.zaprosy and CHUZHOI.split("/")[-1] in stend.zaprosy[0], stend.zaprosy


def test_otkaz_znachit_ne_viden():
    """На наши ролики эмбед отвечает отказом — для зрителя ролика нет."""
    stend = _Stend({NASH_1: (400, "")})
    got = _s_transportom(stend, lambda: tv.check(NASH_1))
    assert got["result"] == tv.NE_VIDEN, got
    # Причину проверка знать не может: так же выглядит и снятый, и чужой,
    # и несуществующий ролик. В пояснении не должно быть диагноза.
    assert "бан" not in got["why"].lower(), got


def test_obryv_seti_ne_ravno_ne_viden():
    """Главный тест файла: оборванная сеть НЕ приговор ролику.

    Ровно здесь и рождалась бы новая ложь — записать «не виден» потому, что мы
    сами до TikTok не дошли."""
    import urllib.error
    for beda in (urllib.error.URLError("нет связи"),
                 TimeoutError("вышло время"),
                 OSError("сеть недоступна")):
        stend = _Stend({NASH_1: beda})
        got = _s_transportom(stend, lambda: tv.check(NASH_1))
        assert got["result"] == tv.NE_PROVERENO, (beda, got)
        assert got["result"] != tv.NE_VIDEN, (beda, got)
        assert got["why"], "молчаливый третий исход бесполезен: %r" % got


def test_dvuhsotka_bez_kartochki_ne_provereno():
    """Ответ есть, а карточки в нём нет — мы не поняли ответ, а не «не виден»."""
    for telo in ("", "{}", "не-json вовсе", json.dumps({"title": ""})):
        stend = _Stend({NASH_1: (200, telo)})
        got = _s_transportom(stend, lambda: tv.check(NASH_1))
        assert got["result"] == tv.NE_PROVERENO, (telo, got)


def test_sluzhba_zanyata_ne_provereno():
    """Лимит запросов и поломка на той стороне — не приговор ролику."""
    for kod in (429, 500, 502, 503):
        stend = _Stend({NASH_1: (kod, "")})
        got = _s_transportom(stend, lambda: tv.check(NASH_1))
        assert got["result"] == tv.NE_PROVERENO, (kod, got)


def test_pachka_razlozhena_verno():
    """Один виден, один нет, один непроверен — итог разложен по трём кучам."""
    stend = _Stend({
        CHUZHOI: (200, KARTOCHKA),
        NASH_1: (400, ""),
        NASH_2: OSError("сеть недоступна"),
    })
    got = _s_transportom(
        stend, lambda: tv.check_many([CHUZHOI, NASH_1, NASH_2], pause=0))
    assert [r["result"] for r in got] == [tv.VIDEN, tv.NE_VIDEN,
                                          tv.NE_PROVERENO], got
    box = tv.summary(got)
    assert [r["url"] for r in box[tv.VIDEN]] == [CHUZHOI], box
    assert [r["url"] for r in box[tv.NE_VIDEN]] == [NASH_1], box
    assert [r["url"] for r in box[tv.NE_PROVERENO]] == [NASH_2], box
    assert len(stend.zaprosy) == 3, stend.zaprosy


def test_pauza_mezhdu_zaprosami_est():
    """Чужой сервис нельзя долбить очередью: пауза есть, и только МЕЖДУ."""
    stend = _Stend({CHUZHOI: (200, KARTOCHKA), NASH_1: (400, ""),
                    NASH_2: (400, "")})
    spal = []
    prev = tv._sleep
    tv._sleep = spal.append
    try:
        _s_transportom(stend, lambda: tv.check_many([CHUZHOI, NASH_1, NASH_2],
                                                    pause=1.5))
    finally:
        tv._sleep = prev
    assert spal == [1.5, 1.5], spal          # три ролика — две паузы


def test_kody_vyhoda():
    """0 — все видны, 1 — есть невидимые, 2 — что-то не проверили."""
    vsem_vidno = [{"url": CHUZHOI, "result": tv.VIDEN, "why": ""}]
    est_nevidimye = vsem_vidno + [{"url": NASH_1, "result": tv.NE_VIDEN,
                                   "why": ""}]
    est_neproverennye = est_nevidimye + [{"url": NASH_2,
                                          "result": tv.NE_PROVERENO, "why": ""}]
    assert tv.exit_code(vsem_vidno) == 0
    assert tv.exit_code(est_nevidimye) == 1
    # Непроверенное важнее: список невидимых заведомо неполон.
    assert tv.exit_code(est_neproverennye) == 2
    assert tv.exit_code([]) == 1


LATINITSA = re.compile(r"[A-Za-z]+")
#: Единственное латинское слово, которому в отчёте место, — имя самой площадки.
RAZRESHENO = {"tiktok"}


def _bez_ssylok(text):
    """Убрать из отчёта сами ссылки: там латиница неизбежна и это не текст."""
    return " ".join(w for w in text.split()
                    if "://" not in w and "tiktok.com" not in w)


def test_v_otchete_net_angliyskih_slov_i_kodov():
    """Отчёт читает владелец завода, а не программист."""
    results = [
        {"url": CHUZHOI, "result": tv.VIDEN, "why": ""},
        {"url": NASH_1, "result": tv.NE_VIDEN,
         "why": "TikTok не отдаёт карточку ролика посторонним"},
        {"url": NASH_2, "result": tv.NE_PROVERENO,
         "why": "не дошли до TikTok (сеть недоступна)"},
    ]
    text = tv.render(results)
    ostatok = _bez_ssylok(text)
    chuzhie = {w.lower() for w in LATINITSA.findall(ostatok)} - RAZRESHENO
    assert not chuzhie, "английские слова в отчёте человеку: %s" % sorted(chuzhie)
    for kod in ("400", "200", "404", "429", "500"):
        assert kod not in text, "код ответа в отчёте человеку: %s" % kod
    # И главное: отчёт обязан развести «не видят» и «не проверили».
    assert "НЕ видят" in text, text
    assert "проверить не удалось" in text, text


def test_otchet_ne_stavit_diagnoz():
    """«Не виден» — это не «забанили»: причину проверка не знает и не выдумывает."""
    text = tv.render([{"url": NASH_1, "result": tv.NE_VIDEN, "why": ""}])
    for vydumka in ("бан", "забанил", "модерац", "удалён администрац"):
        assert vydumka not in text.lower(), (vydumka, text)
    assert "не знает" in text.lower(), text


def test_pustaya_ssylka_ne_hodit_v_set():
    """Пустая строка в списке — не повод дёргать чужой сервис."""
    stend = _Stend({})
    got = _s_transportom(stend, lambda: tv.check("   "))
    assert got["result"] == tv.NE_PROVERENO, got
    assert stend.zaprosy == [], stend.zaprosy


def test_spisok_iz_faila():
    """Файл со ссылками: по одной в строке, пометки и пустые строки пропускаем."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "ссылки.txt"
        p.write_text("# ролики за вторник\n%s\n\n  %s  \n" % (NASH_1, NASH_2),
                     encoding="utf-8")
        assert tv.read_list(p) == [NASH_1, NASH_2], tv.read_list(p)


def test_cli_pachkoi_bez_seti():
    """Сквозной проход командой: печать человеку и код возврата 1 на невидимых."""
    import contextlib
    import io
    stend = _Stend({CHUZHOI: (200, KARTOCHKA), NASH_1: (400, "")})
    buf = io.StringIO()

    def _run():
        with contextlib.redirect_stdout(buf):
            return tv.main(["--url", CHUZHOI, "--url", NASH_1, "--pause", "0"])

    code = _s_transportom(stend, _run)
    text = buf.getvalue()
    assert code == 1, (code, text)
    assert NASH_1 in text and CHUZHOI in text, text
    assert "НЕ видят" in text, text


def test_cli_bez_ssylok_ne_hodit_v_set():
    import contextlib
    import io
    stend = _Stend({})
    buf = io.StringIO()

    def _run():
        with contextlib.redirect_stdout(buf):
            return tv.main([])

    code = _s_transportom(stend, _run)
    assert code == 1, code
    assert stend.zaprosy == [], stend.zaprosy
    assert "Не с чего начать" in buf.getvalue(), buf.getvalue()


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok  %s" % name)


if __name__ == "__main__":
    _run_all()
    print("OK publishing/tiktok_visible.py: карточка = виден, отказ = не виден, "
          "обрыв связи = отдельный третий исход и НЕ приговор ролику; отчёт "
          "человеку без кодов и английских слов")
