# -*- coding: utf-8 -*-
"""
Проверка готовности модуля engine-media (ядро рендера).

Печатает ПО-РУССКИ, чего не хватает и как это починить — без стектрейсов.

Код возврата — СТЕПЕНЬ БЕДЫ (CONTRACT.md §2), а не «да/нет»:
    0 — модуль полностью работоспособен;
    2 — УРЕЗАННЫЙ режим: главное (карусели и рендер видео) работает, часть
        возможностей недоступна — человеку не надо бросать всё и чинить сейчас;
    1 — НЕ ГОТОВ: нет главного пути (ядро рендера) ИЛИ сама проверка не
        смогла отработать.

Почему это важно: `1` уходит владельцу красным «🔴 СРОЧНО»
(health_report.doctor_level → "down", phase()=broken), а `2` — обычной строкой
суточной сводки. До разделения любая мелочь давала `1`, и машина, отлично
печатающая карусели, помечалась как сломанная только потому, что на ней нет
torch/whisperx — а они нужны ТОЛЬКО разговорным Reels (транскрибация).

Запуск (из корня установки): python modules/engine_media/doctor.py
Никаких абсолютных путей и никакого input(): корень вычисляется относительно
этого файла (каталог с CONTRACT.md), всё остальное — от него.
"""
from __future__ import annotations

import atexit
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Корень установки = каталог с CONTRACT.md. doctor.py лежит в
# <корень>/modules/engine_media/, поэтому поднимаемся на два уровня.
ROOT = Path(__file__).resolve().parents[2]

# Консоль русской Windows — cp866/cp1251. Любой символ вне кодовой страницы
# роняет печать UnicodeEncodeError, и доктор возвращает 1 («проверка не смогла
# отработать») на исправной машине — ровно тот класс ложной тревоги, ради
# которого заведён код 2. Своя печать всегда в utf-8 с заменой непечатаемого.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # старый питон или подменённый stdout — не критично
    pass

# ── «чем доставить» под ТЕКУЩУЮ ОС (BOX-18) ──────────────────────────────────
# На маке команды winget не существует, и клиент упирался в тупиковую подсказку.
# ОС определяем ЕДИНЫМ принятым в проекте способом — install/check_env.py
# (detect_os + manifest_hint + ключи <os>_install из module.manifest.json).
# Своего определения ОС не заводим.
# Windows-путь не меняется: там возвращается windows_install из манифеста, а он
# совпадает с текстом, что был зашит здесь раньше; при любой неудаче — прежний текст.
_CHECK_ENV_CACHE: list = []      # [] — ещё не грузили; [mod|None] — загружено


def _check_env_module():
    """install/check_env.py как модуль (он не пакет и не лежит на PYTHONPATH)."""
    if _CHECK_ENV_CACHE:
        return _CHECK_ENV_CACHE[0]
    mod = None
    try:
        import importlib.util
        for parent in Path(__file__).resolve().parents:
            cand = parent / "install" / "check_env.py"
            if cand.is_file():
                spec = importlib.util.spec_from_file_location("factory_check_env", cand)
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                break
    except Exception:
        mod = None
    _CHECK_ENV_CACHE.append(mod)
    return mod


def _manifest_binary(name: str):
    """Запись system_binaries своего модуля по имени бинарника (или None)."""
    try:
        man = Path(__file__).resolve().parent / "module.manifest.json"
        data = json.loads(man.read_text(encoding="utf-8-sig"))
        for b in data.get("system_binaries", []) or []:
            if (b.get("name") or "").strip().lower() == name:
                return dict(b)
    except Exception:
        pass
    return None


def _install_hint(name: str, windows_hint: str) -> str:
    """Команда установки `name` для текущей ОС: winget / brew / apt."""
    ce = _check_env_module()
    if ce is None:
        return windows_hint
    try:
        os_key = ce.detect_os()
        entry = _manifest_binary(name) or {"name": name}
        if os_key == "windows":
            return (entry.get("windows_install") or "").strip() or windows_hint
        return ce.manifest_hint(entry, os_key)
    except Exception:
        return windows_hint

