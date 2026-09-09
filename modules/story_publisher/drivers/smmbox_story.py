# -*- coding: utf-8 -*-
"""
smmbox_story — общий слой отправки СТОРИС через SmmBox для драйверов площадок.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Через SmmBox сторис уходят на две разные площадки
(Instagram и группа ВКонтакте), и различаются они ровно двумя строками —
кодом площадки и типом адресата. Писать один и тот же HTTP дважды значит
завести две реализации, которые разойдутся: в этом проекте так уже случалось
трижды со списками файлов.

ЗДЕСЬ НЕТ СВОЕГО HTTP. Всё делает `smmbox_post.py`, который уже написан,
проверен на карусели ВК и живёт в коробке: `api_key`, `groups`, `pick_target`,
`upload`, `build_post`, `call`. Второй клиент к тому же сервису — это второй
источник поломок.

🔴 ГЛАВНОЕ, ЧТО НАДО ЗНАТЬ ПРО ЭТОТ ПУТЬ. Режим сторис у SmmBox ЖИВЬЁМ НЕ
ПОДТВЕРЖДЁН. В `smmbox_post.build_options` записано дословно: «Оба задания
сервис ПРИНЯЛ с номером и датой. Отказ выглядит как успех — читать посты у
SmmBox нечем (шесть методов, чтения нет)». Там же — случай, когда появившаяся
через три минуты история оказалась ЧУЖОЙ, и поймали это только тем, что скачали
картинку и посмотрели.

Отсюда правило этого модуля: успешный ответ сервиса возвращается статусом
**`unconfirmed`**, а НЕ `accepted`. «Приняли задание» и «история вышла» — разные
события, и пока у сервиса нет чтения, второе мы утверждать не можем. Первый же
живой прогон с проверкой глазами закроет вопрос; до него завод не имеет права
говорить человеку «опубликовано».

Только stdlib плюс то, что уже тянет `smmbox_post`.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: Расширения, которые считаем картинкой. Всё прочее уходит как видео — так же,
#: как решают остальные драйверы сторис.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _smmbox():
    """Подтянуть smmbox_post из корня установки.

    Модули лежат в `modules/`, а инструмент — в корне; при запуске через
    `python -m story_publisher` корень уже в `sys.path`, но при прямом импорте
    драйвера может и не быть. Добавляем явно, а не надеемся.
    """
    корень = Path(__file__).resolve().parents[3]
    if str(корень) not in sys.path:
        sys.path.insert(0, str(корень))
    import smmbox_post
    return smmbox_post


def is_image(path) -> bool:
    return Path(path).suffix.lower() in _IMAGE_SUFFIXES


def описание(social: str, gtype: str) -> str:
    """Человеческое имя адресата — для сообщений и для намерения."""
    имена = {"io": "Instagram", "vk": "ВКонтакте", "tg": "Telegram"}
    типы = {"user": "личная страница", "group": "сообщество", "page": "страница"}
    return f"{имена.get(social, social)} — {типы.get(gtype, gtype)}"


def publish_story(media_path, caption, cfg, dry_run, *, social, gtype,
                  group_id=None):
    """Отправить сторис через SmmBox. Возвращает словарь-намерение драйвера.

    Статусы те же, что у остальных драйверов: `not_configured`, `dry_run`,
    `unconfirmed`, `error`. Статуса `accepted` здесь нет СОЗНАТЕЛЬНО — см.
    шапку модуля.
    """
    intent = {
        "platform": None,          # проставит вызывающий драйвер
        "media": str(media_path),
        "dry_run": bool(dry_run),
        "via": "smmbox",
        "target": описание(social, gtype),
    }

    try:
        sb = _smmbox()
    except Exception as exc:                       # noqa: BLE001 — важен факт
        intent["status"] = "error"
        intent["detail"] = ("Не удалось подключить smmbox_post (%s). Сторис "
                            "через SmmBox не отправлены." % exc.__class__.__name__)
        return intent

    ключ = cfg.secret("smmbox_api_key")
    if not ключ:
        intent["status"] = "not_configured"
        intent["missing"] = ["ключ SmmBox (smmbox_api_key)"]
        intent["detail"] = (
            "Не заполнен ключ SmmBox — сторис в «%s» публиковаться не будут. "
            "Подключается один раз: скажите помощнику «подключи сммбокс»."
            % intent["target"])
        return intent

    вид = "photo" if is_image(media_path) else "video"

    if dry_run:
        intent["status"] = "dry_run"
        intent["media_kind"] = вид
        intent["detail"] = (
            "Готово к отправке в «%s» историей (%s). Пробный прогон — ничего "
            "не отправлено." % (intent["target"], вид))
        return intent

    # ── боевая отправка ──────────────────────────────────────────────────────
    try:
        адресат = sb.pick_target(ключ, want_id=group_id, social=social,
                                 gtype=gtype)
    except SystemExit as exc:
        # pick_target выходит с человеческим текстом, если адресата нет.
        intent["status"] = "not_configured"
        intent["detail"] = ("Адресат «%s» в SmmBox не подключён. %s"
                            % (intent["target"], str(exc)[:300]))
        return intent
    except Exception as exc:                       # noqa: BLE001
        intent["status"] = "error"
        intent["detail"] = ("Не удалось выбрать адресата в SmmBox (%s)."
                            % exc.__class__.__name__)
        return intent

    try:
        загружено = sb.upload(ключ, Path(media_path), вид)
        запись = sb.build_post(адресат, text=caption or "", media=загружено,
                               media_kind=вид, when=None, as_clip=False,
                               autodelete_min=None, as_story=True)
        ответ = sb.call("POST", "/posts", key=ключ, payload=запись)
    except Exception as exc:                       # noqa: BLE001
        intent["status"] = "error"
        intent["detail"] = ("SmmBox не принял историю для «%s» (%s)."
                            % (intent["target"], exc.__class__.__name__))
        return intent

    # 🔴 Именно здесь и нигде иначе решается честность этого пути.
    # Сервис ответил «принято» — это ВСЁ, что мы знаем. Прочитать, вышла ли
    # история, у SmmBox нечем. Поэтому статус «не подтверждено», а в тексте
    # человеку прямо сказано, что проверить надо глазами.
    intent["status"] = "unconfirmed"
    intent["response"] = {k: ответ.get(k) for k in ("id", "date", "status")
                          if isinstance(ответ, dict)}
    intent["detail"] = (
        "SmmBox принял историю для «%s» (задание %s). ⚠️ Это НЕ значит, что она "
        "вышла: читать опубликованное у сервиса нечем, поэтому подтвердить "
        "доставку нельзя. Откройте профиль и посмотрите глазами."
        % (intent["target"],
           (ответ or {}).get("id", "без номера") if isinstance(ответ, dict)
           else "без номера"))
    return intent
