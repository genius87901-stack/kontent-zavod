# -*- coding: utf-8 -*-
"""Регресс-тест: строка про ffmpeg в niche-hooks не должна гореть всегда.

ЧТО ЛОВИМ. Доктор искал ffmpeg через PATH (`shutil.which`). В коробке его там
нет НИКОГДА: системный ffmpeg не ставится, завод рендерит бинарём из пакета
imageio-ffmpeg. Из-за этого строка «Нет системного ffmpeg» стояла в 100% отчётов
со всех машин, включая полностью здоровые, и в суточную сводку владельца каждый
раз уезжала категория `no_ffmpeg`. Строка, которая всегда красная, перестаёт
читаться — за ней уже не видно настоящей беды.

Чинить это молчанием было нельзя: если ffmpeg нет НИГДЕ, сказать об этом надо.
Поэтому тест проверяет обе стороны: есть пакет — не шумим; нет нигде — говорим.

Гоняется двумя способами:
    python modules/niche_hooks/test_doctor.py      # автономно
    pytest modules/niche_hooks/test_doctor.py      # если стоит pytest
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from modules.niche_hooks import doctor  # noqa: E402

_HAS_PIP_FFMPEG = importlib.util.find_spec("imageio_ffmpeg") is not None


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


def _run():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = doctor.main()
    return code, buf.getvalue()


def test_ffmpeg_from_package_is_found():
    """ffmpeg из пакета imageio-ffmpeg считается найденным, даже если PATH пуст."""
    if not _HAS_PIP_FFMPEG:
        print("    (пропуск: imageio_ffmpeg не установлен в этом окружении)")
        return
    with _patched([(doctor.shutil, "which", lambda *_a, **_k: None),
                   (doctor, "_bin_ok", lambda *_a, **_k: False)]):
        assert doctor._ffmpeg_where() == "pip", (
            "бинарь ffmpeg лежит в пакете, которым рендерит весь завод, а "
            "доктор его не видит — и объявляет здоровую коробку урезанной")


def test_no_permanent_ffmpeg_noise_on_healthy_box():
    """Пакет есть → ffmpeg НЕ попадает в список нехватки и не красит отчёт."""
    if not _HAS_PIP_FFMPEG:
        print("    (пропуск: imageio_ffmpeg не установлен в этом окружении)")
        return
    with _patched([(doctor.shutil, "which", lambda *_a, **_k: None),
                   (doctor, "_bin_ok", lambda name, *_a, **_k: name == "yt-dlp"),
                   (doctor, "_watch_skill_present", lambda: True)]):
        code, out = _run()
    assert "не хватает: " not in out, (
        "на машине, где есть и yt-dlp, и /watch, и ffmpeg (пакетом), доктор "
        "всё равно жалуется:\n%s" % out)
    assert code == 0, "код %s при полном комплекте инструментов:\n%s" % (code, out)


def test_category_for_owner_is_not_no_ffmpeg():
    """Категория в сводке владельцу перестаёт быть вечным `no_ffmpeg`.

    Проверяем сквозным путём: берём НАСТОЯЩИЙ текст доктора при нехватке
    yt-dlp и /watch (ffmpeg на месте пакетом) и прогоняем его через тот самый
    классификатор, что кладёт одно слово в суточную сводку.
    """
    if not _HAS_PIP_FFMPEG:
        print("    (пропуск: imageio_ffmpeg не установлен в этом окружении)")
        return
    sys.path.insert(0, str(REPO))
    import health_report as hr  # noqa: WPS433

    with _patched([(doctor.shutil, "which", lambda *_a, **_k: None),
                   (doctor, "_bin_ok", lambda *_a, **_k: False),
                   (doctor, "_watch_skill_present", lambda: False)]):
        code, out = _run()
    assert code == 2, "нет yt-dlp и /watch — это урезанный режим (2), а не %s" % code
    assert hr.category(code, out) != "no_ffmpeg", (
        "владельцу опять уезжает `no_ffmpeg`, хотя ffmpeg на машине есть, а не "
        "хватает yt-dlp и /watch:\n%s" % out)


def test_missing_everywhere_is_still_said_out_loud():
    """Если ffmpeg нет НИГДЕ — доктор обязан сказать это, а не промолчать."""
    with _patched([(doctor, "_ffmpeg_where", lambda: ""),
                   (doctor, "_bin_ok", lambda *_a, **_k: True),
                   (doctor, "_watch_skill_present", lambda: True)]):
        code, out = _run()
    assert code == 2, "отсутствующий ffmpeg — урезанный режим (2), а не %s" % code
    assert "ffmpeg не найден" in out, (
        "нехватку ffmpeg замолчали — это враньё в другую сторону:\n%s" % out)
    assert "imageio-ffmpeg" in out, "не подсказали штатный способ починки"


_TESTS = [
    test_ffmpeg_from_package_is_found,
    test_no_permanent_ffmpeg_noise_on_healthy_box,
    test_category_for_owner_is_not_no_ffmpeg,
    test_missing_everywhere_is_still_said_out_loud,
]


if __name__ == "__main__":
    for fn in _TESTS:
        fn()
        print("  ok — %s" % (fn.__doc__ or fn.__name__).strip().splitlines()[0])
    print("")
    print("OK niche_hooks/doctor.py: ffmpeg ищется там, где он реально лежит; "
          "вечная строка `no_ffmpeg` в сводке владельца больше не горит на "
          "здоровых машинах, а настоящая нехватка по-прежнему называется вслух.")
