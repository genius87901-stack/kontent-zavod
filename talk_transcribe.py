# -*- coding: utf-8 -*-
"""Стадия 2 пайплайна разговорных Reels: видео со спикером -> transcript.json.

WhisperX (faster-whisper + форс-выравнивание wav2vec2) даёт СЛОВА с таймкодами.
Выход — РОВНО тот формат, что ждёт engine_talk.chunk_cues()/render():

    ВЕРХНИЙ УРОВЕНЬ = СПИСОК сегментов (НЕ {"segments": ...}!), каждый:
        {"end": float, "words": [{"w": слово, "s": старт_сек, "e": конец_сек}, ...]}

Почему список, а не объект: engine_talk.render() делает json.load(файл) и сразу
передаёт результат в chunk_cues(), который итерирует его как список сегментов.
Обёртка {"segments": [...]} сломала бы движок (в REELS_TALK_ARCH.md §6 показана
обёртка — это неточность документа, код требует голый массив).

Кэш моделей уводим с диска C: (на боевой машине он почти полон — 13 ГБ, риск R7
в REELS_TALK_ARCH.md): по умолчанию в .cache_models рядом с проектом (диск D:).
Переопределяется переменными окружения HF_HOME / TORCH_HOME до импорта whisperx.
"""
import os
import json
import argparse
import contextlib
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Тяжёлые модели whisper/wav2vec2 держим у проекта (диск D:), а не на C:.
# setdefault — уважает уже заданное окружение, но по умолчанию уводит с C:.
_CACHE = os.path.join(HERE, ".cache_models")
os.environ.setdefault("HF_HOME", _CACHE)
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", os.path.join(_CACHE, "hub"))
os.environ.setdefault("TORCH_HOME", os.path.join(_CACHE, "torch"))
# Windows/кросс-диск: симлинки HF-кэша не создаются -> модели бьются. Копируем.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@contextlib.contextmanager
def _torch_trusted_load():
    """Скоупнутый патч torch.load(weights_only=False) ТОЛЬКО на время загрузки
    наших моделей whisperx/VAD.

    Зачем: начиная с torch 2.6 (у нас 2.8, даунгрейд невозможен — whisperx его
    жёстко требует) torch.load по умолчанию weights_only=True. Чекпоинт VAD
    pyannote, который whisperx.load_model() тянет через Model.from_pretrained,
    содержит объект omegaconf.ListConfig и на weights_only=True рвётся с
    UnpicklingError -> без VAD нет транскрипта -> разговорные Reels не собираются.

    Почему это безопасно: чекпоинт локальный и доверенный — он лежит в нашем
    HF-кэше проекта (.cache_models), а не приходит из недоверенного источника.
    Патч снимается в finally сразу после загрузки, поэтому глобально безопасность
    torch.load НЕ отключается — только на время наших вызовов load_model/align.
    """
    import torch
    _orig_load = torch.load

    def _load_trusted(*args, **kwargs):
        # Форсируем False (а не setdefault): lightning/pyannote передают
        # weights_only=None ЯВНО, а torch трактует None как True. В скоупнутом
        # окне все загрузки — наши доверенные модели, поэтому переопределяем.
        kwargs["weights_only"] = False
        return _orig_load(*args, **kwargs)

    torch.load = _load_trusted
    try:
        yield
    finally:
        torch.load = _orig_load


# ── ОБХОД ЗАГЛУШЕК speechbrain (одно место на весь проект) ───────────────────
# Этот обход нужен ДВОИМ: самой транскрипции (ниже, transcribe()) и доктору
# модуля (modules/engine_media/doctor.py, check_transcription_stack), который
# проверяет, что обход ещё работает. До 21.08.2026 доктор держал СВОЮ копию
# перебора sys.modules (_broken_lazies) — два места одного знания. Теперь обе
# половины живут здесь и вызываются оттуда: сначала
# broken_speechbrain_lazy_modules() — «беда есть?», потом
# neutralize_speechbrain_lazy_imports() — «почини».


def _speechbrain_lazy_module_type():
    """Класс LazyModule speechbrain или None, если пакета/класса нет.

    None — не поломка: speechbrain приезжает зависимостью whisperx, и на
    установке без разговорного слоя его просто нет — чинить нечего.
    """
    try:
        from speechbrain.utils.importutils import LazyModule
    except Exception:
        return None
    return LazyModule


