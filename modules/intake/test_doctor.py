# -*- coding: utf-8 -*-
"""Регресс-тест find_root() у intake — приёмки фото/видео с Google Диска.

Корневая причина бага (найден 11.08.2026, бил по ВСЕМ шести клиентам): корень
установки искали по `CONTRACT.md`, а он в коробку не едет — это документ
разработки, `build_box` его не возит. У клиента маркер не находился ни разу, и
срабатывал запасной вариант — а он был сдвинут на уровень: сюда приходит путь
ФАЙЛА `modules/intake/doctor.py`, значит `parents[1]` — это папка `modules`.
Корнем объявлялась она, `config/factory.config.json` там отсутствует, и intake
отвечал «не указана папка Google Диска» даже тогда, когда она указана.

Почему тест нужен именно здесь: правка уже один раз жила только в стенде и
пропала при обновлении (`import_photos.py`, 11.08.2026) — незакоммиченное
молча откатывается патчем. Тест ловит откат.

Гоняется двумя способами:
    python3 modules/intake/test_doctor.py      # автономно
    pytest modules/intake/test_doctor.py       # если стоит pytest
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import shutil
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.intake import doctor  # noqa: E402

GDRIVE = '{"gdrive_root": "/tmp/gdrive", "client_name": "Тестовый клиент"}'


def _make_box(tmp: str, *, contract: bool, factory_root: bool) -> Path:
    """Раскладка коробки: корень / modules / intake / doctor.py + config."""
    box = Path(tmp) / "КонтентЗавод"
    (box / "modules" / "intake").mkdir(parents=True)
    (box / "config").mkdir()
    (box / "config" / "factory.config.json").write_text(GDRIVE, encoding="utf-8")
    if contract:
        (box / "CONTRACT.md").write_text("контракт", encoding="utf-8")
    if factory_root:
        (box / ".factory_root").write_text("маркер корня", encoding="utf-8")
    f = box / "modules" / "intake" / "doctor.py"
    f.write_text("", encoding="utf-8")
    return box


def test_client_box_root_found_by_factory_root():
    """Клиентская коробка: CONTRACT.md нет, есть .factory_root от установщика."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _make_box(tmp, contract=False, factory_root=True)
        got = doctor.find_root((box / "modules" / "intake" / "doctor.py").resolve())
        assert got == box.resolve(), f"корнем объявлено {got}, а не коробка {box}"
        assert (got / "config" / "factory.config.json").exists(), (
            "конфиг по найденному корню не читается — клиент увидит "
            "«не указана папка Google Диска» при указанной папке")


def test_owner_repo_root_found_by_contract():
    """Репозиторий владельца: .factory_root нет, есть CONTRACT.md."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _make_box(tmp, contract=True, factory_root=False)
        got = doctor.find_root((box / "modules" / "intake" / "doctor.py").resolve())
        assert got == box.resolve(), f"корнем объявлено {got}, а не корень {box}"


def test_fallback_is_box_root_not_modules_dir():
    """Ни одного маркера: запасной вариант обязан дать корень, а не `modules`.

    Это ровно тот случай, что бил у клиентов: `parents[1]` вместо `parents[2]`.
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _make_box(tmp, contract=False, factory_root=False)
        got = doctor.find_root((box / "modules" / "intake" / "doctor.py").resolve())
        assert got.name != "modules", (
            "запасной вариант объявил корнем папку modules — конфига там нет")
        assert got == box.resolve(), f"корнем объявлено {got}, а не коробка {box}"


# ==========================================================================
# Коды возврата (CONTRACT.md §2) и гонка за пробным файлом.
#
# Зачем: до 21.08.2026 у doctor было шесть разных `return 1` и ни одного `2`.
# Собственный вывод говорил «всё остальное работает», а код 1 уходил владельцу
# красным «🔴 СРОЧНО» — ровно тот случай, который контракт §2 называет причиной
# появления кода 2. Плюс can_write() писал файл с ФИКСИРОВАННЫМ именем, и две
# одновременные проверки убивали пробу друг друга (клиентские логи 17.08.2026
# 19:54:40Z: три отчёта за секунду, два «no_write», один «ok»).
# ==========================================================================
from modules.intake import drive_layout  # noqa: E402
from unittest import mock  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_config(box: Path, payload: str) -> None:
    """Кладёт config/factory.config.json с заданным содержимым."""
    (box / "config").mkdir(parents=True, exist_ok=True)
    (box / "config" / "factory.config.json").write_text(payload, encoding="utf-8")


@contextlib.contextmanager
def _root_is(box: Path):
    """Заставляет doctor считать корнем установки временную коробку."""
    original = doctor.find_root
    doctor.find_root = lambda start: box
    try:
        yield
    finally:
        doctor.find_root = original


