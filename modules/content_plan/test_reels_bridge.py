# -*- coding: utf-8 -*-
"""Регресс-тест АВТОСБОРКИ разговорного Reels: слот плана -> смысловые блоки.

Проверяем ровно три исхода шва reels_bridge -> talk_segment, потому что именно
они определяют, что увидит клиент:

    1. КЛЮЧА МОДЕЛИ НЕТ  — прежнее поведение завода: модель не зовётся ни разу,
       слот остаётся честным черновиком с пометкой «блоки готовит человек».
       Ничего не ухудшилось и ничего не потрачено.
    2. КЛЮЧ ЕСТЬ         — блоки получаются сами, blocks.json пишется, слот
       собирается до готового медиа.
    3. МОДЕЛЬ НЕ ОТВЕТИЛА (нет сети / кончилась квота / кривой ответ) — слот
       помечается причиной, а ПРОГОН ПЛАНА ПРОДОЛЖАЕТСЯ: соседние слоты
       собираются как ни в чём не бывало.

────────────────────────────────────────────────────────────────────────────────
ТЕХНИЧЕСКАЯ БЛОКИРОВКА СЕТИ (не «я аккуратно», а запрет в коде).

Повод железный: на этой машине тест однажды уже подхватил живой ключ из .env и
отправил НАСТОЯЩИЙ запрос на площадку. Здесь риск выше — код умеет ходить в
платный API. Поэтому файл на импорте, ДО единого теста:
  * подменяет socket.socket / create_connection / getaddrinfo так, что любая
    попытка соединения падает ГРОМКОЙ ошибкой СетьЗапрещена;
  * подменяет точки входа requests и httpx, если они установлены;
  * вычищает из окружения ANTHROPIC_API_KEY, PIXABAY_API_KEY, PEXELS_API_KEY и
    всё семейство VK_* / UPLOAD_POST_* / TG_*;
  * уводит talk_segment.HERE на пустой каталог, чтобы api_key() не мог прочитать
    настоящий <корень>/.env.
Клиент anthropic не устанавливается и не импортируется: в sys.modules кладётся
заглушка, которая возвращает заранее заготовленный ответ. Ни одного байта наружу.

Гоняется двумя способами:
    python modules/content_plan/test_reels_bridge.py      # автономно
    pytest modules/content_plan/test_reels_bridge.py      # если стоит pytest
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# ── БЛОКИРОВКА СЕТИ ──────────────────────────────────────────────────────────

class СетьЗапрещена(RuntimeError):
    """Тест попытался выйти в сеть. Это провал теста, а не сбой окружения."""


def _взорваться(*_a, **_kw):
    raise СетьЗапрещена(
        "ТЕСТ ПОЛЕЗ В СЕТЬ. Прогон остановлен намеренно: тестам этого модуля "
        "запрещено любое соединение — платный API модели и площадки должны "
        "подменяться заглушками. Смотри, какой вызов сюда привёл.")


def _заблокировать_сеть() -> None:
    """Единственный способ соединиться из Python — сокет. Его и рубим.

    Подменяем socket.socket КЛАССОМ-наследником, а не функцией: стандартный
    модуль ssl на импорте делает `class SSLSocket(socket)`, и функция на этом
    месте роняет чужой импорт TypeError'ом вместо честной ошибки о сети."""
    import ssl  # noqa: F401  — пусть доимпортируется ДО подмены, на всякий

    настоящий = socket.socket

    class _ЗапрещённыйСокет(настоящий):            # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            _взорваться()

    socket.socket = _ЗапрещённыйСокет              # type: ignore[assignment]
    socket.create_connection = _взорваться         # type: ignore[assignment]
    socket.getaddrinfo = _взорваться               # type: ignore[assignment]
    for имя in ("requests", "httpx"):
        мод = sys.modules.get(имя)
        if мод is None:
            try:
                мод = __import__(имя)
            except Exception:
                continue                            # не установлен — блокировать нечего
        for точка in ("request", "get", "post", "put", "delete", "head", "patch"):
            if hasattr(мод, точка):
                setattr(мод, точка, _взорваться)
        клиент = getattr(мод, "Session", None) or getattr(мод, "Client", None)
        if клиент is not None and hasattr(клиент, "request"):
            клиент.request = _взорваться           # type: ignore[assignment]