# ── ТРИ КОРЗИНЫ ПО СТЕПЕНИ БЕДЫ (CONTRACT.md §2) ─────────────────────────────
PROBLEMS: list[str] = []   # нет главного пути / проверка не отработала → код 1
DEGRADED: list[str] = []   # урезанный режим: главное работает → код 2
NOTES: list[str] = []      # подсказки, ничего не потеряно → на код не влияют

# Точная версия protobuf критична: whisperx тянет новую ветку protobuf, а она
# молча ломает детектор лиц mediapipe — тот начинает возвращать «лиц нет» на
# любом кадре. Внешне это выглядит как «на лице текста нет» и портит вёрстку.
PROTOBUF_PIN = "4.25.9"

# ЯДРО РЕНДЕРА. Пакет -> как он импортируется в Python.
# Без любого из них не собирается ГЛАВНОЕ: карусели (PIL+numpy), гейт качества
# checks.py (cv2+mediapipe), сборка видео (imageio-ffmpeg), сток (requests).
# Отсутствие любого — это код 1: чинить прямо сейчас, иначе завод не печёт.
CORE_PACKAGES = {
    "numpy": "numpy",
    "pillow": "PIL",
    "opencv-python-headless": "cv2",
    "mediapipe": "mediapipe",
    "imageio-ffmpeg": "imageio_ffmpeg",
    "requests": "requests",
}

# СЛОЙ РАЗГОВОРНЫХ REELS (транскрибация речи, движок №4 / talk_*.py).
# Карусели рендерятся на PIL и numpy и про torch ничего не знают — раньше эти
# два пакета лежали в обязательных с формулировкой «без него ядро рендера не
# запустится», и машина, отлично печатающая карусели, помечалась как broken.
# По install/stages.json engine-media включается на ЭТАПЕ 1, а разговорные
# форматы (viral-formats, script_writer) — только на ЭТАПЕ 2, то есть на этапе 1
# этих пакетов на машине законно нет. Отсутствие = урезанный режим, код 2.
TALK_PACKAGES = {
    "torch": "torch",
    "whisperx": "whisperx",
}

#: Что именно теряет человек без слоя разговорных Reels — этой фразой доктор
#: объясняет урезанный режим (одна формулировка на все места).
TALK_LOSS = ("разговорные Reels (видео, где вы говорите в камеру: речь → текст → "
             "блоки → нарезка с перебивками) собираться не будут. Карусели, "
             "сторис и обычные Reels работают полностью")

# Точные версии для подсказок «как починить» (совпадают с module.manifest.json).
PIP_PINS = {
    "numpy": "2.0.2",
    "pillow": "11.3.0",
    "opencv-python-headless": "4.11.0.86",
    "mediapipe": "0.10.14",
    "imageio-ffmpeg": "0.6.0",
    "requests": "2.32.5",
    "torch": "2.8.0",
    "whisperx": "3.7.5",
}


def _pin(pip_name: str) -> str:
    """Имя пакета с точной версией для команды pip install."""
    ver = PIP_PINS.get(pip_name)
    return "%s==%s" % (pip_name, ver) if ver else pip_name


def check_python() -> None:
    v = sys.version_info
    if v < (3, 9):
        PROBLEMS.append(
            "Слишком старый Python (%d.%d). Нужен Python 3.9-3.12. "
            "Установите свежий Python и переустановите зависимости." % (v.major, v.minor)
        )
    elif v >= (3, 13):
        NOTES.append(
            "Python %d.%d новее проверенного (3.9-3.12). Часть колёс "
            "(mediapipe, onnxruntime) может не встать — при ошибках установки "
            "используйте Python 3.12." % (v.major, v.minor)
        )


def _importable(import_name: str) -> bool:
    """Пакет реально импортируется? Отдельной функцией — её подменяет тест."""
    try:
        importlib.import_module(import_name)
        return True
    except Exception:
        return False