def _run_doctor(box: Path, argv=()):
    """Гоняет doctor.main() на коробке. Возвращает (код возврата, что напечатал)."""
    buf = io.StringIO()
    with _root_is(box), contextlib.redirect_stdout(buf):
        code = doctor.main(list(argv))
    return code, buf.getvalue()


def _gdrive_box(tmp: str, gdrive_value: str) -> Path:
    """Коробка завода с указанной в конфиге папкой Google Диска."""
    box = Path(tmp) / "коробка"
    # json.dumps — пути под Windows содержат обратные слэши, руками не склеить.
    _write_config(box, json.dumps(
        {"gdrive_root": gdrive_value, "client_name": "Тестовый клиент"},
        ensure_ascii=False))
    return box


def _full_drive(tmp: str) -> tuple[Path, Path]:
    """Коробка + полностью разложенный по контракту Google Диск."""
    gdrive = Path(tmp) / "Мой диск"
    gdrive.mkdir(parents=True)
    drive_layout.ensure_structure(gdrive, client_name="Тестовый клиент")
    return _gdrive_box(tmp, str(gdrive)), gdrive


def test_exit_codes_match_contract():
    """Константы кодов — те самые цифры из CONTRACT.md §2."""
    assert doctor.EXIT_OK == 0, "полностью работоспособен — это 0"
    assert doctor.EXIT_LIMITED == 2, "урезанный режим — это 2, а не 1"
    assert doctor.EXIT_NOT_READY == 1, "не готов — это 1"


def test_gdrive_not_set_is_code_2():
    """Папка Диска не указана → 2 (клиент ещё не настроил, завод работает)."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _gdrive_box(tmp, "")
        code, out = _run_doctor(box)
        assert code == 2, (
            f"код {code}: незаполненный ключ конфига — урезанный режим, "
            "а не красная тревога (CONTRACT.md §2)")
        assert "всё остальное работает" in out, "текст для человека испортился"


def test_no_red_alarm_together_with_it_is_not_a_breakage_phrase():
    """Вывод «всё остальное работает» и код 1 несовместимы — это и был баг."""
    with tempfile.TemporaryDirectory() as tmp:
        code, out = _run_doctor(_gdrive_box(tmp, ""))
        assert not ("работает" in out and code == 1), (
            "doctor пишет «работает» и одновременно просит «🔴 СРОЧНО» — "
            "дословно случай из CONTRACT.md §2")


def test_gdrive_path_missing_is_code_2():
    """Папка указана, но её нет на диске → 2 (опечатка в пути, не поломка)."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _gdrive_box(tmp, str(Path(tmp) / "нет-такой-папки"))
        code, out = _run_doctor(box)
        assert code == 2, f"код {code}: опечатка в пути — не повод для «СРОЧНО»"
        assert "на этом компьютере её нет" in out


def test_factory_folder_missing_is_code_2():
    """Нет папки «КонтентЗавод» → 2, чинится одной командой установщика."""
    with tempfile.TemporaryDirectory() as tmp:
        gdrive = Path(tmp) / "Мой диск"
        gdrive.mkdir(parents=True)
        code, out = _run_doctor(_gdrive_box(tmp, str(gdrive)))
        assert code == 2, f"код {code}: донастройка одной командой — это 2"
        assert "drive_layout" in out, "не подсказали команду починки"


def test_missing_subfolders_is_code_2():
    """Не хватает подпапок и чинить запрещено → 2, та же одна команда."""
    with tempfile.TemporaryDirectory() as tmp:
        gdrive = Path(tmp) / "Мой диск"
        (gdrive / "КонтентЗавод").mkdir(parents=True)
        code, out = _run_doctor(_gdrive_box(tmp, str(gdrive)), ["--no-repair"])
        assert code == 2, f"код {code}: неполная раскладка — урезанный режим"
        assert "не хватает папок" in out


def test_missing_subfolders_repaired_by_doctor():
    """BOX-72. Не хватает папок → доктор заводит их САМ и приём готов.

    Живой случай 01.09.2026: патч 1.3.44 привёз контракт v3 с двумя новыми
    папками, у трёх клиентов приём упал в урезанный режим и это был их
    ЕДИНСТВЕННЫЙ работавший модуль. Лечилось командой, о которой никто не узнал.
    """
    with tempfile.TemporaryDirectory() as tmp:
        gdrive = Path(tmp) / "Мой диск"
        (gdrive / "КонтентЗавод").mkdir(parents=True)
        code, out = _run_doctor(_gdrive_box(tmp, str(gdrive)))
        assert code == 0, "доктор обязан был завести папки сам: " + out
        assert "завёл их сам" in out, out
        contract = drive_layout.load_contract()
        for path in drive_layout.folder_paths(gdrive, contract).values():
            assert path.exists(), f"папка не заведена: {path}"