def _вычистить_ключи() -> None:
    """Живые ключи — вон из окружения прогона. Ни один не должен быть виден."""
    точные = ("ANTHROPIC_API_KEY", "PIXABAY_API_KEY", "PEXELS_API_KEY",
              "OPENAI_API_KEY", "HF_TOKEN", "HUGGINGFACE_TOKEN")
    префиксы = ("VK_", "UPLOAD_POST_", "TG_", "TELEGRAM_", "ANTHROPIC_")
    for имя in list(os.environ):
        if имя in точные or имя.startswith(префиксы):
            os.environ.pop(имя, None)


_заблокировать_сеть()
_вычистить_ключи()

from modules.content_plan import (executor, model, paths,  # noqa: E402
                                  reels_bridge)
import talk_segment  # noqa: E402  (корень уже в sys.path)

# .env настоящего завода не должен быть виден api_key() ни при каких условиях.
talk_segment.HERE = tempfile.mkdtemp(prefix="нет_env_")

assert not talk_segment.api_key(), (
    "ключ модели виден тесту — блокировка не сработала, прогон запрещён")


# ── заготовки ────────────────────────────────────────────────────────────────

#: Транскрипт стадии 2 в формате engine_talk: ГОЛЫЙ список сегментов.
_ТРАНСКРИПТ = [
    {"end": 6.0, "words": [{"w": "первая", "s": 0.0}, {"w": "мысль", "s": 1.0}]},
    {"end": 12.0, "words": [{"w": "вторая", "s": 6.0}, {"w": "мысль", "s": 7.0}]},
]

#: Ответ модели на промпт стадии 3 — ровно то, что вернул бы Claude.
_ОТВЕТ_МОДЕЛИ = json.dumps([
    {"t0": 0.0, "t1": 6.0, "topic": "зачин", "intent": "hook",
     "emotion": 0.8, "visual_query": "wide shot city morning"},
    {"t0": 6.0, "t1": 12.0, "topic": "польза", "intent": "benefit",
     "emotion": 0.5, "visual_query": "general view office desk"},
], ensure_ascii=False)


class _ЗаглушкаAnthropic(types.ModuleType):
    """Модуль-двойник пакета anthropic. Сети не касается вовсе.

    `ответ` — что вернуть; если это исключение, оно бросается на вызове
    messages.create (так воспроизводится «нет сети / кончилась квота»)."""

    def __init__(self, ответ):
        super().__init__("anthropic")
        self.вызовов = 0
        self.последний_промпт = None
        внешний = self

        class _Сообщения:
            def create(self, **kwargs):
                внешний.вызовов += 1
                внешний.последний_промпт = kwargs["messages"][0]["content"]
                if isinstance(ответ, BaseException):
                    raise ответ
                блок = types.SimpleNamespace(type="text", text=ответ)
                return types.SimpleNamespace(content=[блок])

        class Anthropic:
            def __init__(self, api_key=None):
                assert api_key, "клиент создан без ключа"
                self.messages = _Сообщения()

        self.Anthropic = Anthropic


@contextlib.contextmanager
def _подменить(пары):
    было = [(о, и_, getattr(о, и_)) for о, и_, _ in пары]
    for о, и_, знач in пары:
        setattr(о, и_, знач)
    try:
        yield
    finally:
        for о, и_, знач in было:
            setattr(о, и_, знач)


@contextlib.contextmanager
def _модель(ответ, ключ="проверочный-ключ-не-настоящий"):
    """Модель подменена заглушкой, ключ — вымышленный. Наружу не уходит ничего."""
    заглушка = _ЗаглушкаAnthropic(ответ)
    было = sys.modules.get("anthropic")
    sys.modules["anthropic"] = заглушка
    try:
        with _подменить([(talk_segment, "api_key", lambda: ключ)]):
            yield заглушка
    finally:
        if было is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = было


class _ЗаглушкаДвижка(types.ModuleType):
    """render_reels-двойник: НИЧЕГО не рендерит, только подтверждает вызов."""

    def __init__(self):
        super().__init__("render_reels")
        self.вызовов = 0
        self.блоки_файл = None
        внешний = self

        def build(video, transcript, blocks_path, out, **kwargs):
            внешний.вызовов += 1
            внешний.блоки_файл = blocks_path
            Path(out).write_bytes(b"")          # «готовый файл» в очереди
            return {"template": kwargs.get("template"), "dur": 12.0,
                    "size_bytes": 1024, "broll": [], "sheet": None}

        self.build = build


