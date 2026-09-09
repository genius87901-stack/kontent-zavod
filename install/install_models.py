# -*- coding: utf-8 -*-
"""
install_models — отдельный шаг установки: предзаклад весов WhisperX в коробку,
чтобы офлайн-клиент собирал разговорные Reels без обращения в сеть.

ЗАЧЕМ ОТДЕЛЬНЫМ ШАГОМ (тот же принцип, что у install_music).
Веса распознавания речи (WhisperX: faster-whisper + модель форс-выравнивания
wav2vec2 + VAD) — это МОДЕЛИ (гигабайты бинарных данных), а не код. Класть их в
стартовый .zip нельзя: архив раздуется на ~2.5–3 ГБ. Поэтому они, как и банк
музыки, доустанавливаются ЭТИМ отдельным шагом, идемпотентно, и НИКОГДА не
входят в git/архив (папка `.cache_models/` в .gitignore, сборщик build_box её
не кладёт).

ЧЕСТНО ПРО СЕТЬ (два разведённых момента).
  • САМА первичная загрузка весов требует интернета РОВНО ОДИН РАЗ. Это явный
    ОНЛАЙН-шаг подготовки. Его выполняют там, где сеть есть: у владельца на маке
    при подготовке коробки ЛИБО у клиента на этапе с интернетом ДО офлайн-работы.
  • ПОВТОРНЫЙ и ОФЛАЙН запуск — network-free. Если веса уже лежат в `.cache_models`,
    шаг НЕ импортирует whisperx и НЕ трогает сеть вовсе: он видит модели на диске
    и сразу выходит с сообщением «уже на месте». Так первый реальный разбор
    разговорного видео у клиента не полезет в сеть за моделями (находка BOX-14).

КУДА КЛАДЁТСЯ (ровно туда, откуда движок берёт офлайн).
Источник правды по расположению кэша — `talk_transcribe.py` (стадия 2 движка).
Он на импорте выставляет HF_HOME / HUGGINGFACE_HUB_CACHE / TORCH_HOME на
`<корень>/.cache_models`. Этот шаг ИМПОРТИРУЕТ talk_transcribe и берёт его
`_CACHE` — чтобы installer и движок гарантированно указывали в одно место, без
расхождения. Прогрев мы делаем ТЕМ ЖЕ загрузчиком движка (whisperx.load_model /
load_align_model под скоуп-патчем talk_transcribe), поэтому раскладка кэша
байт-в-байт та, что движок потом и читает.

ЧТО ИМЕННО КАЧАЕТСЯ (что реально использует talk_transcribe):
  1. Whisper ASR — faster-whisper, репозиторий `Systran/faster-whisper-<size>`
     (по умолчанию size=medium ~1.5 ГБ; tiny ~75 МБ / base ~145 МБ / small ~0.5 ГБ /
     large-v3 ~3.1 ГБ). Лицензия весов Whisper — MIT.
  2. Форс-выравнивание — wav2vec2 под язык речи. Для ru whisperx берёт
     `jonatasgrosman/wav2vec2-large-xlsr-53-russian` (~1.2 ГБ), лицензия Apache-2.0.
  3. VAD (детектор речи) — чекпоинт, который whisperx тянет сам при load_model
     (~15–20 МБ), лицензия MIT (pyannote). Токен HuggingFace НЕ нужен —
     whisperx берёт VAD со своего зеркала, а не из gated-репозитория.
Итого для дефолта (medium + ru): ~2.5–3 ГБ, один раз.
Сам whisperx — лицензия BSD-2-Clause. Пакеты ставит модуль engine-media (его pip),
ЭТОТ шаг pip не трогает — только докачивает данные-веса уже установленным whisperx.

Идемпотентность: если веса на месте — no-op (и network-free). Повторный запуск
безопасен. Только stdlib для служебной части; whisperx/torch импортируются лениво
и лишь когда реально нужно качать. Без абсолютных путей, без input(), без секретов.

КТО И КОГДА ЭТОТ ШАГ ЗАПУСКАЕТ (BOX-58, 23.08.2026).
Раньше шаг существовал ТОЛЬКО как «необязательный» пункт install/stages.json и в
обычную установку не попадал никогда: мастер /install идёт по этапам 1–3, а
optional_steps по собственному примечанию «не входят в next-цепочку». Итог —
у всех клиентов веса отсутствовали, разговорные ролики были недоступны, и никто
об этом не спрашивал. Теперь шаг встроен в ОБЫЧНУЮ установку этапа 2 (stages.json,
поле first_step этапа 2) и выполняется ПО ЯВНОМУ ОТВЕТУ КЛИЕНТА:

    1) мастер задаёт вопрос из install/questions.overlay.json
       (wizard_questions.speech_recognition) — человеческими словами, с честным
       «+ ~2,7 ГБ скачивания»;
    2) ответ записывается СЮДА:  install_models.py --choice yes|later|no
       (пишется в config/factory.config.json, ключ speech_recognition; сети не
       трогает вовсе);
    3) при ответе «да» мастер запускает саму загрузку (обычный запуск скрипта).

Отказ — штатный путь: установка идёт дальше, завод работает, недоступны только
разговорные ролики. Именно ради этого различия и заведён ключ выбора: проверка
--doctor обязана отличать «клиент так решил» от «должно было встать и не встало».

Запуск (из корня установки):
    python install/install_models.py                 # прогреть (ОНЛАЙН, если весов нет)
    python install/install_models.py --model small   # другой размер whisper
    python install/install_models.py --lang en        # другой язык выравнивания
    python install/install_models.py --status         # что с весами сейчас (по-русски)
    python install/install_models.py --json           # машинная сводка для мастера
    python install/install_models.py --choice no      # записать ответ клиента (без сети)
    python install/install_models.py --doctor         # проверка: отказ или недоделка
    python install/install_models.py --verify         # доказать офлайн-готовность
                                                       #   (грузит модели с HF_HUB_OFFLINE=1)

Код возврата: 0 — готово / нечего делать / корректно объясняет, что нужен онлайн-шаг;
              2 — плохой аргумент (--model / --choice);
              3 — пробовали качать онлайн, но не вышло (нет сети / нет whisperx).
Отдельная шкала у --doctor (CONTRACT.md §2 — код это СТЕПЕНЬ беды):
              0 — веса на месте ЛИБО клиент осознанно отказался/отложил;
              2 — урезанный режим НЕ по выбору клиента: согласился, а весов нет,
                  или вопрос ему вообще не задавали (старая коробка).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

# Консоль русской Windows — cp1251, и ЛЮБОЙ значок вне неё (→ ✓ ✗ • ⚠) роняет
# скрипт UnicodeEncodeError прямо в лицо клиенту. Переводим свой вывод в utf-8
# с заменой непечатаемого: ни один текст мастера больше не может убить установку.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # старый питон или подменённый stdout — не критично
    pass


# Дефолты синхронны с движком (modules/content_plan/reels_bridge:
# _DEFAULT_TR_MODEL="medium", _DEFAULT_TR_LANG="ru").
DEFAULT_MODEL = "medium"
DEFAULT_LANG = "ru"
VALID_MODELS = ("tiny", "base", "small", "medium", "large-v2", "large-v3")

#: Секунд на сетевую операцию в режиме --verify. Сеть там не нужна вовсе, так что
#: это не «мало времени на скачивание», а потолок ожидания перед честным провалом.
_OFFLINE_NET_TIMEOUT = 20

# Ориентировочные размеры весов whisper (для честной сметы человеку).
_WHISPER_SIZE_HINT = {
    "tiny": "~75 МБ", "base": "~145 МБ", "small": "~0.5 ГБ",
    "medium": "~1.5 ГБ", "large-v2": "~3.1 ГБ", "large-v3": "~3.1 ГБ",
}
# Язык -> HF-репозиторий wav2vec2 для форс-выравнивания (дефолты whisperx).
# Держим свой минимум как запас: если whisperx импортируется, его карта главнее.
_ALIGN_HF_FALLBACK = {
    "ru": "jonatasgrosman/wav2vec2-large-xlsr-53-russian",
    "en": "facebook/wav2vec2-base-960h",
    "uk": "Yehor/wav2vec2-xls-r-300m-uk-with-small-lm",
}

# ── ответ клиента: нужны ли ему разговорные ролики ────────────────────────────
#: Ключ в config/factory.config.json, куда ложится ответ на вопрос мастера
#: «будете снимать ролики, где говорите на камеру?». Сама формулировка вопроса
#: живёт в install/questions.overlay.json (wizard_questions.speech_recognition) —
#: тут только машинное значение, чтобы проверка знала: молчание это отказ или
#: недоделка. Ключ несекретный и человеку в отчётах не мешает.
CHOICE_KEY = "speech_recognition"
CHOICE_YES = "yes"        # «да, скачать» — веса ОБЯЗАНЫ встать
CHOICE_LATER = "later"    # «не сейчас» — отложено, спросим при следующей установке
CHOICE_NO = "no"          # «не нужно» — осознанный отказ, чинить нечего
VALID_CHOICES = (CHOICE_YES, CHOICE_LATER, CHOICE_NO)

#: Человеческие подписи состояний для --status/--doctor (без слов «веса»/«модель»
#: там, где текст читает клиент, — но внутри кода зовём вещи своими именами).
_CHOICE_HUMAN = {
    CHOICE_YES: "клиент согласился подключить распознавание речи",
    CHOICE_LATER: "клиент отложил распознавание речи («не сейчас»)",
    CHOICE_NO: "клиент отказался от распознавания речи",
}


def find_root(explicit: str | None = None) -> Path:
    """Корень установки — тот же контракт поиска, что и в остальных install-скриптах."""
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "install" else Path.cwd()


def cache_dir(root: Path) -> Path:
    """Каталог кэша моделей — БЕРЁМ У ДВИЖКА (talk_transcribe._CACHE), а не угадываем.

    Импорт talk_transcribe на модульном уровне выставляет HF_HOME/TORCH_HOME на
    <root>/.cache_models. Так installer и движок указывают в одно место by design.
    Если talk_transcribe недоступен (движок ещё не в дереве) — падаем на тот же
    путь по контракту (<root>/.cache_models).
    """
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import talk_transcribe as tt  # лёгкий импорт: torch/whisperx у него ленивы
        return Path(tt._CACHE)
    except Exception:
        return root / ".cache_models"


def _config_value(root: Path, key: str) -> str | None:
    cfg = root / "config" / "factory.config.json"
    if not cfg.is_file():
        return None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    val = data.get(key)
    return str(val).strip() or None if val else None


def read_choice(root: Path) -> str | None:
    """Ответ клиента про разговорные ролики (yes/later/no) или None, если не спрашивали."""
    val = (_config_value(root, CHOICE_KEY) or "").strip().lower()
    return val if val in VALID_CHOICES else None


def write_choice(root: Path, value: str) -> Path:
    """Записать ответ клиента в config/factory.config.json. Сети не касается.

    Пишем ровно так же, как install/setup_config.py: читаем существующий конфиг,
    ДОПОЛНЯЕМ его (ничего чужого не теряя) и кладём обратно. Иначе повторная
    установка стёрла бы ответы других вопросов.
    """
    cfg_dir = root / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "factory.config.json"
    merged: dict = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(existing, dict):
                merged.update(existing)
        except (OSError, json.JSONDecodeError):
            pass          # битый конфиг не роняет установку — перезапишем своим
    merged[CHOICE_KEY] = value
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8")
    return path


def resolve_model_lang(root: Path, cli_model: str | None,
                       cli_lang: str | None) -> tuple[str, str]:
    """Размер whisper и язык: аргумент → env → config → дефолт (как у музыки)."""
    model = (cli_model
             or os.environ.get("FACTORY_WHISPER_MODEL", "").strip()
             or _config_value(root, "transcribe_model")
             or DEFAULT_MODEL)
    lang = (cli_lang
            or os.environ.get("FACTORY_WHISPER_LANG", "").strip()
            or _config_value(root, "language")
            or DEFAULT_LANG)
    return model.strip(), lang.strip()


def _align_repo_for(lang: str) -> str | None:
    """HF-репозиторий wav2vec2 для языка. Приоритет — карта whisperx, иначе запас."""
    try:
        import whisperx.alignment as _al  # noqa
        m = getattr(_al, "DEFAULT_ALIGN_MODELS_HF", {}) or {}
        if lang in m:
            return m[lang]
        # для некоторых языков (напр. en) whisperx берёт torchaudio-бандл, не HF
        t = getattr(_al, "DEFAULT_ALIGN_MODELS_TORCH", {}) or {}
        if lang in t:
            return None  # это torch-бандл: он ляжет в TORCH_HOME, не как HF-репо
    except Exception:
        pass
    return _ALIGN_HF_FALLBACK.get(lang)


def _has_dir(cache: Path, needle: str) -> bool:
    """Есть ли в кэше распакованный HF-репозиторий с таким сегментом имени."""
    if not cache.exists():
        return False
    return any(needle in p.name for p in cache.rglob("models--*") if p.is_dir())


def cached_whisper_sizes(cache: Path) -> list[str]:
    """Какие размеры whisper РЕАЛЬНО лежат в кэше (не только запрошенный).

    Нужна, чтобы проверка не врала в обратную сторону: на машине сборки в кэше
    лежит «small», а спрашивают про «medium» — честный ответ «есть small, нужен
    medium», а не «весов нет вовсе».
    """
    if not cache.exists():
        return []
    pref = "models--Systran--faster-whisper-"
    out = set()
    for p in cache.rglob("models--Systran--faster-whisper-*"):
        if p.is_dir():
            out.add(p.name[len(pref):])
    return sorted(out)


def models_present(root: Path, model: str, lang: str) -> dict:
    """ГЛАВНАЯ функция готовности: определяет, на месте ли веса (без сети, быстро).

    Смотрит на диск в .cache_models. whisper обязателен; align — обязателен, если
    язык кладётся HF-репозиторием (для torch-бандла считаем «покрыт движком»).
    Возвращает разложенную сводку — её же печатает status_text() по-русски.
    """
    cache = cache_dir(root)
    # HF маскирует '/' в имени репо как '--': org/name -> models--org--name.
    whisper_needle = "Systran--faster-whisper-" + model
    whisper_ok = _has_dir(cache, whisper_needle)

    align_repo = _align_repo_for(lang)
    if align_repo is None:
        # язык на torchaudio-бандле: отдельного HF-репо нет, грузится с whisper-стеком
        align_needle = None
        align_ok = True
        align_kind = "torch-бандл (в TORCH_HOME, отдельно не проверяется)"
    else:
        align_needle = align_repo.replace("/", "--")
        align_ok = _has_dir(cache, align_needle)
        align_kind = align_repo

    ready = whisper_ok and align_ok
    choice = read_choice(root)
    state, code = _verdict(ready, choice)
    return {
        "cache_dir": str(cache),
        "cache_exists": cache.exists(),
        "model": model,
        "lang": lang,
        "whisper_repo": "Systran/faster-whisper-" + model,
        "whisper_present": whisper_ok,
        "whisper_cached_sizes": cached_whisper_sizes(cache),
        "align_repo": align_kind,
        "align_present": align_ok,
        "ready": ready,
        "size_hint": _WHISPER_SIZE_HINT.get(model, "?"),
        # ── чем отказ отличается от поломки ──
        "choice": choice,                 # yes / later / no / null (не спрашивали)
        "state": state,                   # ready / declined / deferred / missing / not_asked
        "doctor_code": code,              # 0 — норма, 2 — недоделка (см. --doctor)
    }


def _verdict(ready: bool, choice: str | None) -> tuple[str, int]:
    """Состояние шага и код проверки. ГЛАВНОЕ РАЗЛИЧЕНИЕ этого файла.

    Пока шаг был «необязательным», у всех клиентов было одно и то же «весов нет»
    — и нельзя было понять, отказался человек или установка недоделана. Теперь:

      ready     — веса на месте (что бы человек ни отвечал) → код 0;
      declined  — сказал «не нужно»                          → код 0 (чинить нечего);
      deferred  — сказал «не сейчас»                          → код 0 (спросим снова);
      missing   — сказал «да», а весов нет                    → код 2 (НЕДОДЕЛКА);
      not_asked — весов нет и вопроса не было                 → код 2 (НЕДОДЕЛКА).
    """
    if ready:
        return "ready", 0
    if choice == CHOICE_NO:
        return "declined", 0
    if choice == CHOICE_LATER:
        return "deferred", 0
    if choice == CHOICE_YES:
        return "missing", 2
    return "not_asked", 2


def _whisperx_available() -> bool:
    try:
        import whisperx  # noqa
        return True
    except Exception:
        return False


def prime(root: Path, model: str, lang: str,
          offline: bool = False) -> tuple[int, list[str]]:
    """Прогреть кэш весов. Возвращает (код_возврата, строки_отчёта).

    offline=False (по умолчанию): если весов нет — ОНЛАЙН-докачка тем же
    загрузчиком, что и у движка. Если веса уже есть — no-op, В СЕТЬ НЕ ЛЕЗЕМ.
    offline=True (--verify): принудительно HF_HUB_OFFLINE=1 и грузим модели, чтобы
    ДОКАЗАТЬ офлайн-готовность (network-free). Загрузка из сети при этом запрещена.
    """
    out: list[str] = []
    st = models_present(root, model, lang)

    # Идемпотентность + офлайн-гарантия: веса на месте → выходим ДО импорта whisperx.
    if st["ready"] and not offline:
        out.append(f"Веса WhisperX уже на месте (модель «{model}», язык «{lang}»).")
        out.append(f"Кэш: {st['cache_dir']}. В сеть не обращаюсь — повтор network-free.")
        return 0, out

    # Проверять офлайн-готовность НЕЧЕГО, если весов на диске нет: доказывать нужно
    # поднятие имеющихся весов, а не отсутствие. Выходим ДО импорта whisperx/torch —
    # он тянет гигабайты с диска и, несмотря на HF_HUB_OFFLINE=1, часть загрузчиков
    # всё равно уходит на CDN HuggingFace и вешает установку насмерть (замерено:
    # 23 минуты живого соединения без единого байта в кэш). Коробка едет клиенту
    # БЕЗ весов, поэтому для него это штатный путь, а не ошибка.
    if offline and not st["ready"]:
        out.append(f"Весов на диске нет (модель «{model}», язык «{lang}») — "
                   f"проверять офлайн-готовность пока нечего.")
        out.append("Это НЕ ошибка: веса докачиваются одним онлайн-шагом там, где есть "
                   "интернет — python install/install_models.py")
        out.append(f"Кэш моделей: {st['cache_dir']}. В сеть не обращаюсь.")
        return 0, out

    if not _whisperx_available():
        # Движок/пакеты ещё не поставлены (сначала ставится модуль engine-media).
        out.append("Пакет whisperx не установлен — веса докачать нечем.")
        out.append("Сначала поставьте движок (этап 1, модуль engine-media), затем "
                   "повторите этот шаг. Данные-веса докачивает уже установленный whisperx; "
                   "сам pip этот шаг не трогает.")
        return 3, out

    # Реальная загрузка — тем же путём, что и движок (общий источник правды).
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import talk_transcribe as tt
    except Exception as ex:  # pragma: no cover - защитный
        out.append(f"Не удалось импортировать движок транскрипции talk_transcribe: {ex}")
        return 3, out

    os.makedirs(st["cache_dir"], exist_ok=True)

    if offline:
        # Доказательство офлайн: любая попытка сети должна упасть, а не тихо скачать.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        # Страховка: HF_HUB_OFFLINE перекрывает не все пути загрузки (часть моделей
        # тянется мимо hub — urllib/torch.hub), а сокет без таймаута висит вечно и
        # уносит с собой установщик. В офлайн-проверке сеть не нужна ВООБЩЕ, поэтому
        # любое обращение обязано упасть быстро и с понятным текстом.
        socket.setdefaulttimeout(_OFFLINE_NET_TIMEOUT)
        out.append("Проверка ОФЛАЙН (HF_HUB_OFFLINE=1): гружу модели без сети…")
    else:
        out.append(f"Онлайн-подготовка: докачиваю веса WhisperX "
                   f"(whisper «{model}» {st['size_hint']} + выравнивание «{lang}»). "
                   f"Нужен интернет, один раз.")

    import whisperx
    tt._neutralize_speechbrain_lazy_imports()
    try:
        # load_model скачивает+инстанцирует whisper И VAD (VAD — под torch-патчем).
        with tt._torch_trusted_load():
            whisperx.load_model(model, "cpu", compute_type="int8", language=lang)
        # выравнивание под язык речи (для ru — wav2vec2 xlsr russian)
        with tt._torch_trusted_load():
            whisperx.load_align_model(language_code=lang, device="cpu")
    except Exception as ex:
        msg = str(ex)
        if offline:
            out.append(f"Офлайн-загрузка не удалась: {msg[:200]}")
            out.append("Значит, веса ещё НЕ полны для офлайна — прогоните онлайн-шаг "
                       "(python install/install_models.py) там, где есть интернет.")
        else:
            out.append(f"Онлайн-докачка не удалась: {msg[:200]}")
            out.append("Проверьте интернет и повторите. Ничего платного не вызывается — "
                       "модели берутся из публичных репозиториев HuggingFace, без токена.")
        return 3, out

    st2 = models_present(root, model, lang)
    if offline:
        out.append("Готово: модели поднялись БЕЗ сети — офлайн-готовность подтверждена.")
    else:
        out.append("Готово: веса на месте, кэш прогрет.")
    out.append(f"Кэш: {st2['cache_dir']}. Теперь разбор разговорного видео пойдёт офлайн.")
    out.append("Повторный запуск безопасен и network-free (веса уже на диске).")
    return 0, out


def doctor_text(root: Path, model: str, lang: str) -> tuple[int, str]:
    """Проверка для клиента: отказ или недоделка. Возвращает (код, текст).

    Пишем человеческим языком и БЕЗ слов «веса»/«модель» — как требует
    CONTRACT.md §2. Внутренние подробности (какой репозиторий, какой кэш) живут
    в --status и --json, клиенту тут они не нужны.
    """
    st = models_present(root, model, lang)
    state = st["state"]
    if state == "ready":
        return 0, ("OK  Разговорные ролики: распознавание речи установлено — завод "
                   "разбирает вашу речь прямо на этом компьютере, интернет для "
                   "этого не нужен.")
    if state == "declined":
        return 0, ("i   Разговорные ролики ВЫКЛЮЧЕНЫ ПО ВАШЕМУ ВЫБОРУ: при установке "
                   "вы сказали, что ролики с речью в кадре вам не нужны, и мы "
                   "ничего не скачивали. Это не поломка — остальное работает "
                   "полностью. Передумаете — скажите «доустанови распознавание речи».")
    if state == "deferred":
        return 0, ("i   Разговорные ролики пока выключены: вы отложили их «на потом» "
                   "(скачивание около 2,7 ГБ). Это не поломка — весь остальной завод "
                   "работает. Скажете «доустанови распознавание речи» — вернёмся к "
                   "этому за один шаг; при следующей установке я спрошу сам.")
    if state == "missing":
        return 2, ("!   НЕ ДОДЕЛАНО, и это НЕ ваш выбор: вы соглашались подключить "
                   "разговорные ролики, но распознавание речи на компьютер так и не "
                   "легло — почти всегда это оборвавшееся скачивание. Ролики с речью "
                   "в кадре сейчас не соберутся. Лечится повтором на живом интернете: "
                   "«доустанови распознавание речи» — докачается только недостающее.")
    return 2, ("!   Разговорные ролики не подключены, и вас об этом даже не спросили "
               "(коробка ставилась старым порядком). Ролики с речью в кадре не "
               "соберутся, всё остальное работает. Скажите «доустанови распознавание "
               "речи» — задам один вопрос и, если согласитесь, скачаю (около 2,7 ГБ, "
               "один раз).")


def _choice_line(st: dict) -> str:
    """Строка про ответ клиента для служебной сводки --status."""
    if st["choice"] is None:
        if st["ready"]:
            return ("Ответ клиента не записан, но веса на месте — проверка считает "
                    "разговорные ролики рабочими.")
        return ("Ответ клиента: НЕ СПРАШИВАЛИ (в config/factory.config.json нет ключа "
                f"{CHOICE_KEY}). Для проверки это недоделка установки, а не отказ.")
    return (f"Ответ клиента: {st['choice']} — {_CHOICE_HUMAN[st['choice']]} "
            f"(config/factory.config.json, ключ {CHOICE_KEY}).")


def status_text(root: Path, model: str, lang: str) -> str:
    st = models_present(root, model, lang)
    lines = ["=== ШАГ УСТАНОВКИ: ВЕСА РАСПОЗНАВАНИЯ РЕЧИ (WhisperX) ===", ""]
    if st["ready"]:
        lines.append(f"Готово: веса на месте (модель «{model}», язык «{lang}»).")
        lines.append("Разговорные Reels собираются ОФЛАЙН — в сеть за моделями не лезем.")
    else:
        lines.append(f"Весов пока нет (или неполны) для: модель «{model}», язык «{lang}».")
        miss = []
        if not st["whisper_present"]:
            miss.append(f"whisper «{model}» ({st['size_hint']}) — {st['whisper_repo']}")
        if not st["align_present"]:
            miss.append(f"выравнивание «{lang}» — {st['align_repo']}")
        if miss:
            lines.append("Не хватает: " + "; ".join(miss) + ".")
        other = [s for s in st["whisper_cached_sizes"] if s != model]
        if other:
            # Не даём проверке врать в обратную сторону: «весов нет» при полном
            # кэше другого размера — это неправда и приводит к лишней закачке.
            lines.append("В кэше при этом ЛЕЖИТ whisper другого размера: "
                         + ", ".join(f"«{s}»" for s in other)
                         + f". Запрошен «{model}» — если хватит имеющегося, запустите "
                           f"шаг с --model {other[0]}.")
        lines.append("")
        lines.append("Это НЕ ошибка. Нужен ОДИН онлайн-шаг подготовки (там, где есть "
                     "интернет): python install/install_models.py")
        lines.append("После него первый и все последующие разборы видео идут офлайн.")
    lines.append("")
    lines.append(_choice_line(st))
    lines.append(f"Состояние шага: {st['state']} (проверка --doctor даст код "
                 f"{st['doctor_code']}: 0 — норма или осознанный отказ, 2 — недоделка).")
    lines.append("")
    lines.append(f"Кэш моделей: {st['cache_dir']} (тот же, что читает движок "
                 "talk_transcribe; в git/архив НЕ входит — как и банк музыки).")
    lines.append("Итого дефолт (medium+ru) ≈ 2,7 ГБ: whisper medium ~1,5 ГБ + "
                 "выравнивание речи под русский ~1,2 ГБ + VAD ~20 МБ. Качается один раз. "
                 "Whisper — MIT, wav2vec2(ru) — Apache-2.0, VAD — MIT, whisperx — BSD-2. "
                 "Платных API нет, токен HuggingFace не нужен.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Отдельный шаг установки: предзаклад весов WhisperX (офлайн-транскрипция).")
    ap.add_argument("--root", help="Корень установки (по умолчанию — авто-поиск).")
    ap.add_argument("--model", help=f"Размер whisper (по умолчанию {DEFAULT_MODEL}).")
    ap.add_argument("--lang", help=f"Язык речи для выравнивания (по умолчанию {DEFAULT_LANG}).")
    ap.add_argument("--status", action="store_true", help="Русская сводка готовности весов.")
    ap.add_argument("--json", action="store_true", help="Машинная сводка (для мастера).")
    ap.add_argument("--verify", action="store_true",
                    help="Доказать офлайн-готовность: грузит модели с HF_HUB_OFFLINE=1.")
    ap.add_argument("--choice", choices=VALID_CHOICES,
                    help="Записать ответ клиента про разговорные ролики "
                         "(yes/later/no). Ничего не качает и в сеть не ходит.")
    ap.add_argument("--doctor", action="store_true",
                    help="Проверка по-русски: отличает осознанный отказ клиента "
                         "(код 0) от недоделанной установки (код 2).")
    args = ap.parse_args(argv)

    root = find_root(args.root)
    model, lang = resolve_model_lang(root, args.model, args.lang)

    if args.model and model not in VALID_MODELS:
        print(f"Неизвестный размер модели: {model}. "
              f"Допустимо: {', '.join(VALID_MODELS)}.")
        return 2

    # Запись ответа клиента — САМОСТОЯТЕЛЬНЫЙ, network-free режим. Мастер зовёт
    # его сразу после вопроса, ДО долгой закачки: тогда оборвавшееся скачивание
    # честно читается как «согласился, но не встало», а не как отказ.
    if args.choice:
        path = write_choice(root, args.choice)
        print(f"Ответ записан: {args.choice} — {_CHOICE_HUMAN[args.choice]}.")
        try:
            print(f"Файл: {path.relative_to(root)}")
        except ValueError:                     # конфиг вне корня — печатаем как есть
            print(f"Файл: {path}")
        if args.choice == CHOICE_YES:
            print("Дальше нужен сам онлайн-шаг: python install/install_models.py")
        else:
            print("Ничего не скачиваю. Установка идёт дальше, завод работает — "
                  "недоступны только ролики с речью в кадре.")
        if not (args.doctor or args.status or args.json):
            return 0
        print("")                              # разделитель перед сводкой ниже

    if args.json:
        print(json.dumps(models_present(root, model, lang), ensure_ascii=False, indent=2))
        return 0
    if args.doctor:
        code, text = doctor_text(root, model, lang)
        print(text)
        return code
    if args.status:
        print(status_text(root, model, lang))
        return 0

    code, report = prime(root, model, lang, offline=args.verify)
    for line in report:
        print(line)
    # Веса легли по явной команде — приводим ответ клиента в соответствие с
    # фактом, иначе проверка вечно писала бы «отказался» при рабочей функции.
    if code == 0 and not args.verify:
        if models_present(root, model, lang)["ready"] and read_choice(root) != CHOICE_YES:
            write_choice(root, CHOICE_YES)
    return code


if __name__ == "__main__":
    sys.exit(main())