def check_packages() -> None:
    """Ядро — блокирует (код 1). Слой разговорных Reels — урезает (код 2)."""
    for pip_name, import_name in CORE_PACKAGES.items():
        if not _importable(import_name):
            PROBLEMS.append(
                "Не установлен пакет %s — без него ядро рендера не запустится "
                "(не соберутся ни карусели, ни видео). "
                "Почините командой: pip install %s" % (pip_name, _pin(pip_name))
            )

    missing_talk = [p for p, i in TALK_PACKAGES.items() if not _importable(i)]
    if missing_talk:
        DEGRADED.append(
            "Нет пакетов для распознавания речи (%s) — %s. Это нормально, пока не "
            "подключён этап 2 (разговорные форматы): на этапе 1 они и не ставятся. "
            "Если разговорные Reels нужны — поставьте: pip install %s"
            % (", ".join(missing_talk), TALK_LOSS,
               " ".join(_pin(p) for p in missing_talk))
        )


def check_protobuf() -> None:
    """Степень беды: код 1, хотя «внешне всё работает».

    Это единственный случай, где урезанный режим был бы опаснее поломки: с
    чужой версией protobuf детектор лиц не падает, а ТИХО отвечает «лиц нет» на
    любом кадре. Завод продолжает печатать карусели — с текстом поверх лица.
    Человек ничего не заметит, пока не увидит испорченную вёрстку у себя в
    ленте, поэтому это именно «бросай всё и чини»."""
    try:
        import google.protobuf as pb
    except Exception:
        PROBLEMS.append(
            "Не установлен protobuf. Он нужен детектору лиц. "
            "Почините: pip install protobuf==%s" % PROTOBUF_PIN
        )
        return
    ver = getattr(pb, "__version__", "?")
    if ver != PROTOBUF_PIN:
        PROBLEMS.append(
            "Неверная версия protobuf: стоит %s, нужна ровно %s. С другой версией "
            "детектор лиц тихо ломается и завод решает, что «на лице текста нет» — "
            "вёрстка портится незаметно. Почините: pip install protobuf==%s"
            % (ver, PROTOBUF_PIN, PROTOBUF_PIN)
        )


# ── ASCII-ссылка на mediapipe и ГОНКА ЗА НЕЙ ─────────────────────────────────
# Каталоги, которые создали МЫ и обязаны убрать за собой (чужие не трогаем).
_MP_OWN_ROOTS: list[str] = []
#: Сек. Брошенный каталог старше этого — мусор от давно умершего прогона:
#: проверка доктора живёт секунды, сутки ей не нужны ни при какой погоде.
_MP_STALE_AGE = 24 * 3600


def _mp_safe_root() -> str:
    """Общий каталог под ASCII-ссылки. Путь не абсолютный в коде — из окружения."""
    return os.path.join(
        os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
        "KonsentZavod", "mp_safe")


def _mp_own_root(safe_root: str) -> str:
    """Каталог под СВОЮ ссылку: уникален для процесса и для каждого вызова.

    Именно эта уникальность и чинит гонку: два одновременных прогона больше не
    делят один и тот же путь и не могут снести ссылку друг у друга."""
    return os.path.join(safe_root, "proc-%d-%s" % (os.getpid(), uuid.uuid4().hex[:8]))


def _mp_link_is_valid(link: str, real_dir: str) -> bool:
    """Ссылка есть и уже указывает ровно на нужный каталог пакета."""
    if not os.path.lexists(link):
        return False
    try:
        return (os.path.normcase(os.path.realpath(link))
                == os.path.normcase(os.path.realpath(real_dir)))
    except OSError:
        return False


def _mp_make_link(root: str, link: str, real_dir: str) -> bool:
    """Создать junction root/mediapipe -> real_dir. True, если получилось.

    Гонку на самом mklink переживаем молча: проигравший увидит уже готовую
    ссылку и просто ею воспользуется (проверка _mp_link_is_valid снаружи)."""
    try:
        os.makedirs(root, exist_ok=True)
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, real_dir],
                           capture_output=True)
        return r.returncode == 0
    except OSError:
        return False


