# -*- coding: utf-8 -*-
"""Регресс-тесты доктора engine-media (ядро рендера).

Ловят три беды, из-за которых у живых клиентов зря загоралась красная тревога:

1. ГОНКА ЗА ФИКСИРОВАННЫМ ПУТЁМ JUNCTION. Лечение кириллического пути к
   mediapipe (BOX-61) работало с ОДНОЙ жёстко заданной ссылкой
   <LOCALAPPDATA>/KonsentZavod/mp_safe/mediapipe и сносило её через os.rmdir,
   если она вела не туда. Два параллельных прогона — и один сносит ссылку,
   пока второй по ней импортирует mediapipe. Доказательство из клиентских
   логов: 17.08.2026 19:54:40Z одна копия одной и той же секундой прислала три
   отчёта — engine-media=broken в одном и =ok в двух других.

2. ЗАВЫШЕННЫЕ ТРЕБОВАНИЯ. torch и whisperx лежали в обязательном ядре, хотя
   карусели рендерятся на PIL и numpy. Машина, отлично печатающая карусели,
   помечалась как broken.

3. ДОКТОР НЕ УМЕЛ КОД 2 (`return 1 if PROBLEMS else 0`) — любая мелочь уходила
   владельцу красным «🔴 СРОЧНО» вместо строки суточной сводки (CONTRACT.md §2).

Гоняется двумя способами (как у modules/intake/test_doctor.py):
    python modules/engine_media/test_doctor.py      # автономно
    pytest modules/engine_media/test_doctor.py      # если стоит pytest
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.engine_media import doctor  # noqa: E402


# ── общая оснастка ───────────────────────────────────────────────────────────
def _reset_buckets() -> None:
    """Корзины доктора — модульные списки; перед каждой проверкой чистим."""
    doctor.PROBLEMS.clear()
    doctor.DEGRADED.clear()
    doctor.NOTES.clear()


class _fake_imports:
    """Подменяет doctor._importable: перечисленные пакеты «не ставятся»."""

    def __init__(self, *missing: str):
        self.missing = set(missing)

    def __enter__(self):
        self.real = doctor._importable
        doctor._importable = lambda name: name not in self.missing
        return self

    def __exit__(self, *exc):
        doctor._importable = self.real
        return False


# ── 1. коды возврата: 2 против 1 ─────────────────────────────────────────────
def test_no_torch_is_degraded_not_broken():
    """Нет torch/whisperx — это урезанный режим (код 2), а не поломка.

    Карусели рендерятся на PIL и numpy; речевой слой нужен только разговорным
    Reels и по install/stages.json приезжает лишь на этапе 2.
    """
    _reset_buckets()
    with _fake_imports("torch", "whisperx"):
        doctor.check_packages()
    assert not doctor.PROBLEMS, (
        "отсутствие torch/whisperx уехало в блокирующие: %s" % doctor.PROBLEMS)
    assert doctor.DEGRADED, "про урезанный режим доктор промолчал"
    assert doctor.verdict() == 2, (
        "код возврата %d вместо 2 — владельцу улетит красная тревога"
        % doctor.verdict())
    text = " ".join(doctor.DEGRADED)
    assert "разговорные Reels" in text, (
        "человеку не объяснили по-русски, что именно недоступно: %s" % text)
    assert "Traceback" not in text, "в тексте для человека стектрейс"


def test_no_numpy_or_pillow_is_broken():
    """Нет numpy/pillow — ядро рендера мертво, это код 1."""
    for missing, imp in (("numpy", "numpy"), ("pillow", "PIL")):
        _reset_buckets()
        with _fake_imports(imp):
            doctor.check_packages()
        assert doctor.PROBLEMS, "отсутствие %s не признано блокирующим" % missing
        assert doctor.verdict() == 1, (
            "код %d вместо 1 при отсутствии %s" % (doctor.verdict(), missing))
        assert missing in " ".join(doctor.PROBLEMS)


def test_all_present_is_zero():
    """Всё на месте — код 0, без выдуманных деградаций."""
    _reset_buckets()
    with _fake_imports():
        doctor.check_packages()
    assert not doctor.PROBLEMS and not doctor.DEGRADED
    assert doctor.verdict() == 0


def test_verdict_priority_problem_over_degraded():
    """Есть и то и другое — побеждает 1: чинить надо прямо сейчас."""
    _reset_buckets()
    doctor.DEGRADED.append("нет речевого слоя")
    assert doctor.verdict() == 2
    doctor.PROBLEMS.append("нет numpy")
    assert doctor.verdict() == 1
    _reset_buckets()


# ── 2. junction: уникальность пути ───────────────────────────────────────────
def test_junction_path_unique_per_process():
    """Путь своей ссылки уникален на каждый вызов и несёт PID процесса."""
    safe_root = os.path.join("не", "важно")
    seen = []
    lock = threading.Lock()

    def worker():
        mine = [doctor._mp_own_root(safe_root) for _ in range(50)]
        with lock:
            seen.extend(mine)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == len(set(seen)), (
        "пути ссылок повторились — параллельные проверки снова подерутся за один "
        "каталог (повторов: %d)" % (len(seen) - len(set(seen))))
    pid_mark = "proc-%d-" % os.getpid()
    assert all(os.path.basename(p).startswith(pid_mark) for p in seen), (
        "в имени каталога нет PID — чужой процесс не отличит своё от чужого")


def test_junction_path_unique_across_processes():
    """Два ОТДЕЛЬНЫХ процесса не могут получить один и тот же путь."""
    root = Path(__file__).resolve().parents[2]
    code = ("import sys; sys.path.insert(0, r'%s');"
            "from modules.engine_media import doctor;"
            "print(doctor._mp_own_root('safe'))" % root)
    got = set()
    for _ in range(2):
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, "дочерний прогон упал: %s" % r.stderr[:300]
        got.add(r.stdout.strip())
    assert len(got) == 2, "разные процессы получили один путь: %s" % got


def test_link_validity_check():
    """Годной считается только ссылка, ведущая ровно в нужный каталог."""
    with tempfile.TemporaryDirectory() as tmp:
        real = Path(tmp) / "real"
        other = Path(tmp) / "other"
        real.mkdir()
        other.mkdir()
        assert doctor._mp_link_is_valid(str(real), str(real)) is True
        assert doctor._mp_link_is_valid(str(other), str(real)) is False
        assert doctor._mp_link_is_valid(str(Path(tmp) / "нет"), str(real)) is False


def test_parallel_import_survives_foreign_link():
    """ГЛАВНЫЙ тест гонки (только Windows — на других ОС лечение не нужно).

    Сцена ровно та, что валила клиента: общая ссылка на месте, но ведёт НЕ ТУДА
    (копию завода переставили), и одновременно бегут несколько проверок. Старый
    код сносил общую ссылку через os.rmdir и создавал заново — сосед в этот миг
    получал FileNotFoundError на импорте. Требуем: каждый параллельный вызов
    получает РАБОЧИЙ путь, а чужую ссылку никто не сносит.
    """
    if sys.platform != "win32":
        print("  (пропуск test_parallel_import_survives_foreign_link: "
              "лечение junction нужно только на Windows)")
        return

    import importlib.util

    class _Spec:
        def __init__(self, d):
            self.submodule_search_locations = [d]

    tmp = tempfile.mkdtemp()
    real_spec = importlib.util.find_spec
    saved_path = list(sys.path)
    saved_local = os.environ.get("LOCALAPPDATA")
    try:
        # Кириллица в пути — иначе доктор законно ничего не делает.
        real_dir = Path(tmp) / "КонтентЗавод" / "site-packages" / "mediapipe"
        real_dir.mkdir(parents=True)
        (real_dir / "marker.txt").write_text("пакет на месте", encoding="utf-8")
        stale = Path(tmp) / "старая_копия" / "mediapipe"
        stale.mkdir(parents=True)

        os.environ["LOCALAPPDATA"] = str(Path(tmp) / "local")
        safe_root = doctor._mp_safe_root()
        os.makedirs(safe_root, exist_ok=True)
        shared = os.path.join(safe_root, "mediapipe")
        assert doctor._mp_make_link(safe_root, shared, str(stale)), (
            "не удалось создать junction — тест не воспроизводит сцену")

        def fake_find_spec(name, *a, **kw):
            if name == "mediapipe":
                return _Spec(str(real_dir))
            return real_spec(name, *a, **kw)

        importlib.util.find_spec = fake_find_spec

        errors = []

        def worker():
            for _ in range(5):
                try:
                    root = doctor._mediapipe_ascii_safe_import()
                    if not root:
                        errors.append("лечение не отработало вовсе")
                        continue
                    probe = os.path.join(root, "mediapipe", "marker.txt")
                    if not os.path.exists(probe):
                        errors.append("ссылка не ведёт к пакету: %s" % probe)
                except Exception as ex:      # ровно то, что видел клиент
                    errors.append("исключение при импорте: %s" % ex)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, "параллельные проверки подрались: %s" % errors[:3]
        assert doctor._mp_link_is_valid(shared, str(stale)), (
            "чужая (устаревшая) общая ссылка снесена — именно этим доктор и "
            "ронял импорт у соседнего процесса")
        assert doctor._MP_OWN_ROOTS, "свои каталоги не заведены"
        assert len(set(doctor._MP_OWN_ROOTS)) == len(doctor._MP_OWN_ROOTS), (
            "свои каталоги повторяются")

        # Убираем за собой: свои ссылки снимаются, чужая остаётся жить.
        doctor._mp_cleanup()
        assert not doctor._MP_OWN_ROOTS, "уборка не сняла свои ссылки"
        assert os.path.lexists(shared), "уборка задела чужую ссылку"
        assert (real_dir / "marker.txt").exists(), (
            "уборка ушла ВНУТРЬ ссылки и снесла настоящий пакет — это должен "
            "быть os.rmdir по самой ссылке, а не rmtree")
    finally:
        importlib.util.find_spec = real_spec
        sys.path[:] = saved_path
        if saved_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = saved_local
        doctor._mp_cleanup()
        # Сначала снимаем junction'ы, потом уже сносим дерево.
        for name in ("mediapipe",):
            doctor._mp_drop_link(os.path.join(doctor._mp_safe_root(), name))
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all():
    for fn in (
        test_no_torch_is_degraded_not_broken,
        test_no_numpy_or_pillow_is_broken,
        test_all_present_is_zero,
        test_verdict_priority_problem_over_degraded,
        test_junction_path_unique_per_process,
        test_junction_path_unique_across_processes,
        test_link_validity_check,
        test_parallel_import_survives_foreign_link,
    ):
        fn()
        print("  ok  %s" % fn.__name__)


if __name__ == "__main__":
    _run_all()
    print("OK engine_media/doctor.py: нет torch/whisperx -> код 2 (урезанный "
          "режим), нет numpy/pillow -> код 1, путь junction уникален на процесс "
          "и параллельные проверки не сносят ссылку друг у друга")