def test_repair_creates_exactly_the_new_v3_folders():
    """Тот самый переход 1.3.42 -> 1.3.44: было по контракту v2, стало v3."""
    with tempfile.TemporaryDirectory() as tmp:
        gdrive = Path(tmp) / "Мой диск"
        gdrive.mkdir(parents=True)
        drive_layout.ensure_structure(gdrive, client_name="Тестовый клиент")
        новые = [gdrive / "КонтентЗавод" / "1 Сырьё" / "Длинные видео",
                 gdrive / "КонтентЗавод" / "1 Сырьё" / "Нарезка из эфиров"]
        for d in новые:
            if d.exists():
                # rmtree, не rmdir: ensure_structure кладёт в каждую папку
                # памятку «ЧТО СЮДА КЛАСТЬ.txt», и rmdir по непустой падал
                # OSError-ом — тест был красным у каждого клиента.
                shutil.rmtree(d)
        code, out = _run_doctor(_gdrive_box(tmp, str(gdrive)))
        assert code == 0, "доктор не довёл структуру: " + out
        for d in новые:
            assert d.exists(), f"новая папка контракта не заведена: {d.name}"


def test_no_repair_does_not_touch_the_disk():
    """Флаг --no-repair оставляет диск нетронутым: чистая диагностика."""
    with tempfile.TemporaryDirectory() as tmp:
        gdrive = Path(tmp) / "Мой диск"
        (gdrive / "КонтентЗавод").mkdir(parents=True)
        было = sorted(p.name for p in (gdrive / "КонтентЗавод").iterdir())
        _run_doctor(_gdrive_box(tmp, str(gdrive)), ["--no-repair"])
        стало = sorted(p.name for p in (gdrive / "КонтентЗавод").iterdir())
        assert было == стало, f"диск изменён: было {было}, стало {стало}"


def test_config_broken_is_code_1():
    """Конфиг не читается → 1: сама проверка не может отработать."""
    with tempfile.TemporaryDirectory() as tmp:
        box = Path(tmp) / "коробка"
        _write_config(box, "{это не json")
        code, out = _run_doctor(box)
        assert code == 1, (
            f"код {code}: проверка не смогла отработать — по контракту это 1")
        assert "Traceback" not in out, "клиенту улетел стектрейс"


def test_no_write_access_is_code_1():
    """Нет прав на запись → 1: обмена через Диск нет, чинит только человек."""
    with tempfile.TemporaryDirectory() as tmp:
        box, _ = _full_drive(tmp)
        with mock.patch.object(doctor, "can_write",
                               return_value=(False, "на диске нет места")):
            code, out = _run_doctor(box)
        assert code == 1, (
            f"код {code}: диск «только для чтения» или кончилось место — "
            "главного пути нет, это настоящая тревога")
        assert "записать в неё не получается" in out


def test_healthy_drive_is_code_0():
    """Диск разложен полностью → 0."""
    with tempfile.TemporaryDirectory() as tmp:
        box, _ = _full_drive(tmp)
        code, out = _run_doctor(box)
        assert code == 0, f"код {code}, а диск в порядке. Вывод:\n{out}"
        assert "Приём материалов готов." in out


def test_unexpected_failure_is_code_1_without_traceback():
    """Непредвиденная поломка проверки → 1 и человеческий текст, не стектрейс."""
    buf = io.StringIO()
    with mock.patch.object(doctor, "main", side_effect=RuntimeError("шёл дождь")):
        with contextlib.redirect_stdout(buf):
            code = doctor._run()
    out = buf.getvalue()
    assert code == 1, f"код {code}: doctor упал исключением — по контракту 1"
    assert "Traceback" not in out and "шёл дождь" in out


# --- гонка за пробным файлом -------------------------------------------------
def test_probe_name_is_unique_and_carries_pid():
    """Имя пробного файла своё у каждого вызова и содержит PID процесса."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        first, second = doctor._probe_path(folder), doctor._probe_path(folder)
        assert first != second, (
            "имя пробного файла повторяется — две проверки снова подерутся")
        assert str(os.getpid()) in first.name, (
            "в имени нет PID: пробы разных процессов могут совпасть")


def test_can_write_parallel_threads_all_true():
    """16 потоков одновременно проверяют одну папку — все обязаны получить True."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        results: list[tuple[bool, str | None]] = []
        lock = threading.Lock()
        start = threading.Barrier(16)

        def worker():
            start.wait()
            for _ in range(40):
                ok, err = doctor.can_write(folder)
                if not ok:
                    with lock:
                        results.append((ok, err))
                    return
            with lock:
                results.append((True, None))

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        bad = [r for r in results if not r[0]]
        assert not bad, (
            "при параллельной проверке папки, куда писать МОЖНО, часть потоков "
            f"ответила «нельзя»: {bad[:3]}")
        assert len(results) == 16, "не все потоки отчитались"

        leftovers = [p.name for p in folder.iterdir()]
        assert not leftovers, f"пробные файлы не убраны: {leftovers[:3]}"