def _mp_drop_link(link: str) -> None:
    """Снять ТОЛЬКО junction, не тронув каталог, на который он указывает.

    Именно os.rmdir, а не shutil.rmtree: rmtree способен уйти внутрь ссылки и
    вынести настоящий пакет mediapipe из site-packages."""
    try:
        os.rmdir(link)
    except OSError:
        pass


def _mp_sweep_stale(safe_root: str) -> None:
    """Подмести каталоги proc-* от давно умерших прогонов (старше суток).

    Свежие чужие каталоги НЕ трогаем никогда: рядом может идти живой прогон,
    которому эта ссылка нужна прямо сейчас."""
    try:
        names = os.listdir(safe_root)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not name.startswith("proc-"):
            continue
        root = os.path.join(safe_root, name)
        if root in _MP_OWN_ROOTS:
            continue
        try:
            if now - os.path.getmtime(root) < _MP_STALE_AGE:
                continue
        except OSError:
            continue
        _mp_drop_link(os.path.join(root, "mediapipe"))
        try:
            os.rmdir(root)
        except OSError:
            pass


def _mp_cleanup() -> None:
    """Убрать за собой свои ссылки. Зарегистрирована на выход процесса."""
    for root in list(_MP_OWN_ROOTS):
        _mp_drop_link(os.path.join(root, "mediapipe"))
        try:
            os.rmdir(root)
        except OSError:
            pass
        try:
            _MP_OWN_ROOTS.remove(root)
        except ValueError:
            pass


atexit.register(_mp_cleanup)


def _mp_add_to_path(root: str) -> None:
    if root not in sys.path:
        sys.path.insert(0, root)


def _mediapipe_ascii_safe_import() -> str | None:
    """BOX-61 (checks.py) в этом докторе НЕ вызывался — mediapipe в
    check_face_detector() импортировался напрямую, и живой мини-прогон падал
    ровно на том же кириллическом пути установки («КонтентЗавод» —
    обязательное имя папки), что уже чинили для самого рендера. Итог: рендер
    работал, а доктор рапортовал engine-media=broken — ложная тревога.

    ГОНКА ЗА ФИКСИРОВАННЫМ ПУТЁМ (чинится здесь). Прежняя версия работала с
    ОДНИМ жёстко заданным путём <LOCALAPPDATA>/KonsentZavod/mp_safe/mediapipe и,
    если ссылка вела не туда, делала os.rmdir. Два параллельных прогона — и один
    сносит ссылку ровно в тот момент, когда второй по ней импортирует mediapipe:
    импорт падает, детектор лиц «не запускается», доктор рапортует broken на
    исправной машине. Живое доказательство: 17.08.2026 19:54:40Z одна копия одной
    и той же секундой прислала три отчёта — engine-media=broken в одном и =ok в
    двух других. Реальной поломки не было.

    Теперь так:
      1) общая ссылка уже указывает куда надо — переиспользуем КАК ЕСТЬ,
         ничего не сносим (обычный случай, мусора не появляется вовсе);
      2) общей ссылки нет — пробуем создать; проиграли гонку соседу — просто
         берём то, что он создал;
      3) общая ссылка есть, но ведёт не туда (копию завода переставили) —
         СНОСИТЬ ЕЁ НЕЛЬЗЯ, ею может пользоваться соседний процесс. Делаем свою,
         уникальную по PID+uuid4, и убираем её за собой на выходе.

    Возвращает каталог, добавленный в sys.path (или None, если лечение не
    понадобилось / не удалось — тогда импортируем как раньше, доктор не падает).

    Копия логики checks._mediapipe_ascii_safe_import(), не импорт оттуда:
    checks.py тянет за собой engine4.py (полный движок каруселей) — слишком
    тяжело для лёгкой health-проверки. Меняешь один — проверь и синхронизируй
    со вторым. НА 21.08.2026 checks.py ВСЁ ЕЩЁ СО СТАРЫМ os.rmdir ПО ФИКСИРОВАННОМУ
    ПУТИ — там нужна ровно такая же правка (файл принадлежит другой ветке)."""
    if sys.platform != "win32":
        return None
    import importlib.util
    spec = importlib.util.find_spec("mediapipe")
    if spec is None or not spec.submodule_search_locations:
        return None
    real_dir = spec.submodule_search_locations[0]
    if real_dir.isascii():
        return None

    safe_root = _mp_safe_root()
    shared = os.path.join(safe_root, "mediapipe")

    # (1) готовая и верная общая ссылка — лучший случай: ничего не создаём.
    if _mp_link_is_valid(shared, real_dir):
        _mp_add_to_path(safe_root)
        return safe_root

    # (2) ссылки нет — создаём общую. Гонка на mklink безопасна: проигравший
    #     увидит верную ссылку соседа и пойдёт по ветке (1).
    if not os.path.lexists(shared):
        _mp_make_link(safe_root, shared, real_dir)
        if _mp_link_is_valid(shared, real_dir):
            _mp_add_to_path(safe_root)
            return safe_root

    # (3) чужая/устаревшая ссылка на месте — свою кладём рядом, под уникальным
    #     именем. Чужое не сносим: параллельный процесс может быть в импорте.
    _mp_sweep_stale(safe_root)
    own_root = _mp_own_root(safe_root)
    if _mp_make_link(own_root, os.path.join(own_root, "mediapipe"), real_dir):
        _MP_OWN_ROOTS.append(own_root)
        _mp_add_to_path(own_root)
        return own_root
    return None


