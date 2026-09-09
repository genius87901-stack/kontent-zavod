# -*- coding: utf-8 -*-
"""Регресс-тест ЧЕСТНОСТИ витрины: «показано клиенту» ≠ «записано в файл».

ЧТО ЛОВИМ. Заглушка провайдера (`CONTENT_PLAN_SHOWCASE_STUB=1`) не трогает сеть
и пишет сообщения в `content_plan/showcase_stub/*.txt`. Она возвращала
`sent=True`, слот уезжал в статус «выложено в витрину, ждёт просмотра клиентом»,
в план записывались выдуманные `message_ids` вида `stub-<slot>`, а cli печатал
«Выложено: N». Ни один байт при этом не уходил в Telegram: клиент материала не
видел, а завод — и план, и отчёт, и Claude, читающий отчёт, — считал показанным.

Правило, которое тест закрепляет: показанным считается только то, что человек
действительно мог открыть в телефоне. Пробный прогон, заглушка и любой отказ
статус слота НЕ меняют и «выложено» о себе не пишут.

Гоняется двумя способами:
    python modules/content_plan/test_showcase_truth.py     # автономно
    pytest modules/content_plan/test_showcase_truth.py     # если стоит pytest
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.content_plan import doctor, model, paths, showcase  # noqa: E402


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
def _sandbox(stub: bool):
    """Витрина в песочнице: свои папки данных, никакой сети и никакого .env."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "queue").mkdir(parents=True)
        was = os.environ.get(showcase.STUB_ENV)
        if stub:
            os.environ[showcase.STUB_ENV] = "1"
        else:
            os.environ.pop(showcase.STUB_ENV, None)
        try:
            with _patched([
                (paths, "data_dir", lambda: root),
                (paths, "queue_dir", lambda: root / "queue"),
                (paths, "showcase_log_path", lambda: root / "showcase_log.jsonl"),
                (paths, "ensure_dirs", lambda: None),
            ]):
                yield root
        finally:
            if was is None:
                os.environ.pop(showcase.STUB_ENV, None)
            else:
                os.environ[showcase.STUB_ENV] = was


def _plan():
    slot = {"id": "2030-01-02-carousel", "format": "carousel",
            "platforms": ["instagram"], "publish_at": "2030-01-02T09:00:00",
            "veto_opens_at": "2030-01-01T21:00:00", "rubric": "Кейсы",
            "topic": "история клиента", "status": model.GENERATED,
            "artifact": None}
    return {"version": 1, "approved": False, "brief": {"horizon_days": 30},
            "slots": [slot]}, slot


# --- заглушка не изображает выкладку -----------------------------------------

def test_stub_does_not_report_sent():
    """Заглушка обязана честно сказать «в Telegram НЕ отправлено»."""
    with _sandbox(stub=True):
        plan, slot = _plan()
        res = showcase.send_unit(slot, plan, send=True)
    assert res["ok"], "заглушка не должна выглядеть отказом: %r" % res
    assert res["sent"] is False, (
        "заглушка отчиталась об отправке (sent=True), хотя сети не было вовсе — "
        "клиент решит, что материал у него в канале: %r" % res)
    assert "НЕ отправлено" in res["reason"], (
        "в причине не сказано прямым текстом, что отправки не было: %r"
        % res["reason"])
    assert not res.get("message_ids"), (
        "заглушка выдумала идентификаторы сообщений %r — в плане они выглядят "
        "как настоящий след отправки" % res.get("message_ids"))


def test_stub_does_not_change_slot_status():
    """Статус слота после заглушки — прежний: клиент ничего не видел."""
    with _sandbox(stub=True):
        plan, slot = _plan()
        rep = showcase.run(plan, send=True)
    assert slot["status"] == model.GENERATED, (
        "слот уехал в статус %r после заглушки — план теперь врёт, что клиент "
        "этот материал видел" % slot["status"])
    assert "showcase" not in slot, (
        "в слот записан след выкладки, которой не было: %r" % slot.get("showcase"))
    assert not rep["sent"], "заглушка попала в «выложено»: %r" % rep["sent"]
    assert len(rep["dry_run"]) == 1, (
        "заглушка обязана считаться неотправленной: %r" % rep)
    assert rep["stub"] is True, "отчёт не признаётся, что работал заглушкой"


