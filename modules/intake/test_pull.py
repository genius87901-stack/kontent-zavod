# -*- coding: utf-8 -*-
"""Тесты приёмки материалов с Диска (`intake/pull.py`, BOX-53).

Что здесь держится под охраной — ровно то, на чём встала первая клиентка
11.08.2026 («папки пустые, а зачем мы туда всё скидывали?»):

    1. видео, бро́шенное в КОРЕНЬ «1 Сырьё», всё равно доезжает до завода —
       раскладывать по подпапкам клиент не обязан;
    2. если клиент всё-таки разложил — его раскладка сильнее нашего умолчания;
    3. повторный заход не заводит те же файлы второй раз (журнал принятого);
    4. файлы клиента на Диске остаются на месте — мы только копируем;
    5. чужие папки («2 На утверждение», «НЕ_ТРОГАТЬ») в завод не затягиваются;
    6. снимок с ЗАГЛАВНЫМ расширением (IMG_1.JPG) не теряется.

Фото проверяются без PIL и без банка: вызов `import_photos.py` подменяется —
тест сторожит МАРШРУТИЗАЦИЮ, а нумерацию кадров держит сам import_photos.

Гоняется двумя способами:
    python3 modules/intake/test_pull.py      # автономно
    pytest modules/intake/test_pull.py       # если стоит pytest
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.intake import drive_layout, pull  # noqa: E402

CONTRACT = drive_layout.load_contract()


def _box(tmp):
    """Коробка завода: корень с маркером и пустыми каталогами движка."""
    box = Path(tmp) / "box"
    (box / "inbox").mkdir(parents=True)
    (box / ".factory_root").write_text("маркер", encoding="utf-8")
    return box


def _drive(tmp, files):
    """Диск клиента со структурой из контракта. files: {отн. путь: содержимое}."""
    gd = Path(tmp) / "GDrive"
    gd.mkdir(parents=True, exist_ok=True)
    drive_layout.ensure_structure(str(gd), client_name="Тест")
    factory = drive_layout.factory_root(gd, CONTRACT)
    for rel, body in files.items():
        p = factory / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
    folders = {k: str(v) for k, v in drive_layout.folder_paths(gd, CONTRACT).items()}
    folders["_factory"] = str(factory)
    return gd, factory, folders


def _fake_bank(calls):
    """Подмена банка фото: запоминает, что ему отдали, и говорит «принял»."""
    def _run(root, staging):
        names = sorted(p.name for p in Path(staging).iterdir())
        calls.append(names)
        return 0, "\n".join("  %d  <- %s" % (i + 1, n) for i, n in enumerate(names))
    return _run


def _pull(box, src, folders, **kw):
    return pull.pull(box, src, CONTRACT, folders, **kw)


# --------------------------------------------------------------------------
def test_video_iz_kornya_syrya_doezzhaet():
    """Куча в корне «1 Сырьё» — главный сценарий. Клиент ничего не раскладывал."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {"1 Сырьё/ролик.mp4": "видео"})
        rep = _pull(box, factory / "1 Сырьё", folders)
        assert rep["taken"].get("talking") == ["ролик.mp4"], rep["taken"]
        assert (box / "talking" / "ролик.mp4").exists(), "видео не доехало в завод"
        assert rep["video_by_default"] == 1, "решение приняли мы — надо сказать вслух"


def test_podpapka_klienta_silnee_umolchaniya():
    """Разложил сам — уважаем: «Видео для вставок» не должно уехать в talking."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {
            "1 Сырьё/Видео для вставок/город.mp4": "б-ролл",
            "1 Сырьё/Говорящие видео/речь.mp4": "речь",
        })
        rep = _pull(box, factory / "1 Сырьё", folders)
        assert rep["taken"].get("video") == ["город.mp4"], rep["taken"]
        assert rep["taken"].get("talking") == ["речь.mp4"], rep["taken"]
        assert rep["video_by_default"] == 0, "клиент разложил сам — умолчание не при чём"


def test_video_as_vstavki_perebivaet_umolchanie():
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {"1 Сырьё/кадр.mov": "вставка"})
        rep = _pull(box, factory / "1 Сырьё", folders, video_default="video")
        assert rep["taken"].get("video") == ["кадр.mov"], rep["taken"]
        assert (box / "video" / "кадр.mov").exists()


def test_povtorny_zahod_ne_dublirует():
    """Журнал принятого. Без него банк фото плодил бы дубли на каждом заходе."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {"1 Сырьё/ролик.mp4": "видео"})
        first = _pull(box, factory / "1 Сырьё", folders)
        second = _pull(box, factory / "1 Сырьё", folders)
        assert sum(len(v) for v in first["taken"].values()) == 1
        assert sum(len(v) for v in second["taken"].values()) == 0, second["taken"]
        assert second["skipped_known"] == 1
        assert len(list((box / "talking").iterdir())) == 1, "копия завелась дважды"
        assert second["video_by_default"] == 0, (
            "повторный заход ничего не взял, а отчёт снова говорит «взял как "
            "говорящее» — сбивает клиента, будто только что было решение")


