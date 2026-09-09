# -*- coding: utf-8 -*-
"""Тесты возврата готового на Диск (`intake/push.py`).

Обратной дороги «завод → Диск» в проекте не было вовсе, и первое, что этот код
может сделать не так, — испортить клиенту его же папку. Поэтому под охраной:

    1. файл ложится РОВНО в папку контракта, а не куда придётся;
    2. одноимённый файл клиента НЕ затирается — рядом ложится «имя_2»;
    3. повторный заход не плодит вторую копию (журнал отданного);
    4. журнал переживает перезапуск — это файл, а не память процесса;
    5. в клиентские папки («1 Сырьё/Фото» и прочие) класть нельзя вообще:
       разрешены только цели с owner=factory;
    6. пробный запуск не пишет ни байта.

Ни ffmpeg, ни сеть здесь не нужны: «Диск» — обычная временная папка, ровно как
его видит синхронизатор Google на машине клиента.

Гоняется двумя способами:
    python3 modules/intake/test_push.py       # автономно
    pytest modules/intake/test_push.py        # если стоит pytest
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.intake import drive_layout, push  # noqa: E402

CONTRACT = drive_layout.load_contract()


def _stand(tmp):
    """Коробка завода + пустая папка «Google Диска» + два готовых куска."""
    box = Path(tmp) / "box"
    (box / "inbox").mkdir(parents=True)
    gd = Path(tmp) / "drive"
    gd.mkdir()
    src = Path(tmp) / "out_longcut" / "эфир"
    src.mkdir(parents=True)
    (src / "01_про_страх.mp4").write_bytes(b"video-1")
    (src / "01_про_страх.txt").write_text("ТЕМА: про страх", encoding="utf-8")
    return box, gd, src


def _files(src):
    return sorted(p for p in src.iterdir() if p.is_file())


def test_kladet_v_papku_kontrakta():
    """Нарезка уезжает в «1 Сырьё/Нарезка из эфиров», а не куда придётся."""
    with tempfile.TemporaryDirectory() as tmp:
        box, gd, src = _stand(tmp)
        rep = push.push(box, _files(src), gd, target="cuts", subdir="эфир")
        dest = Path(rep["dest"])
        assert dest.parts[-3:] == ("1 Сырьё", "Нарезка из эфиров", "эфир"), dest
        assert (dest / "01_про_страх.mp4").exists(), list(dest.iterdir())
        assert len(rep["put"]) == 2, rep


def test_chuzhoi_fail_ne_zatiraetsya():
    """У клиента уже лежит файл с тем же именем — кладём рядом, не поверх."""
    with tempfile.TemporaryDirectory() as tmp:
        box, gd, src = _stand(tmp)
        dest = push.target_dir(gd, "cuts", "эфир")
        dest.mkdir(parents=True)
        chuzhoi = dest / "01_про_страх.mp4"
        chuzhoi.write_text("это положил клиент", encoding="utf-8")
        push.push(box, _files(src), gd, target="cuts", subdir="эфир")
        assert chuzhoi.read_text(encoding="utf-8") == "это положил клиент", \
            "затёрли чужое"
        assert (dest / "01_про_страх_2.mp4").exists(), list(dest.iterdir())


def test_povtornyi_zahod_ne_plodit_kopii():
    with tempfile.TemporaryDirectory() as tmp:
        box, gd, src = _stand(tmp)
        push.push(box, _files(src), gd, target="cuts", subdir="эфир")
        rep2 = push.push(box, _files(src), gd, target="cuts", subdir="эфир")
        assert rep2["put"] == [], rep2
        assert rep2["skipped_known"] == 2, rep2
        dest = push.target_dir(gd, "cuts", "эфир")
        assert len(list(dest.iterdir())) == 2, list(dest.iterdir())


def test_zhurnal_perezhivaet_perezapusk():
    """Журнал — файл на диске коробки, а не память процесса."""
    with tempfile.TemporaryDirectory() as tmp:
        box, gd, src = _stand(tmp)
        push.push(box, _files(src), gd, target="cuts", subdir="эфир")
        data = json.loads((box / push.LEDGER_REL).read_text(encoding="utf-8"))
        assert len(data) == 2, data
        assert all(v["target"] == "cuts" for v in data.values()), data


def test_v_klientskuyu_papku_klast_nelzya():
    """Цель вне списка owner=factory — отказ, а не «положу куда-нибудь»."""
    with tempfile.TemporaryDirectory() as tmp:
        box, gd, src = _stand(tmp)
        for bad in ("raw.photos", "photos", "1 Сырьё"):
            try:
                push.push(box, _files(src), gd, target=bad)
            except ValueError:
                continue
            raise AssertionError("в «%s» класть быть не должно" % bad)


def test_probnyi_zapusk_nichego_ne_pishet():
    with tempfile.TemporaryDirectory() as tmp:
        box, gd, src = _stand(tmp)
        rep = push.push(box, _files(src), gd, target="cuts", subdir="эфир",
                        dry_run=True)
        assert len(rep["put"]) == 2, rep
        assert not push.target_dir(gd, "cuts", "эфир").exists(), "создал папку"
        assert not (box / push.LEDGER_REL).exists(), "записал журнал"


def test_net_faila_ne_ronyaet_ostalnye():
    """Один файл пропал — остальные всё равно доезжают, о пропаже говорим."""
    with tempfile.TemporaryDirectory() as tmp:
        box, gd, src = _stand(tmp)
        files = _files(src) + [src / "нет-такого.mp4"]
        rep = push.push(box, files, gd, target="cuts", subdir="эфир")
        assert len(rep["put"]) == 2, rep
        assert any("нет файла" in e for e in rep["errors"]), rep


def test_poteryannyi_zhurnal_ne_plodit_dublei():
    """Журнал потеряли, а файлы на месте — второй копии быть не должно.

    Живой случай 28.08.2026: журнал сбросили руками, повторный заход развёл
    16 кусков в 32 («имя_2»). Клиент открывает папку и видит два одинаковых
    ролика — это хуже, чем не сделать ничего."""
    with tempfile.TemporaryDirectory() as tmp:
        box, gd, src = _stand(tmp)
        push.push(box, _files(src), gd, target="cuts", subdir="эфир")
        (box / push.LEDGER_REL).unlink()          # журнал потеряли
        rep = push.push(box, _files(src), gd, target="cuts", subdir="эфир")
        assert rep["put"] == [], rep
        assert rep["skipped_known"] == 2, rep
        dest = push.target_dir(gd, "cuts", "эфир")
        assert len(list(dest.iterdir())) == 2, list(dest.iterdir())


def test_rabochie_faily_zavoda_ne_uezzhayut():
    """В папке нарезки лежит и кухня — контактные листы, пробные рендеры.

    Живой случай 28.08.2026: первый прогон увёз клиенту на Диск
    `ПРОБНЫЙ_РИЛС_04_SHEET.jpg`. Отдаём только пары «видео + карточка»."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "нарезка"
        src.mkdir()
        (src / "01_тема.mp4").write_bytes(b"v")
        (src / "01_тема.txt").write_text("ТЕМА", encoding="utf-8")
        (src / "ПРОБНЫЙ_РИЛС_01_SHEET.jpg").write_bytes(b"jpg")
        (src / "01_тема_transcript.json").write_text("[]", encoding="utf-8")
        (src / "ПРОБНЫЙ_РИЛС_01.mp4").write_bytes(b"v")      # рендер без карточки
        got = {p.name for p in push.collect_dir(src)}
        assert got == {"01_тема.mp4", "01_тема.txt"}, got


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok  %s" % name)


if __name__ == "__main__":
    _run_all()
    print("OK intake/push.py: готовое уезжает в папку контракта, чужое на Диске "
          "не затирается, повторный заход не плодит копий, в клиентские папки "
          "завод не пишет")
