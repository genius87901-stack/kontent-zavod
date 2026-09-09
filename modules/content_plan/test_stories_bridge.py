# -*- coding: utf-8 -*-
"""Регресс-тест АВТОСБОРКИ сторис: слот плана -> кадр 1080×1920.

ПОВОД. До 28.08.2026 формат `stories` планировался, но не собирался НИКОГДА:
executor._real_media_for отвечал на него жёстко зашитой строкой «движка сторис в
заводе нет». Движок при этом лежал в соседнем модуле той же коробки
(story_publisher/story_builder.py) — не было только шва. Клиент планировал
сторис и молча не получал их.

Поэтому главная проверка здесь — ТРЕВОЖКА: исполнитель обязан звать мост, а не
возвращать заглушку. Если кто-то вернёт старое поведение, тест покраснеет.

Проверяем:
    1. Исполнитель зовёт мост (заглушки «движка нет» больше нет).
    2. Нет хука и темы — честный needs_input, а не падение.
    3. Нет модуля сторис в копии — честный needs_input, а не падение.
    4. ФОТО НЕ ОБЯЗАТЕЛЬНО: без единого входного кадра сторис всё равно
       собирается (синтетический фон). Это отличие сторис от каруселей, и оно
       намеренное — легко потерять при рефакторинге.
    5. Фото из слота действительно становится фоном.
    6. Подписью идёт ПЕРВОЕ предложение смысла, а не весь абзац.
    7. Доктор жалуется, только когда модуля сторис на диске нет.

СЕТЬ. Ни один путь здесь в сеть не ходит: сборка кадра — локальный рендер.
Файл гоняется общим run_tests.py под предохранителем tests_guard.

Гоняется двумя способами:
    python modules/content_plan/test_stories_bridge.py
    pytest modules/content_plan/test_stories_bridge.py
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "modules"))

from content_plan import doctor, executor, paths, stories_bridge  # noqa: E402


# ── помощники ────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _времянка():
    with tempfile.TemporaryDirectory(prefix="stories_bridge_") as d:
        yield Path(d)


@contextlib.contextmanager
def _подменить(пары):
    """(объект, имя, значение) — вернуть как было по выходу."""
    было = [(о, и_, getattr(о, и_)) for о, и_, _ in пары]
    for о, и_, знач in пары:
        setattr(о, и_, знач)
    try:
        yield
    finally:
        for о, и_, знач in было:
            setattr(о, и_, знач)


@contextlib.contextmanager
def _очередь(tmp: Path):
    """Очередь плана уводим во времянку — в рабочий каталог не пишем."""
    q = tmp / "queue"
    q.mkdir(parents=True, exist_ok=True)
    with _подменить([(paths, "queue_dir", lambda: q),
                     (paths, "ensure_dirs", lambda: None)]):
        yield q


@contextlib.contextmanager
def _движок_заглушка(записи: list):
    """Подменить story_builder.build_story: записать аргументы, не рисовать.

    Нужен там, где проверяется КОНТРАКТ вызова (что именно мост передаёт
    движку), а не картинка. Настоящий рендер проверяется отдельным тестом.
    """
    def build_story(hook, text, code=None, accent="lime", background=None,
                    out=None, index=1, total=1):
        записи.append({"hook": hook, "text": text, "code": code,
                       "background": background, "out": out})
        Path(out).write_bytes(b"")
        return {"path": out, "width": 1080, "height": 1920,
                "engine": "fallback",
                "background": "material" if background else "synthetic",
                "code": bool(code)}

    пакет = types.ModuleType("story_publisher")
    пакет.__path__ = []
    модуль = types.ModuleType("story_publisher.story_builder")
    модуль.build_story = build_story
    пакет.story_builder = модуль
    было = {и_: sys.modules.get(и_)
            for и_ in ("story_publisher", "story_publisher.story_builder")}
    sys.modules["story_publisher"] = пакет
    sys.modules["story_publisher.story_builder"] = модуль
    try:
        yield записи
    finally:
        for и_, знач in было.items():
            if знач is None:
                sys.modules.pop(и_, None)
            else:
                sys.modules[и_] = знач


@contextlib.contextmanager
def _модуля_нет():
    """Сымитировать копию завода БЕЗ story_publisher."""
    with _подменить([(stories_bridge, "_load_builder", lambda: None)]):
        yield


def _слот(sid="s1", **поля):
    слот = {"id": sid, "format": "stories", "platforms": ["instagram"],
            "publish_at": "2030-01-02T09:00:00",
            "topic": "почему план не выполняется",
            "hook": "вы считаете не то"}
    слот.update(поля)
    return слот


# ── 1. ТРЕВОЖКА: исполнитель зовёт мост, а не заглушку ───────────────────────

def test_исполнитель_зовёт_мост_а_не_заглушку():
    """Главная проверка файла. Раньше здесь возвращалась строка «движка сторис
    в заводе нет» при любых входах — и слот не собирался никогда."""
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        res = executor._real_media_for(_слот())
    assert res["ok"] is True, res
    assert записи, "исполнитель не позвал движок сторис — вернулась заглушка"
    assert res["media"]["width"] == 1080
    assert res["media"]["height"] == 1920


def test_подсказка_движка_больше_не_говорит_что_движка_нет():
    подсказка = executor._engine_hint({"format": "stories"})
    assert "story_builder" in подсказка, подсказка
    assert "НЕТ" not in подсказка, подсказка


# ── 2-3. честные отказы вместо падений ───────────────────────────────────────

def test_слот_без_хука_и_темы_просит_смыслы():
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        res = stories_bridge.build(_слот(topic="", hook=""))
    assert res["ok"] is False
    assert res["needs_input"].startswith("смыслы:"), res
    assert not записи, "движок звать не должны были — входа нет"


def test_нет_модуля_сторис_честный_отказ():
    with _времянка() as tmp, _очередь(tmp), _модуля_нет():
        res = stories_bridge.build(_слот())
    assert res["ok"] is False
    assert res["needs_input"].startswith("модуль:"), res


# ── 4. фото НЕ обязательно (отличие сторис от каруселей) ─────────────────────

def test_без_единого_фото_кадр_всё_равно_собирается():
    """Сторис — единственный формат, которому входное фото не нужно: движок
    рисует синтетический тёмный фон. Если кто-то добавит сюда guard на фото
    «за компанию с каруселями», тест покраснеет."""
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        with _подменить([(stories_bridge, "resolve_photos", lambda s: [])]):
            res = stories_bridge.build(_слот())
    assert res["ok"] is True, res
    assert записи[0]["background"] is None
    assert res["media"]["background"] == "synthetic"
    assert res["media"]["photo_used"] is None


def test_настоящий_рендер_отдаёт_кадр_1080х1920():
    """Без заглушек: настоящий story_builder на синтетическом фоне.

    install_root движка уводим во времянку — тогда engine_photo рядом не
    найдётся и сработает запасной рендер на голом PIL. Проверяем ровно то, ради
    чего всё затевалось: на выходе валидный PNG канонического размера.
    """
    from story_publisher import story_builder as sb
    from PIL import Image

    with _времянка() as tmp, _очередь(tmp) as q:
        with _подменить([(sb, "install_root", lambda: tmp)]):
            res = stories_bridge.build(_слот())
        # Проверяем ВНУТРИ времянки: на выходе каталог удаляется вместе с кадром.
        assert res["ok"] is True, res
        кадр = q / "s1" / "story.png"
        assert кадр.is_file(), "кадр сторис не создан"
        with Image.open(кадр) as im:
            assert im.size == (1080, 1920), im.size


# ── 5-6. что именно уходит в движок ──────────────────────────────────────────

def test_фото_из_слота_становится_фоном():
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        фото = tmp / "кадр.jpg"
        фото.write_bytes(b"")
        with _подменить([(stories_bridge, "resolve_photos",
                          lambda s: [str(фото)])]):
            res = stories_bridge.build(_слот())
    assert записи[0]["background"] == str(фото)
    assert res["media"]["photo_used"] == str(фото)
    assert res["media"]["background"] == "material"


def test_подписью_идёт_первое_предложение_смысла():
    смысл = ("Считать надо маржу, а не выручку. Дальше идёт длинный абзац, "
             "который на кадре 1080×1920 не поместится и читаться не будет.")
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        stories_bridge.build(_слот(meaning=смысл))
    assert записи[0]["text"] == "Считать надо маржу, а не выручку."


def test_кодовое_слово_доезжает_до_плашки():
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        res = stories_bridge.build(_слот(code="ГАЙД"))
    assert записи[0]["code"] == "ГАЙД"
    assert res["media"]["code"] is True


# ── 8. команда выкладки: сторис идут НЕ через autopost ───────────────────────

def test_команда_выкладки_сторис_зовёт_story_publisher():
    """ТРЕВОЖКА. autopost.py кладёт пост в ЛЕНТУ. Сторис-слот раньше молча
    проваливался в общую ветку и собирал команду поста — то есть человеку
    предлагали опубликовать историю постом в ленту."""
    from content_plan import gate

    cmd = gate.build_autopost_command(_слот())
    assert "story_publisher" in cmd, cmd
    assert "autopost.py" not in cmd, cmd
    assert "--send" in cmd, "предохранитель --send обязан остаться: %s" % cmd
    assert "PYTHONPATH=modules" in cmd, cmd


def test_сторис_не_обещают_срок_которого_не_умеют():
    """У story_publisher нет отложенной постановки. Дописать `--at` значило бы
    пообещать срок, который скрипт не исполнит."""
    from content_plan import gate

    cmd = gate.build_autopost_command(_слот())
    assert "--at" not in cmd, "сторис не умеют отложенную выкладку: %s" % cmd


def test_лента_собирает_прежнюю_команду():
    """РЕГРЕССИЯ: карусели и reels обязаны идти прежним путём."""
    from content_plan import gate

    карусель = gate.build_autopost_command(_слот(sid="c1", format="carousel"))
    ролик = gate.build_autopost_command(_слот(sid="r1", format="reels"))
    assert "autopost.py" in карусель and "--at" in карусель, карусель
    assert "autopost.py" in ролик and "--video" in ролик, ролик


# ── 7. доктор ────────────────────────────────────────────────────────────────

def test_доктор_молчит_когда_модуль_на_месте():
    limits = []
    doctor._check_stories_engine(5, limits)
    assert limits == [], limits


def test_доктор_жалуется_когда_модуля_нет():
    limits = []
    with _времянка() as tmp:
        with _подменить([(doctor.config, "install_root", lambda: tmp)]):
            doctor._check_stories_engine(5, limits)
    assert len(limits) == 1
    assert "story_publisher" in limits[0]
    # Жалоба обязана называть лечение, а не только беду.
    assert "обновлен" in limits[0].lower()


# ── 10-13. ДВЕ ОСИ: серия кадров и готовый вертикальный ролик ────────────────

def test_серия_собирает_столько_кадров_сколько_просит_слот():
    """«Карусель в сторис» — это несколько кадров подряд: листаемой карусели
    внутри ОДНОЙ истории у площадок нет вовсе."""
    with _времянка() as tmp, _очередь(tmp) as q, _движок_заглушка([]) as записи:
        res = stories_bridge.build(_слот(
            story_frames=4,
            meaning="Первое. Второе. Третье. Четвёртое."))
    assert res["ok"] is True, res
    медиа = res["media"]
    assert медиа["story_kind"] == "series", медиа
    assert медиа["count"] == 4, медиа
    assert медиа["files"] == ["story_%02d.png" % i for i in range(1, 5)], медиа
    assert len(записи) == 4, записи


def test_в_серии_хук_только_на_первом_кадре_а_плашка_на_последнем():
    """Хук на каждом кадре — это один заголовок восемь раз подряд; плашка
    кодового слова на каждом — призыв там, где ещё нечего делать."""
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        stories_bridge.build(_слот(
            story_frames=3, code="ГАЙД",
            hook="вы считаете не то",
            meaning="Первое. Второе. Третье."))
    assert записи[0]["hook"] == "вы считаете не то", записи[0]
    assert записи[1]["hook"] != "вы считаете не то", записи[1]
    assert [з["code"] for з in записи] == [None, None, "ГАЙД"], записи


def test_серия_не_растёт_бесконечно():
    """Восемь историй подряд человек ещё досматривает, дальше — пролистывает."""
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        res = stories_bridge.build(_слот(story_frames=99, meaning="Раз. Два."))
    assert res["media"]["count"] == 8, res["media"]
    assert len(записи) == 8, len(записи)


def test_готовый_ролик_уходит_историей_как_есть():
    """Рилс и сторис — оба 1080x1920, перекладывать нечего. Ролик не копируем
    и не пережимаем: лишняя перекодировка только испортила бы качество."""
    with _времянка() as tmp, _очередь(tmp):
        ролик = tmp / "gotoviy.mp4"
        ролик.write_bytes(b"")
        res = stories_bridge.build(_слот(story_video=str(ролик)))
    assert res["ok"] is True, res
    assert res["media"]["story_kind"] == "video", res["media"]
    assert res["media"]["file"] == str(ролик), res["media"]


def test_сырой_материал_спикера_историей_не_уходит():
    """slot["video"] у reels-слота — это НЕСМОНТИРОВАННЫЙ исходник. Мост не
    имеет права подставить его в сторис: это опубликовало бы сырьё."""
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        res = stories_bridge.build(_слот(video="ischodnik_so_spikerom.mp4"))
    assert res["ok"] is True, res
    assert res["media"]["story_kind"] == "single", res["media"]
    assert len(записи) == 1, записи


def test_несуществующий_ролик_называется_вслух():
    """Пропавший ролик — needs_input того же формата, что у reels_bridge, а НЕ
    исключение и уж точно не молча собранная картинка вместо видео.

    До 02.09.2026 здесь летел ValueError: слот падал в FAILED, и человек читал
    трассировку вместо объяснения. Формат отказа сведён к общему."""
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        res = stories_bridge.build(_слот(story_video=str(tmp / "netu.mp4")))
    assert res["ok"] is False, res
    assert "файла нет" in res["needs_input"], res
    assert not записи, "мост собрал картинку вместо запрошенного видео"


# ── 14-17. «РЕПОСТ РИЛСА В СТОРИС»: ссылка на слот вместо ручного пути ───────
#
# ПОВОД (02.09.2026). Владелец: «сторис берёт опубликованный рилс, а выходит
# статичная картинка». Причина была не в драйвере ВК (он выбирает
# stories.getVideoUploadServer по расширению верно), а здесь: ветка видео
# включалась ТОЛЬКО ручным `story_video`, которого не заполнял никто из завода.
# Слот без него молча собирался обычным кадром с фото-фоном — это и была та
# самая «обложка видео».

def _рилс_в_очереди(q, slot_id="r1"):
    """Положить в очередь готовый ролик слота — как это делает reels_bridge."""
    d = q / slot_id
    d.mkdir(parents=True, exist_ok=True)
    ролик = d / "reels.mp4"
    ролик.write_bytes(b"")
    return ролик


def test_ссылка_на_рилс_слот_даёт_видео_без_ручного_пути():
    """Готовый ролик завода всегда лежит по одному адресу
    queue/<slot_id>/reels.mp4 — путь выводится, спрашивать его незачем."""
    with _времянка() as tmp, _очередь(tmp) as q, _движок_заглушка([]) as записи:
        ролик = _рилс_в_очереди(q, "r1")
        res = stories_bridge.build(_слот(sid="s5", story_from_reels="r1"))
    assert res["ok"] is True, res
    assert res["media"]["story_kind"] == "video", res["media"]
    assert str(res["media"]["file"]).lower().endswith(".mp4"), res["media"]
    assert Path(res["media"]["file"]) == ролик, res["media"]
    assert not записи, "картинку рисовать было незачем — есть готовый ролик"


def test_story_video_принимает_идентификатор_слота():
    """Второй вход в ту же ветку: голое имя слота вместо пути."""
    with _времянка() as tmp, _очередь(tmp) as q:
        ролик = _рилс_в_очереди(q, "r7")
        res = stories_bridge.build(_слот(sid="s6", story_video="r7"))
    assert res["ok"] is True, res
    assert res["media"]["story_kind"] == "video", res["media"]
    assert Path(res["media"]["file"]) == ролик, res["media"]


def test_ссылка_на_рилс_без_ролика_громко_отказывает_а_не_рисует_картинку():
    """ГЛАВНАЯ проверка беды 02.09.2026. Человек попросил видео, ролика нет —
    мост обязан сказать это вслух, а не собрать статичный кадр и отчитаться
    успехом. Именно тихая подмена уезжала в ВК «обложкой видео»."""
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        res = stories_bridge.build(_слот(sid="s7", story_from_reels="r9"))
    assert res["ok"] is False, res
    assert "needs_input" in res, res
    assert "r9" in res["needs_input"], res
    assert not записи, "мост собрал статичный кадр вместо запрошенного видео"


def test_обычная_история_и_серия_живут_как_раньше():
    """ГРАНИЦА. Слот, задуманный КАРТИНКОЙ, ни одного поля видео не имеет и
    обязан работать ровно как до правки — это основной путь сторис."""
    with _времянка() as tmp, _очередь(tmp), _движок_заглушка([]) as записи:
        одна = stories_bridge.build(_слот(sid="s8"))
        серия = stories_bridge.build(_слот(sid="s9", story_frames=3,
                                           meaning="Раз. Два. Три."))
    assert одна["ok"] is True and одна["media"]["story_kind"] == "single", одна
    assert одна["media"]["file"] == "story.png", одна["media"]
    assert серия["ok"] is True and серия["media"]["story_kind"] == "series", серия
    assert серия["media"]["count"] == 3, серия["media"]
    assert len(записи) == 4, записи


def _прогнать_всё():
    """Собираем тесты АВТОМАТИЧЕСКИ. Раньше здесь стоял список вызовов руками —
    и шесть дописанных тестов серии и видео молча не запускались: файл был
    зелёным, а проверок в нём не было. Ручной список обязан разойтись с файлом,
    вопрос только когда."""
    тесты = [(и_, ф) for и_, ф in sorted(globals().items())
             if и_.startswith("test_") and callable(ф)]
    for и_, ф in тесты:
        ф()
    return len(тесты)


if __name__ == "__main__":
    сколько = _прогнать_всё()
    print("OK (%d проверок) сторис в плане: исполнитель зовёт настоящий движок "
          "(заглушки «движка нет» больше нет), кадр выходит 1080×1920 даже без "
          "единого входного фото, серия кадров и готовый вертикальный ролик "
          "уходят каждый своим путём, а доктор жалуется только когда модуля "
          "сторис действительно нет на диске." % сколько)