def test_stub_writes_file_and_says_where():
    """Файл на диск заглушка пишет — и честно называет его в причине."""
    with _sandbox(stub=True) as root:
        plan, slot = _plan()
        res = showcase.send_unit(slot, plan, send=True)
        written = list((root / "showcase_stub").glob("*.txt"))
        assert written, "заглушка не записала файл — тогда она не делает ничего"
        body = written[0].read_text(encoding="utf-8")
    assert written[0].name in res["reason"], (
        "не сказано, куда лёг материал: %r" % res["reason"])
    assert "клиент этого не видел" in body, (
        "сам файл не предупреждает, что это не выкладка:\n%s" % body[:200])


def test_dry_run_still_changes_nothing():
    """Пробный прогон (без --send) статус не трогает — прежнее правило цело."""
    with _sandbox(stub=False):
        plan, slot = _plan()
        with _patched([(showcase, "provider_status", lambda: (True, None))]):
            rep = showcase.run(plan, send=False)
    assert slot["status"] == model.GENERATED
    assert not rep["sent"] and len(rep["dry_run"]) == 1


def test_real_send_marks_showcased():
    """А настоящая отправка статус меняет — иначе мы врали бы в другую сторону."""
    with _sandbox(stub=False):
        plan, slot = _plan()

        def _fake_send(sl, pl, send=False):
            return {"slot": sl["id"], "ok": True, "sent": True, "kind": "text",
                    "chat": "@kanal", "message_ids": [777],
                    "reason": "выложено в витрину"}

        with _patched([(showcase, "send_unit", _fake_send)]):
            rep = showcase.run(plan, send=True)
    assert slot["status"] == model.SHOWCASED, (
        "материал реально ушёл клиенту, а план об этом не знает — вторая "
        "половина того же вранья")
    assert len(rep["sent"]) == 1
    assert slot["showcase"]["message_ids"] == [777]


def test_log_records_the_truth():
    """Журнал витрины пишет sent=False для заглушки — аудит не должен врать."""
    with _sandbox(stub=True) as root:
        plan, _slot = _plan()
        showcase.run(plan, send=True)
        rows = [json.loads(l) for l
                in (root / "showcase_log.jsonl").read_text(
                    encoding="utf-8").splitlines() if l.strip()]
    assert rows and rows[-1]["sent"] is False, (
        "в журнале выкладки заглушка записана как отправка: %r" % rows)


# --- доктор говорит про заглушку вслух ---------------------------------------

def test_doctor_calls_stub_a_degradation():
    """Доктор не имеет права печатать «Витрина настроена» при заглушке."""
    limits, lines = [], []
    with _sandbox(stub=True):
        plan, _slot = _plan()
        with _patched([(showcase, "pending_revisions", lambda *a, **k: []),
                       (showcase, "package_readiness",
                        lambda *a, **k: {"ready": []})]):
            doctor._check_showcase(plan, limits, lines)
    text = "\n".join(lines)
    assert "Витрина настроена" not in text, (
        "доктор объявил витрину настроенной, хотя она пишет в файлы:\n%s" % text)
    assert any("ЗАГЛУШКОЙ" in lim for lim in limits), (
        "заглушка не попала в урезанный режим — владелец не узнает, что "
        "клиенту ничего не показывают: %r" % limits)


def test_doctor_says_ok_when_showcase_is_real():
    """При настоящей витрине доктор по-прежнему говорит «настроена»."""
    limits, lines = [], []
    with _sandbox(stub=False):
        plan, _slot = _plan()
        with _patched([(showcase, "provider_status", lambda: (True, None)),
                       (showcase, "pending_revisions", lambda *a, **k: []),
                       (showcase, "package_readiness",
                        lambda *a, **k: {"ready": []})]):
            doctor._check_showcase(plan, limits, lines)
    assert any("Витрина настроена" in l for l in lines), (
        "рабочую витрину доктор перестал признавать рабочей: %r" % lines)
    assert not limits, "рабочая витрина записана в деградацию: %r" % limits


_TESTS = [
    test_stub_does_not_report_sent,
    test_stub_does_not_change_slot_status,
    test_stub_writes_file_and_says_where,
    test_dry_run_still_changes_nothing,
    test_real_send_marks_showcased,
    test_log_records_the_truth,
    test_doctor_calls_stub_a_degradation,
    test_doctor_says_ok_when_showcase_is_real,
]


if __name__ == "__main__":
    for fn in _TESTS:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn()
        print("  ok — %s" % (fn.__doc__ or fn.__name__).strip().splitlines()[0])
    print("")
    print("OK content_plan/showcase.py: заглушка больше не изображает выкладку "
          "— статус слота, отчёт и журнал говорят «в Telegram не отправлено», а "
          "доктор называет заглушку урезанным режимом.")