class _ЗаглушкаПроверок(types.ModuleType):
    def __init__(self):
        super().__init__("checks")
        self.verify_talk = lambda meta, strict=True: []


@contextlib.contextmanager
def _завод(tmp: Path, движок=True):
    """Вымышленный завод: очередь вtmp, движок рендера — заглушкой."""
    очередь = tmp / "queue"
    очередь.mkdir(parents=True, exist_ok=True)
    моду = {}
    if движок:
        моду["render_reels"] = _ЗаглушкаДвижка()
        моду["checks"] = _ЗаглушкаПроверок()
    было = {и_: sys.modules.get(и_) for и_ in моду}
    sys.modules.update(моду)
    try:
        with _подменить([(paths, "queue_dir", lambda: очередь),
                         (paths, "ensure_dirs", lambda: None)]):
            yield моду.get("render_reels")
    finally:
        for и_, знач in было.items():
            if знач is None:
                sys.modules.pop(и_, None)
            else:
                sys.modules[и_] = знач


def _слот(tmp: Path, sid="r1"):
    """Reels-слот со всеми входами, кроме блоков: видео есть, транскрипт есть."""
    видео = tmp / "говорящее.mp4"
    видео.write_bytes(b"")
    транскрипт = tmp / "transcript.json"
    транскрипт.write_text(json.dumps(_ТРАНСКРИПТ, ensure_ascii=False),
                          encoding="utf-8")
    return {"id": sid, "format": "reels", "platforms": ["instagram"],
            "publish_at": "2030-01-02T09:00:00",
            "veto_opens_at": "2030-01-01T21:00:00",
            "status": model.PLANNED,
            "topic": "как мы считаем себестоимость",
            "hook": "вы теряете деньги на этом шаге",
            "video": str(видео), "transcript": str(транскрипт)}


@contextlib.contextmanager
def _времянка():
    with tempfile.TemporaryDirectory(prefix="reels_bridge_") as d:
        yield Path(d)


# ── 1. КЛЮЧА НЕТ: слот остаётся черновиком, модель не зовётся ────────────────

def test_без_ключа_слот_остаётся_черновиком():
    with _времянка() as tmp, _завод(tmp) as движок:
        слот = _слот(tmp)
        заглушка = _ЗаглушкаAnthropic(_ОТВЕТ_МОДЕЛИ)
        было = sys.modules.get("anthropic")
        sys.modules["anthropic"] = заглушка       # доступна, но звать её нельзя
        try:
            with _подменить([(talk_segment, "api_key", lambda: "")]):
                итог = reels_bridge.build(слот)
        finally:
            if было is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = было

    assert итог["ok"] is False, "без ключа слот не может быть собран: %r" % итог
    нужен = итог["needs_input"]
    assert нужен.startswith("blocks:"), нужен
    assert "ANTHROPIC_API_KEY" in нужен, нужен
    assert "человек" in нужен, "клиенту не сказано, кто делает разбивку: %s" % нужен
    assert заглушка.вызовов == 0, "без ключа модель звали — это трата денег клиента"
    assert движок.вызовов == 0, "движок рендера запущен без блоков"


def test_без_ключа_прогон_плана_не_падает():
    """Исполнитель кладёт dry-run-артефакт с пометкой, слот НЕ в FAILED."""
    with _времянка() as tmp, _завод(tmp):
        слот = _слот(tmp)
        план = {"version": 1, "approved": True, "approved_at": "2030-01-01T00:00:00",
                "brief": {}, "slots": [слот]}
        with _подменить([(talk_segment, "api_key", lambda: "")]):
            отчёт = executor.run(план, dry_run=False, lead_days=99999)

    assert отчёт["errors"] == [], "прогон плана дал ошибку: %r" % отчёт["errors"]
    assert отчёт["generated"] == ["r1"]
    assert слот["status"] == model.GENERATED, слот["status"]
    assert "ANTHROPIC_API_KEY" in слот["needs_input"], слот["needs_input"]


# ── 2. КЛЮЧ ЕСТЬ: блоки получены сами, слот собран ──────────────────────────