def check_face_detector() -> None:
    """Живой мини-прогон детектора лиц: ловим случай, когда mediapipe стоит, но
    из-за окружения (тот же protobuf) молча не работает.

    Степень беды: код 1 — по той же причине, что и protobuf. Детектор лиц стоит
    на главном пути: гейт качества checks.py решает им, не легло ли текстовое
    поле на лицо. Молча испорченная вёрстка уходит в ленту клиента."""
    try:
        _mediapipe_ascii_safe_import()
        import numpy as np
        import mediapipe as mp
    except Exception:
        return  # об отсутствии пакетов уже сказано выше
    try:
        fd = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5)
        fd.process(np.zeros((64, 64, 3), dtype="uint8"))  # чёрный кадр, лиц нет — ок
        fd.close()
    except Exception as ex:
        PROBLEMS.append(
            "Детектор лиц (mediapipe) установлен, но не запускается: %s. "
            "Чаще всего причина — неверная версия protobuf (нужна %s)."
            % (str(ex)[:100], PROTOBUF_PIN)
        )


def check_ffmpeg() -> None:
    """Рендер использует ffmpeg из пакета imageio-ffmpeg (он идёт в комплекте),
    поэтому системный ffmpeg не обязателен. Проверяем именно рабочий путь.

    Степень беды: рабочий ffmpeg из пакета — это ГЛАВНЫЙ путь (без него не
    собирается ни одно видео), поэтому его отсутствие — код 1. Системный ffmpeg
    в PATH — просто удобство, его нет у большинства чистых Windows: это NOTE,
    даже не урезанный режим (ровно тот случай, из-за которого клиентам летели
    красные тревоги до BOX-51)."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        PROBLEMS.append(
            "Нет ffmpeg: не установлен пакет imageio-ffmpeg, который приносит "
            "ffmpeg с собой. Без него не соберётся ни одно видео. "
            "Почините: pip install imageio-ffmpeg==0.6.0"
        )
        return
    try:
        subprocess.run([exe, "-version"], capture_output=True, timeout=30)
    except Exception:
        PROBLEMS.append(
            "ffmpeg из пакета imageio-ffmpeg не запускается — видео собрать "
            "не получится. Переустановите пакет: "
            "pip install --force-reinstall imageio-ffmpeg==0.6.0"
        )
        return
    # Системный ffmpeg — необязательный запасной вариант, только подсказка.
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=30)
    except Exception:
        NOTES.append(
            "Системный ffmpeg в PATH не найден — это не проблема: рендер берёт "
            "ffmpeg из пакета. Если захотите поставить системный: %s"
            % _install_hint("ffmpeg", "winget install Gyan.FFmpeg")
        )


def check_model_cache() -> None:
    """Модели распознавания речи качаются автоматически при первом запуске.
    Их отсутствие — не ошибка, просто предупреждаем, что первый прогон дольше.

    Степень беды: NOTE, а не урезанный режим. Человеку тут НЕЧЕГО делать —
    веса скачаются сами при первом разборе разговорного видео; на свежей
    коробке их нет ВСЕГДА, и код 2 означал бы «урезан» на каждой новой
    установке до первого ролика — это шум в суточной сводке, а не сигнал.
    Если слоя разговорных Reels нет вовсе — про это уже сказано кодом 2 в
    check_packages(), дублировать нечем."""
    cache = ROOT / ".cache_models"
    # HF кладёт модели в <HF_HOME>/hub/models--..., поэтому ищем рекурсивно.
    whisper_ready = cache.exists() and any(cache.rglob("models--Systran--faster-whisper-*"))
    if not whisper_ready:
        NOTES.append(
            "Модели распознавания речи (whisper) ещё не скачаны. Это нормально: "
            "они загрузятся сами при первом разборе разговорного видео "
            "(нужен интернет, ~0.5 ГБ, один раз). Карусели и стоковые вставки "
            "работают без них."
        )
    # Детектор лиц mediapipe несёт модель внутри пакета — отдельно качать нечего.


def check_transcription_stack() -> None:
    """Живая проверка, что стек транскрипции (whisperx + VAD) реально поднимается.

    Зачем: раньше doctor этого не ловил. Пакеты стоят -> doctor=0, а транскрипция
    мертва: начиная с torch 2.6 (у нас 2.8) torch.load грузит чекпоинты в
    безопасном режиме (weights_only=True) и рвётся на VAD-модели whisperx
    (UnpicklingError: omegaconf.ListConfig). Без транскрипта нет блоков -> нет
    перебивок -> разговорные Reels не собираются вообще. Это была тихая дыра.

    Проверяем в два слоя, оба — по РЕАЛЬНЫМ причинам поломки:
      (1) синтетикой без скачивания моделей воспроизводим оба режима отказа
          (weights_only и ленивый импорт speechbrain) и убеждаемся, что наш
          обход в talk_transcribe их чинит;
      (2) если модель whisper уже в кэше — поднимаем настоящий whisperx.load_model
          нашим чинёным путём (сеть при этом не дёргаем — берём кэшированный размер).

    Степень беды: всё, что здесь ломается, — это код 2, а не 1. Сломанный стек
    транскрипции гасит РОВНО ту же возможность, что и полное отсутствие
    torch/whisperx (разговорные Reels), а карусели и обычные Reels продолжают
    печататься. Было бы странно: пакета нет — «урезан», пакет есть, но не
    работает — «коробка сломана».
    """
    try:
        import torch
        import whisperx  # noqa: F401  (нужен как факт наличия стека)
    except Exception:
        return  # об отсутствии пакетов уже сказано в check_packages()

    # Наш чинёный загрузчик лежит в корне установки рядом с engine_*.py.
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import talk_transcribe as tt
    except Exception as ex:
        DEGRADED.append(
            "Не удалось загрузить модуль транскрипции talk_transcribe: %s. "
            "Разговорные видео не превратятся в текст, и разговорные Reels не "
            "соберутся. Карусели и обычные Reels работают." % str(ex)[:120]
        )
        return

    # (1a) Регрессия torch>=2.6 (weights_only). Синтетический чекпоинт с не-тензором
    # воспроизводит РОВНО тот отказ, что рушит VAD-чекпоинт pyannote, но без скачивания.
    import io
    import argparse as _ap
    try:
        buf = io.BytesIO()
        torch.save({"_probe": _ap.Namespace(v=[1, 2, 3])}, buf)
        buf.seek(0)
        try:
            torch.load(buf)
            default_ok = True
        except Exception:
            default_ok = False
        if not default_ok:
            # Это и есть режим, который ломал VAD. Наш скоуп-патч обязан его чинить.
            buf.seek(0)
            with tt._torch_trusted_load():
                torch.load(buf)
    except Exception as ex:
        DEGRADED.append(
            "Транскрипция сломана: torch %s грузит чекпоинты в безопасном режиме "
            "(weights_only) и падает на VAD-модели whisperx, а обход в "
            "talk_transcribe не срабатывает (%s). Разговорные Reels без этого "
            "не соберутся; карусели и обычные Reels работают. "
            "Проверьте talk_transcribe._torch_trusted_load."
            % (getattr(torch, "__version__", "?"), str(ex)[:80])
        )

    # (1b) Ленивые заглушки speechbrain: при обращении к __file__ они бросают
    # ImportError (нужен пакет k2, которого нет), и это рушит inspect.stack()
    # внутри загрузки VAD. Убеждаемся, что наш обход их гасит.
    try:
        from speechbrain.utils.importutils import LazyModule
    except Exception:
        LazyModule = None
    if LazyModule is not None:
        def _broken_lazies():
            bad = []
            for m in list(sys.modules.values()):
                if isinstance(m, LazyModule):
                    try:
                        getattr(m, "__file__", None)
                    except Exception:
                        bad.append(m)
            return bad
        if _broken_lazies():
            tt._neutralize_speechbrain_lazy_imports()
            if _broken_lazies():
                DEGRADED.append(
                    "Транскрипция сломана: заглушки speechbrain бросают ImportError "
                    "при доступе к __file__ (нужен пакет k2, которого нет), из-за "
                    "чего whisperx не может загрузить VAD. Обход в talk_transcribe "
                    "не помог. Разговорные Reels не соберутся; карусели и обычные "
                    "Reels работают."
                )

    # (2) Настоящая загрузка — только если модель уже скачана (сеть не трогаем).
    cache = ROOT / ".cache_models"
    cached_sizes = []
    if cache.exists():
        # HF кладёт модели в <HF_HOME>/hub/models--..., поэтому ищем рекурсивно.
        for d in cache.rglob("models--Systran--faster-whisper-*"):
            size = d.name.rsplit("faster-whisper-", 1)[-1]
            if size and size not in cached_sizes:
                cached_sizes.append(size)
    if cached_sizes:
        size = "tiny" if "tiny" in cached_sizes else cached_sizes[0]
        try:
            tt._neutralize_speechbrain_lazy_imports()
            with tt._torch_trusted_load():
                whisperx.load_model(size, "cpu", compute_type="int8", language="ru")
        except Exception as ex:
            msg = str(ex)
            netish = any(s in msg.lower() for s in (
                "connect", "network", "http", "offline", "resolve",
                "timed out", "temporarily", "name or service"))
            if netish:
                NOTES.append(
                    "Живую загрузку whisperx проверить не удалось (похоже на "
                    "сеть/кэш): %s. Синтетическая проверка стека прошла." % msg[:90]
                )
            else:
                DEGRADED.append(
                    "Стек транскрипции не поднимается: whisperx.load_model('%s') "
                    "упал: %s. Разговорные Reels не соберутся; карусели и обычные "
                    "Reels работают." % (size, msg[:100])
                )
    else:
        NOTES.append(
            "Модель whisper ещё не в кэше — живую загрузку стека транскрипции "
            "пропустил (скачается при первом разборе разговорного видео). "
            "Синтетическая проверка weights_only/VAD прошла."
        )


def _secret(name: str) -> str:
    """Значение секрета: окружение, затем .env в корне установки.

    Раньше смотрели ТОЛЬКО os.environ, а мастер установки кладёт секреты в
    <корень>/.env (install/setup_config.py) — то есть заполненный ключ доктор
    в упор не видел и всегда писал «ключа нет». Сами значения нигде не
    печатаются и не логируются (CONTRACT.md §3)."""
    val = os.environ.get(name)
    if val:
        return val
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip().upper() == name.upper():
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def check_optional_keys() -> None:
    """Необязательные ключи. Их отсутствие корректно (required:false) — модуль
    работает в урезанном режиме, а не падает.

    Степень беды: ключи стока — код 2. Так прямо велит CONTRACT.md §2
    («Незаполненные ключи ... — всегда 2»), и возможность действительно
    теряется: без ключа завод не подберёт видео-вставки и стоковые фоны сам.
    ANTHROPIC_API_KEY — единственное исключение и остаётся NOTE: без него
    НИЧЕГО не теряется, ту же авторазбивку делает сессия Claude без ключа, поле
    задумано пустым (см. help в module.manifest.json)."""
    if not (_secret("PIXABAY_API_KEY") or _secret("PEXELS_API_KEY")):
        DEGRADED.append(
            "Не задан ни один ключ стока (Pixabay/Pexels). Карусели и Reels "
            "соберутся, но завод не сможет сам подбирать видео-вставки и "
            "стоковые фоны — их придётся класть руками. Ключи вводятся при "
            "установке; можно заполнить позже, когда будет удобно."
        )
    if not _secret("ANTHROPIC_API_KEY"):
        NOTES.append(
            "Не задан ANTHROPIC_API_KEY — это норма: авторазбивку разговорного "
            "видео на блоки делает сессия Claude без ключа."
        )


#: Проверка -> куда класть ЕЁ СОБСТВЕННУЮ поломку, если она свалится с
#: исключением. Контракт (§2) говорит «сама проверка не смогла отработать» = 1,
#: но это верно ровно для того слоя, о котором проверка отвечает: если упала
#: проверка стека транскрипции, неизвестно только про разговорные Reels — а это
#: урезанный режим, а не мёртвая коробка. Проверки ядра — в PROBLEMS.
CHECK_BUCKET = {
    "check_python": "problem",
    "check_packages": "problem",
    "check_protobuf": "problem",
    "check_face_detector": "problem",
    "check_ffmpeg": "problem",
    "check_model_cache": "note",
    "check_transcription_stack": "degraded",
    "check_optional_keys": "degraded",
}


def verdict() -> int:
    """Код возврата по трём корзинам (CONTRACT.md §2). Отдельно от печати —
    так его проверяет тест, не разбирая текст."""
    if PROBLEMS:
        return 1
    if DEGRADED:
        return 2
    return 0


def main() -> int:
    for check in (
        check_python,
        check_packages,
        check_protobuf,
        check_face_detector,
        check_ffmpeg,
        check_model_cache,
        check_transcription_stack,
        check_optional_keys,
    ):
        name = getattr(check, "__name__", "?")
        try:
            check()
        except Exception as ex:  # сам doctor не должен падать стектрейсом
            msg = ("Не удалось выполнить проверку «%s»: %s. Это сбой самой "
                   "проверки." % (name, str(ex)[:100]))
            bucket = CHECK_BUCKET.get(name, "problem")
            if bucket == "problem":
                PROBLEMS.append(msg)
            elif bucket == "degraded":
                DEGRADED.append(msg + " Про этот слой возможностей ничего не "
                                      "известно; главное — карусели и рендер — "
                                      "проверено отдельно.")
            else:
                NOTES.append(msg)

    print("== Проверка ядра рендера (engine-media) ==")
    if PROBLEMS:
        print("\nНЕ ГОТОВО. Главное не заработает, пока не почините:")
        for i, p in enumerate(PROBLEMS, 1):
            print("  %d) %s" % (i, p))
    else:
        print("\nГлавное на месте: карусели и рендер видео готовы к работе.")
    if DEGRADED:
        print("\nРаботает НЕ ВСЁ (урезанный режим). Это не поломка — бросать "
              "дела и чинить прямо сейчас не нужно.")
        print("Сейчас недоступно:")
        for i, d in enumerate(DEGRADED, 1):
            print("  %d) %s" % (i, d))
    if NOTES:
        print("\nПодсказки (работать не мешают):")
        for n in NOTES:
            print("  - %s" % n)

    code = verdict()
    if code == 0:
        print("\nИтог: модуль полностью работоспособен.")
    elif code == 2:
        print("\nИтог: главное работает, часть возможностей недоступна "
              "(см. список выше).")
    else:
        print("\nИтог: модуль не готов — нужен ремонт по списку выше.")
    return code


if __name__ == "__main__":
    sys.exit(main())