def test_fajly_na_diske_ne_tronuty():
    """Обещание клиенту: «ваши файлы остались на своих местах»."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {
            "1 Сырьё/ролик.mp4": "видео",
            "1 Сырьё/Музыка/трек.mp3": "музыка",
        })
        before = sorted(p.relative_to(factory).as_posix()
                        for p in factory.rglob("*") if p.is_file())
        _pull(box, factory / "1 Сырьё", folders)
        after = sorted(p.relative_to(factory).as_posix()
                       for p in factory.rglob("*") if p.is_file())
        assert before == after, "на Диске что-то переехало или пропало"


def test_chuzhie_papki_ne_zatyagivayutsya():
    """`--from` на весь «КонтентЗавод»: черновики и служебное — не сырьё."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {
            "1 Сырьё/ролик.mp4": "видео",
            "2 На утверждение/черновик.mp4": "черновик завода",
            "НЕ_ТРОГАТЬ/Черновики движка/кусок.mp4": "полуфабрикат",
        })
        rep = _pull(box, factory, folders)
        assert rep["taken"].get("talking") == ["ролик.mp4"], rep["taken"]
        assert not (box / "talking" / "черновик.mp4").exists()
        assert not (box / "talking" / "кусок.mp4").exists()


def test_zaglavnoe_rasshirenie_ne_teryaetsya():
    """IMG_1.JPG с зеркалки: import_photos ищет «*.jpg», на маке glob чувствителен
    к регистру — поэтому кадры готовим с расширением в нижнем регистре."""
    calls = []
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {"1 Сырьё/IMG_1.JPG": "кадр"})
        real, pull._import_photos = pull._import_photos, _fake_bank(calls)
        try:
            rep = _pull(box, factory / "1 Сырьё", folders)
        finally:
            pull._import_photos = real
        assert calls == [["IMG_1.jpg"]], calls
        assert rep["taken"].get("photos") == ["IMG_1.JPG"], rep["taken"]
        assert rep["photo_numbers"] == [1], rep["photo_numbers"]


def test_teksty_ostayutsya_na_diske():
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {"1 Сырьё/Тексты и заметки/идея.txt": "мысль"})
        rep = _pull(box, factory / "1 Сырьё", folders)
        assert rep["notes_left"] == 1, rep
        assert not rep["taken"], rep["taken"]


def test_svezhaya_korobka_bez_papki_photos():
    """Первый в жизни клиент, первое фото: photos/ ещё нет — банк должен её завести.

    Найдено боем 11.08.2026: настоящий import_photos.py каталог не создаёт и
    падал FileNotFoundError на этом самом сценарии. import_photos.py не правим
    (в незакоммиченной правке на этой ветке) — подстраховка живёт в pull.py.
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        assert not (box / "photos").exists()
        gd, factory, folders = _drive(tmp, {"1 Сырьё/кадр.jpg": "кадр"})
        calls = []
        real, pull._import_photos = pull._import_photos, _fake_bank(calls)
        try:
            _pull(box, factory / "1 Сырьё", folders)
        finally:
            pull._import_photos = real
        assert (box / "photos").is_dir(), "банк фото не завёлся на пустом месте"


def test_podskazki_i_musor_propuskayutsya():
    """«ЧТО СЮДА КЛАСТЬ.txt» кладём мы сами — материалом он не является."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {"1 Сырьё/.DS_Store": "мусор"})
        rep = _pull(box, factory / "1 Сырьё", folders)
        assert rep["notes_left"] == 0 and not rep["taken"] and not rep["unknown"], rep


def test_dry_run_nichego_ne_pishet():
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {"1 Сырьё/ролик.mp4": "видео"})
        rep = _pull(box, factory / "1 Сырьё", folders, dry_run=True)
        assert rep["taken"].get("talking") == ["ролик.mp4"]
        assert not (box / "talking").exists(), "пробный запуск создал файлы"
        assert not (box / pull.LEDGER_REL).exists(), "пробный запуск записал журнал"


def test_odnoimennye_fajly_ne_zatirayut_drug_druga():
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {
            "1 Сырьё/ролик.mp4": "первое",
            "1 Сырьё/Говорящие видео/ролик.mp4": "второе",
        })
        _pull(box, factory / "1 Сырьё", folders)
        got = sorted(p.name for p in (box / "talking").iterdir())
        assert got == ["ролик.mp4", "ролик_2.mp4"], got


def test_neizvestny_tip_ne_trogaem():
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {"1 Сырьё/архив.zip": "непонятно"})
        rep = _pull(box, factory / "1 Сырьё", folders)
        assert rep["unknown"] == ["архив.zip"], rep["unknown"]
        assert not rep["taken"]


def test_otchet_govorit_po_russki():
    """Отчёт читает клиент, а не оператор: ни одного технического слова."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {"1 Сырьё/ролик.mp4": "видео"})
        rep = _pull(box, factory / "1 Сырьё", folders)
        text = pull.render(rep, "talking")
        assert "Взял в работу: 1 говорящее видео." in text, text
        assert "остались на своих местах" in text, text
        assert "--video-as вставки" in text, text
        assert "Traceback" not in text


def test_zhurnal_perezhivaet_perezapusk():
    """Журнал — файл на диске коробки, а не память процесса."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp)
        gd, factory, folders = _drive(tmp, {"1 Сырьё/ролик.mp4": "видео"})
        _pull(box, factory / "1 Сырьё", folders)
        data = json.loads((box / pull.LEDGER_REL).read_text(encoding="utf-8"))
        assert len(data) == 1
        assert list(data.values())[0]["dest"] == "talking", data


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok  %s" % name)


if __name__ == "__main__":
    _run_all()
    print("OK intake/pull.py: съёмка доезжает с Диска в завод — и кучей, и "
          "разложенная; повторный заход не плодит дублей; файлы клиента на "
          "Диске не двигаются")