def test_с_ключом_блоки_получены_и_слот_собран():
    with _времянка() as tmp, _завод(tmp) as движок:
        слот = _слот(tmp)
        with _модель(_ОТВЕТ_МОДЕЛИ) as заглушка:
            итог = reels_bridge.build(слот)

        assert итог["ok"] is True, "слот с ключом не собрался: %r" % итог
        assert заглушка.вызовов == 1, "модель звали %d раз" % заглушка.вызовов

        # Контекст слота реально уехал в промпт — иначе разбивка была бы вслепую.
        промпт = заглушка.последний_промпт
        assert "как мы считаем себестоимость" in промпт, промпт[:400]
        assert "вы теряете деньги на этом шаге" in промпт, промпт[:400]

        # Блоки записаны в том виде, который читает движок: json["blocks"].
        файл = Path(движок.блоки_файл)
        assert файл.name == "blocks.json", файл
        данные = json.loads(файл.read_text(encoding="utf-8"))
        assert [b["intent"] for b in данные["blocks"]] == ["hook", "benefit"]
        assert данные["blocks"][-1]["t1"] == 12.0, "блоки не покрыли ролик"
        assert данные["source"].startswith("talk_segment")

        assert итог["media"]["blocks"] == "auto (talk_segment)", итог["media"]
        assert движок.вызовов == 1


def test_кривой_ответ_модели_не_проходит_валидацию():
    """Модель ответила JSON'ом без обязательных полей — блоков нет, не падаем."""
    with _времянка() as tmp, _завод(tmp) as движок:
        слот = _слот(tmp)
        мусор = json.dumps([{"t0": 0.0, "t1": 12.0, "topic": "без интента"}])
        with _модель(мусор):
            итог = reels_bridge.build(слот)

    assert итог["ok"] is False, итог
    assert "не удалась" in итог["needs_input"], итог["needs_input"]
    assert движок.вызовов == 0, "движок запустили на непроверенных блоках"


# ── 3. ОШИБКА МОДЕЛИ: слот помечен, прогон продолжается ─────────────────────

def test_ошибка_модели_не_роняет_прогон_плана():
    """Первый слот упирается в модель, второй собирается — план не встал."""
    with _времянка() as tmp, _завод(tmp) as движок:
        плохой = _слот(tmp, "r1")
        хороший = _слот(tmp, "r2")
        хороший["blocks"] = [
            {"t0": 0.0, "t1": 12.0, "topic": "готовое", "intent": "hook",
             "emotion": 0.6, "visual_query": "wide shot river"}]
        план = {"version": 1, "approved": True, "approved_at": "2030-01-01T00:00:00",
                "brief": {}, "slots": [плохой, хороший]}

        with _модель(ConnectionError("сеть недоступна (проверочная подмена)")):
            отчёт = executor.run(план, dry_run=False, lead_days=99999)

    assert отчёт["errors"] == [], ("ошибка модели уронила прогон в FAILED: %r"
                                   % отчёт["errors"])
    assert отчёт["generated"] == ["r1", "r2"], отчёт["generated"]

    assert плохой["status"] == model.GENERATED, плохой["status"]
    пометка = плохой["needs_input"]
    assert "не удалась" in пометка and "ConnectionError" in пометка, пометка

    assert хороший["status"] == model.GENERATED
    assert "needs_input" not in хороший, "слот с готовыми блоками помечен зря"
    assert движок.вызовов == 1, "соседний слот не собрался: %d" % движок.вызовов


def test_системный_выход_движка_тоже_не_роняет_прогон():
    """talk_segment вышел через SystemExit — ловим и его (мимо except Exception)."""
    with _времянка() as tmp, _завод(tmp):
        слот = _слот(tmp)
        with _модель(SystemExit("НЕТ КЛЮЧА (проверочная подмена)")):
            итог = reels_bridge.build(слот)

    assert итог["ok"] is False, итог
    assert "SystemExit" in итог["needs_input"], итог["needs_input"]


# ── стадия talk_segment сама по себе ─────────────────────────────────────────