def _PARALLEL_CHILD() -> str:
    """Код ребёнка: долбит can_write по общей папке и печатает OK/FAIL."""
    return (
        "import sys, pathlib\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from modules.intake import doctor\n"
        "folder = pathlib.Path(sys.argv[2])\n"
        "bad = ''\n"
        "for _ in range(80):\n"
        "    ok, err = doctor.can_write(folder)\n"
        "    if not ok:\n"
        "        bad = err or 'без причины'\n"
        "        break\n"
        "print('FAIL: ' + bad if bad else 'OK')\n"
    )


def test_can_write_parallel_processes_all_true():
    """Три ПРОЦЕССА разом проверяют одну папку — как в логах 17.08.2026."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "КонтентЗавод"
        folder.mkdir(parents=True)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", _PARALLEL_CHILD(), str(REPO_ROOT), str(folder)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            for _ in range(3)
        ]
        outs = []
        for p in procs:
            out, err = p.communicate(timeout=120)
            outs.append((p.returncode,
                         out.decode("utf-8", "replace").strip(),
                         err.decode("utf-8", "replace").strip()))
        for rc, out, err in outs:
            assert rc == 0, f"ребёнок упал (код {rc}): {err}"
            assert out == "OK", (
                "параллельная проверка записываемой папки дала отказ — "
                f"вернулась та самая гонка: {out}")


def test_can_write_survives_vanished_probe():
    """Пробу удалили за нас (FileNotFoundError) — запись всё равно прошла."""
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(Path, "unlink", side_effect=FileNotFoundError()):
            ok, err = doctor.can_write(Path(tmp))
        assert ok, f"исчезнувшая проба принята за отказ в правах: {err}"
        assert err is None


def test_can_write_does_not_swallow_real_permission_error():
    """Настоящая ошибка прав при уборке не проглатывается."""
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(Path, "unlink",
                               side_effect=PermissionError("доступ запрещён")):
            ok, err = doctor.can_write(Path(tmp))
        assert not ok, "ошибку прав проглотили — doctor обязан её ловить"
        assert "доступ запрещён" in (err or ""), f"причина потеряна: {err}"


def test_can_write_reports_unwritable_folder():
    """Папки нет вовсе — писать некуда, честный отказ с причиной."""
    with tempfile.TemporaryDirectory() as tmp:
        ok, err = doctor.can_write(Path(tmp) / "нет-такой-папки")
        assert not ok and err, "несуществующая папка признана записываемой"


_NEW_TESTS = [
    test_exit_codes_match_contract,
    test_gdrive_not_set_is_code_2,
    test_no_red_alarm_together_with_it_is_not_a_breakage_phrase,
    test_gdrive_path_missing_is_code_2,
    test_factory_folder_missing_is_code_2,
    test_missing_subfolders_is_code_2,
    test_config_broken_is_code_1,
    test_no_write_access_is_code_1,
    test_healthy_drive_is_code_0,
    test_unexpected_failure_is_code_1_without_traceback,
    test_probe_name_is_unique_and_carries_pid,
    test_can_write_parallel_threads_all_true,
    test_can_write_parallel_processes_all_true,
    test_can_write_survives_vanished_probe,
    test_can_write_does_not_swallow_real_permission_error,
    test_can_write_reports_unwritable_folder,
]


def _run_all():
    test_client_box_root_found_by_factory_root()
    test_owner_repo_root_found_by_contract()
    test_fallback_is_box_root_not_modules_dir()


def _run_new():
    """Гоняет новые тесты по одному, чтобы в выводе было видно каждый."""
    for fn in _NEW_TESTS:
        fn()
        first_line = (fn.__doc__ or fn.__name__).strip().splitlines()[0]
        print(f"  ok — {first_line}")


if __name__ == "__main__":
    _run_all()
    print("OK intake/doctor.py: корень установки находится и в клиентской "
          "коробке (.factory_root), и в репозитории (CONTRACT.md), "
          "и без маркеров — папка modules корнем не объявляется")
    print("")
    print("Коды возврата (CONTRACT.md §2) и гонка за пробным файлом:")
    _run_new()
    print("")
    print("OK intake/doctor.py: урезанный режим отдаёт 2, а красное «СРОЧНО» "
          "остаётся только за настоящей поломкой; can_write выдерживает "
          "параллельные проверки и не проглатывает ошибки прав")