def broken_speechbrain_lazy_modules():
    """Заглушки speechbrain, которые ПРЯМО СЕЙЧАС ломают обращение к __file__.

    Возвращает список модулей-заглушек, у которых getattr(mod, "__file__")
    бросает исключение. Пустой список — обход не нужен либо уже сработал.

    Отдельной публичной функцией — потому что это и есть проверяемый признак
    беды: доктор модуля меряет им, надо ли чинить и помогла ли починка.
    Подробности самой беды — в neutralize_speechbrain_lazy_imports().
    """
    lazy = _speechbrain_lazy_module_type()
    if lazy is None:
        return []
    bad = []
    for mod in list(sys.modules.values()):
        if not isinstance(mod, lazy):
            continue
        try:
            getattr(mod, "__file__", None)
        except Exception:      # noqa: BLE001 — ImportError и всё, что похоже
            bad.append(mod)
    return bad


def neutralize_speechbrain_lazy_imports():
    """Обезвредить «ленивые» модули speechbrain, из-за которых падает загрузка VAD.

    speechbrain (тянется зависимостями whisperx) регистрирует в sys.modules
    объекты-заглушки LazyModule (напр. speechbrain.k2_integration). При обращении
    к любому атрибуту они пытаются доимпортировать целевой модуль — а он требует
    пакет k2, которого у нас нет, и бросают ImportError вместо AttributeError.

    Ловушка: pyannote грузит VAD-чекпоинт через обёртку pytorch_lightning, которая
    зовёт inspect.stack(); та через linecache перебирает sys.modules и делает
    getattr(mod, '__file__') на КАЖДОМ — включая заглушки speechbrain -> ImportError
    прерывает inspect.stack() -> whisperx.load_model падает, транскрипт не строится.

    Чиним точечно: проставляем этим заглушкам обычный атрибут __file__=None. Тогда
    getattr(mod, '__file__') возвращает его напрямую и __getattr__ (а с ним и ленивый
    импорт k2) НЕ срабатывает. Реальные атрибуты по-прежнему грузятся лениво, так что
    сам speechbrain не ломаем — гасим только обращение linecache к __file__.

    Возвращает, сколько заглушек обезврежено (0 — чинить было нечего).
    """
    lazy = _speechbrain_lazy_module_type()
    if lazy is None:
        return 0               # speechbrain не установлен/иная версия
    fixed = 0
    for mod in list(sys.modules.values()):
        if isinstance(mod, lazy) and "__file__" not in mod.__dict__:
            try:
                mod.__file__ = None
                fixed += 1
            except Exception:
                pass  # best-effort: не роняем транскрипцию из-за косметики
    return fixed


#: Старые внутренние имена. Доктор модуля зовёт их сегодня — держим, пока он не
#: перешёл на публичные (см. отчёт по задаче: доктору надо звать
#: broken_speechbrain_lazy_modules() вместо своей копии перебора sys.modules).
_neutralize_speechbrain_lazy_imports = neutralize_speechbrain_lazy_imports
#: Публичное имя скоупнутого патча torch.load — доктор дёргает его из соседнего
#: модуля, а лезть за подчёркивание через границу модуля незачем.
torch_trusted_load = _torch_trusted_load


def _load_audio(video_path, sr=16000):
    """Декодировать дорожку в float32 mono @16кГц — как whisperx.load_audio, но
    через ffmpeg из imageio (в PATH его на машине завода НЕТ, путь берём у
    imageio_ffmpeg — то же правило, что во всём проекте: HANDOFF, «ffprobe/ffmpeg»).
    """
    import subprocess
    import numpy as np
    import imageio_ffmpeg

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ff, "-nostdin", "-threads", "0", "-i", video_path,
           "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le",
           "-ar", str(sr), "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0


def _to_engine_format(wx_segments):
    """WhisperX {word,start,end} -> формат завода {w,s,e} + end на сегмент.

    Слова без таймкодов пропускаем: WhisperX иногда не выравнивает числа и
    иностранные токены и оставляет их без start/end — иначе chunk_cues падает
    на w["s"]. Сегмент без единого выровненного слова тоже отбрасываем.

    W1: ДОБАВЛЯЕМ поле "sc" — уверенность форс-выравнивания wav2vec2
    (whisperx.align кладёт её в w["score"]), которую раньше выбрасывали.
    Низкая уверенность + короткая длительность = запинка/промямленное слово;
    talk_tighten использует это как сигнал, НЕЗАВИСИМЫЙ от словаря паразитов.
    Поле именно ДОБАВЛЯЕТСЯ, существующие w/s/e не переименованы: транскрипт
    читают engine_talk, talk_segment, talk_plan — они берут ключи по имени и
    лишнего поля не замечают. Если score не пришёл (слово не выровнено, чужой
    транскрипт) — ключа просто нет, а не 0.0: «нет данных» ≠ «нулевая
    уверенность», иначе отбор паразитов примет старые файлы за сплошную
    запинку.
    """
    out = []
    for seg in wx_segments:
        words = []
        for w in seg.get("words", []):
            if w.get("start") is None or w.get("end") is None:
                continue
            token = (w.get("word") or "").strip()
            if not token:
                continue
            item = {"w": token,
                    "s": round(float(w["start"]), 3),
                    "e": round(float(w["end"]), 3)}
            if w.get("score") is not None:
                item["sc"] = round(float(w["score"]), 3)
            words.append(item)
        if not words:
            continue
        end = round(float(seg.get("end", words[-1]["e"])), 3)
        out.append({"end": end, "words": words})
    return out