def test_segment_без_ключа_бросает_NoApiKey_до_сети():
    with _времянка() as tmp:
        транскрипт = tmp / "transcript.json"
        транскрипт.write_text(json.dumps(_ТРАНСКРИПТ), encoding="utf-8")
        with _подменить([(talk_segment, "api_key", lambda: "")]):
            try:
                talk_segment.segment(str(транскрипт))
            except talk_segment.NoApiKey:
                pass
            else:
                raise AssertionError("segment() без ключа обязан бросить NoApiKey")


def test_промпт_без_контекста_слота_не_меняется():
    """Ручной запуск CLI (topic/hook нет) — промпт прежний, без лишних строк."""
    with _времянка() as tmp:
        транскрипт = tmp / "transcript.json"
        транскрипт.write_text(json.dumps(_ТРАНСКРИПТ), encoding="utf-8")
        голый = talk_segment.build_prompt(str(транскрипт))
        с_контекстом = talk_segment.build_prompt(str(транскрипт), topic="тема",
                                                 hook="крючок")
    assert "КОНТЕКСТ СЛОТА" not in голый
    assert "КОНТЕКСТ СЛОТА" in с_контекстом
    assert len(с_контекстом) > len(голый)


# ── честность доктора: соберётся ли ролик сам ────────────────────────────────

def _вердикт_доктора(веса: bool, ключ: bool):
    """Что доктор скажет про автосборку при таких весах и таком ключе."""
    from modules.content_plan import config, doctor
    пределы, строки = [], []
    with _подменить([(doctor, "whisper_weights_present", lambda: веса),
                     (config, "has_secret", lambda k: ключ)]):
        doctor._check_reels_autonomy(3, пределы, строки)
    return пределы, строки


def test_доктор_говорит_нет_весов_расшифровки():
    пределы, строки = _вердикт_доктора(веса=False, ключ=True)
    assert len(пределы) == 1, пределы
    assert "САМИ НЕ СОБЕРУТСЯ" in пределы[0], пределы[0]
    assert "распознавания речи" in пределы[0], пределы[0]
    assert not any("собираются САМИ" in с for с in строки), строки


def test_доктор_говорит_нужен_ключ_модели():
    пределы, строки = _вердикт_доктора(веса=True, ключ=False)
    assert len(пределы) == 1, пределы
    assert "С УЧАСТИЕМ ЧЕЛОВЕКА" in пределы[0], пределы[0]
    assert "ANTHROPIC_API_KEY" in пределы[0], пределы[0]
    assert "платная" in пределы[0], "про деньги клиенту не сказали: %s" % пределы[0]


def test_доктор_говорит_соберётся_сам():
    пределы, строки = _вердикт_доктора(веса=True, ключ=True)
    assert пределы == [], "всё на месте, а доктор жалуется: %r" % пределы
    assert any("собираются САМИ" in с for с in строки), строки
    assert any("платное обращение" in с for с in строки), (
        "доктор умолчал, что автосборка стоит денег: %r" % строки)


def test_сеть_действительно_заблокирована():
    """Сам предохранитель обязан работать — иначе все тесты выше ничего не стоят."""
    try:
        socket.create_connection(("example.com", 80), timeout=1)
    except СетьЗапрещена:
        return
    raise AssertionError("блокировка сети НЕ работает — тестам нельзя доверять")


def _прогнать_всё():
    test_сеть_действительно_заблокирована()
    test_без_ключа_слот_остаётся_черновиком()
    test_без_ключа_прогон_плана_не_падает()
    test_с_ключом_блоки_получены_и_слот_собран()
    test_кривой_ответ_модели_не_проходит_валидацию()
    test_ошибка_модели_не_роняет_прогон_плана()
    test_системный_выход_движка_тоже_не_роняет_прогон()
    test_segment_без_ключа_бросает_NoApiKey_до_сети()
    test_промпт_без_контекста_слота_не_меняется()
    test_доктор_говорит_нет_весов_расшифровки()
    test_доктор_говорит_нужен_ключ_модели()
    test_доктор_говорит_соберётся_сам()


if __name__ == "__main__":
    _прогнать_всё()
    print("OK reels_bridge → talk_segment: без ключа слот остаётся черновиком и "
          "модель не зовётся; с ключом блоки получены сами и слот собран; "
          "ошибка модели помечает слот и НЕ роняет прогон плана. Сеть на всём "
          "прогоне была технически заблокирована, ключи из окружения вычищены.")