def transcribe(video_path, out_path=None, model_size="medium", language="ru",
               device="cpu", compute_type="int8", batch_size=8, clean=True):
    """Видео -> transcript.json в формате engine_talk. Возвращает (путь, сегменты).

    compute_type="int8" — под CPU (float16 на CPU whisperx не считает).
    model_size: tiny/base/small/medium/large-v3 — компромисс диск-время-точность.

    clean=True — прогнать текст через talk_text_clean.clean_transcript перед
    записью. Раньше распознанный текст уходил в субтитры КАК ЕСТЬ, а субтитр
    вжигается в картинку: огрех оставался в опубликованном ролике навсегда.
    Чистка ДЕТЕРМИНИРОВАННАЯ (правила, не модель) и по построению не трогает
    тайминги — правится только строка `w`, число слов и `s`/`e` те же, поэтому
    караоке-подсветка её не замечает. Выключатель оставлен, чтобы можно было
    получить сырой выход WhisperX для сравнения (`--no-clean`).
    """
    import whisperx  # импорт внутри: тяжёлый, не тормозим соседние стадии

    os.makedirs(_CACHE, exist_ok=True)
    # До первого torch.load моделей: см. функцию — иначе VAD не поднимется.
    neutralize_speechbrain_lazy_imports()

    audio = _load_audio(video_path)

    # Загрузка моделей — под скоупнутым патчем torch.load (см. _torch_trusted_load):
    # без него whisperx на torch>=2.6 падает UnpicklingError на чекпоинте VAD.
    with _torch_trusted_load():
        model = whisperx.load_model(model_size, device, compute_type=compute_type,
                                    language=language)
    result = model.transcribe(audio, batch_size=batch_size, language=language)

    # форс-выравнивание -> слова с таймкодами (<100 мс)
    with _torch_trusted_load():
        align_model, meta = whisperx.load_align_model(language_code=language,
                                                      device=device)
    aligned = whisperx.align(result["segments"], align_model, meta, audio,
                             device, return_char_alignments=False)

    segs = _to_engine_format(aligned["segments"])

    if clean:
        # Импорт здесь, а не наверху: чистка — чистые функции без зависимостей,
        # и её отсутствие не должно ронять транскрипцию целиком.
        import talk_text_clean
        segs = talk_text_clean.clean_transcript(segs)

    if out_path is None:
        base = os.path.splitext(os.path.basename(video_path))[0]
        out_path = os.path.join(os.path.dirname(video_path) or ".",
                                base + "_transcript.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(segs, f, ensure_ascii=False, indent=2)
    return out_path, segs


# Пресеты качества (R41): draft = текущий дефолт medium (байт-в-байт тот же
# путь), final = large-v3 для финальных рендеров. Явный -m/--model сильнее.
QUALITY_PRESETS = {"draft": "medium", "final": "large-v3"}


def main():
    ap = argparse.ArgumentParser(
        description="Видео спикера -> transcript.json (WhisperX, формат engine_talk)")
    ap.add_argument("video", help="путь к видео со спикером")
    ap.add_argument("-o", "--out", default=None,
                    help="куда писать transcript.json (по умолчанию рядом с видео)")
    ap.add_argument("--quality", choices=sorted(QUALITY_PRESETS), default="draft",
                    help="пресет качества: draft=medium (по умолчанию, как раньше), "
                         "final=large-v3 — МЕДЛЕННЕЕ В РАЗЫ на CPU, точнее; "
                         "для финальных рендеров")
    ap.add_argument("-m", "--model", default=None,
                    help="размер whisper-модели: tiny/base/small/medium/large-v3 "
                         "(явное значение переопределяет --quality)")
    ap.add_argument("-l", "--lang", default="ru", help="язык речи (по умолчанию ru)")
    ap.add_argument("--no-clean", action="store_true",
                    help="НЕ чистить распознанный текст правилами (сырой выход "
                         "WhisperX). По умолчанию чистка включена: текст идёт в "
                         "субтитры, а они вжигаются в картинку")
    args = ap.parse_args()

    model_size = args.model or QUALITY_PRESETS[args.quality]
    out, segs = transcribe(args.video, args.out, model_size=model_size,
                           language=args.lang, clean=not args.no_clean)
    nwords = sum(len(s["words"]) for s in segs)
    print("готово: %s  (сегментов: %d, слов: %d)" % (out, len(segs), nwords),
          flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
