# -*- coding: utf-8 -*-
"""Видят ли ролик завода в TikTok посторонние люди (замер 28.08.2026).

ЗАЧЕМ. Публикации завода в TikTok набирают ровно ноль просмотров. При этом
сервис публикации отвечает `success: true` и отдаёт НАСТОЯЩУЮ ссылку — а
посторонний человек по этой ссылке ролик не открывает. То есть отчёт об успехе
ничего не доказывает: завод сегодня не умеет отличить вышедший ролик от
невышедшего и записывает в журнал успех там, где для зрителя не случилось
ничего.

ЧЕМ ПРОВЕРЯЕМ. Открытый эмбед-эндпоинт TikTok — тот самый, которым чужие сайты
получают карточку ролика для вставки. Ключ не нужен, вход не нужен: он отвечает
ровно то, что видно ПОСТОРОННЕМУ.

    https://www.tiktok.com/oembed?url=<ссылка на ролик>

Замер 28.08.2026, руками:
  * чужой публичный ролик (`.../@dan.ragan/video/7294996907393371435`) —
    ответ 200 и карточка с названием, автором и картинкой-обложкой;
  * наши ролики (`.../@chirkova.valeria/video/7678378480605711633` и
    `.../7677504879962623249`) — ответ 400, карточки нет.

ЧЕГО ЭТА ПРОВЕРКА НЕ ДЕЛАЕТ — ЧЕСТНО. Тот же самый отказ 400 приходит и на
удалённый ролик, и на ролик, которого никогда не было, и на опечатку в ссылке.
Значит проверка отвечает на вопрос «видят ли это люди», и ТОЛЬКО на него. Она
НЕ говорит, почему не видят: теневой бан, модерация, ролик снят, ссылка кривая —
всё это здесь неразличимо. Выдавать её за диагноз причины нельзя ни в коде, ни в
тексте человеку.

ТРИ ИСХОДА, А НЕ ДВА. Кроме «виден» и «не виден» обязателен третий — «проверить
не удалось» (сети нет, таймаут, служба ответила непонятным). Молча записать
«не виден» из-за оборванной сети — это ровно та же ложь, от которой мы лечимся,
только с другим знаком.

Только stdlib: модуль `publishing` едет клиенту, сторонних зависимостей в нём
быть не должно.

Коды выхода: 0 — все ролики видны; 1 — есть невидимые (или проверять нечего);
2 — что-то проверить не удалось, итог неполный.

Запуск:
    PYTHONPATH=modules PY -m publishing.tiktok_visible --url https://...
    PYTHONPATH=modules PY -m publishing.tiktok_visible --file ссылки.txt
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OEMBED = "https://www.tiktok.com/oembed"

#: Секунд на один запрос. Эндпоинт лёгкий (отдаёт карточку, а не видео), но
#: проверка идёт пачкой перед выпуском — зависнуть на минуте нельзя.
DEFAULT_TIMEOUT = 12

#: Пауза между роликами в пачке. Это ЧУЖОЙ бесплатный сервис без объявленного
#: лимита: долбить его очередью из полусотни ссылок подряд нельзя — отрежут, и
#: мы получим пачку «проверить не удалось» на ровном месте.
DEFAULT_PAUSE = 1.0

# Исходы. Значения латиницей — это внутренние коды, человеку они не показываются
# никогда: наружу идёт только `render()`.
VIDEN = "viden"                  # посторонний открывает ролик
NE_VIDEN = "ne_viden"            # посторонний ролик НЕ открывает
NE_PROVERENO = "ne_provereno"    # мы не знаем: не дошли до ответа


# --- точка подмены транспорта для ОФЛАЙН-ТЕСТОВ ------------------------------
# Приём тот же, что в `analytics/upload_post.py`: тесты подставляют сюда функцию
# `fn(url, timeout) -> (код ответа, тело)`, и ни одного сетевого байта не летит.
# Обрыв сети заглушка изображает исключением OSError. В бою здесь None и
# работает `_http_get`.
TRANSPORT = None


def set_transport(fn):
    """Подменить транспорт (для тестов). None = вернуть боевой urllib.
    Возвращает прежний — чтобы тест мог положить его обратно."""
    global TRANSPORT
    prev, TRANSPORT = TRANSPORT, fn
    return prev


def _sleep(seconds):
    """Отдельной функцией, чтобы тесты не ждали вежливую паузу по-настоящему."""
    time.sleep(seconds)


def oembed_url(url) -> str:
    """Адрес запроса карточки. Ссылку кодируем целиком: в наших ссылках есть
    и `@`, и хвосты вида `?is_from_webapp=1`, которые иначе уедут в параметры
    самого эмбеда."""
    return OEMBED + "?" + urllib.parse.urlencode({"url": str(url).strip()})


def _http_get(url, timeout=DEFAULT_TIMEOUT):
    """Один боевой запрос. Возвращает (код, тело). Отказ по коду (400 и прочие)
    ошибкой НЕ считается — это и есть ответ на наш вопрос, поэтому HTTPError
    разворачивается в пару (код, тело). Ошибка — только «не дошли»: сети нет,
    таймаут, DNS."""
    req = urllib.request.Request(oembed_url(url), method="GET")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status or 0), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        try:
            body = ex.read().decode("utf-8", "replace")
        except Exception:                                       # noqa: BLE001
            body = ""
        return int(ex.code), body


def _looks_like_card(body) -> bool:
    """Пришла ли настоящая карточка ролика.

    Мало кода 200: сервисы отдают двухсотку и на страницу-заглушку, и на пустой
    объект. Признаём карточкой только ответ, где есть чем показать ролик —
    название или имя автора (в замере 28.08.2026 у чужого публичного ролика
    пришли `title`, `author_name` и `thumbnail_url` разом)."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    return any(str(data.get(k) or "").strip()
               for k in ("title", "author_name", "thumbnail_url"))


def check(url, timeout=DEFAULT_TIMEOUT) -> dict:
    """Видит ли посторонний этот ролик. Один ролик, один запрос.

    Возвращает {"url", "result", "why"}, где `result` — один из VIDEN /
    NE_VIDEN / NE_PROVERENO, а `why` — короткая причина для человека. Исключений
    наружу не выпускает: «не смог проверить» — это тоже ответ, а не авария."""
    url = str(url or "").strip()
    out = {"url": url, "result": NE_PROVERENO, "why": ""}
    if not url:
        out["why"] = "пустая строка вместо ссылки"
        return out

    fetch = TRANSPORT if TRANSPORT is not None else _http_get
    try:
        code, body = fetch(url, timeout)
    except (urllib.error.URLError, OSError, TimeoutError) as ex:
        # Сеть/таймаут/DNS. НЕ «не виден»: мы просто не дошли до ответа, и
        # выдавать это за приговор ролику — та самая ложь, от которой лечимся.
        reason = getattr(ex, "reason", ex)
        out["why"] = "не дошли до TikTok (%s)" % reason
        return out
    except Exception as ex:                                     # noqa: BLE001
        out["why"] = "не смогли спросить TikTok (%s)" % ex
        return out

    code = int(code or 0)
    if code == 200:
        if _looks_like_card(body):
            out["result"] = VIDEN
            return out
        # Двухсотка без карточки — это не «не виден», а «мы не поняли ответ».
        out["why"] = "TikTok ответил, но карточки ролика в ответе нет"
        return out
    if code in (400, 403, 404):
        out["result"] = NE_VIDEN
        # Причину назвать НЕЛЬЗЯ: тот же отказ приходит и на снятый, и на
        # несуществующий ролик, и на опечатку в ссылке.
        out["why"] = "TikTok не отдаёт карточку ролика посторонним"
        return out
    # 429, 5xx и всё прочее — беда на той стороне, а не приговор ролику.
    out["why"] = "TikTok сейчас не отвечает по существу"
    return out


def check_many(urls, timeout=DEFAULT_TIMEOUT, pause=DEFAULT_PAUSE) -> list:
    """Пачка ссылок по очереди, с вежливой паузой между ними.

    По одной руками никто проверять не будет — перед выпуском нужен список.
    Пауза только МЕЖДУ запросами: перед первым ждать не за чем."""
    results = []
    for n, url in enumerate(urls):
        if n and pause:
            _sleep(pause)
        results.append(check(url, timeout=timeout))
    return results


def summary(results) -> dict:
    """Раскладка исходов: сколько видно, сколько нет, сколько не проверили."""
    out = {VIDEN: [], NE_VIDEN: [], NE_PROVERENO: []}
    for r in results:
        out.setdefault(r.get("result", NE_PROVERENO), []).append(r)
    return out


def exit_code(results) -> int:
    """0 — все видны; 1 — есть невидимые; 2 — что-то проверить не удалось.

    Непроверенное ВАЖНЕЕ невидимого: пока хоть одна ссылка осталась без ответа,
    список невидимых заведомо неполон, и говорить «вот и всё, что не вышло»
    нельзя. Пустой список — тоже не повод рапортовать «всё видно»."""
    if not results:
        return 1
    box = summary(results)
    if box[NE_PROVERENO]:
        return 2
    if box[NE_VIDEN]:
        return 1
    return 0


# --------------------------------------------------------------------------
# отчёт человеку
# --------------------------------------------------------------------------
def render(results) -> str:
    """Текст для человека. Без кодов, без английских слов, без диагнозов."""
    if not results:
        return "Проверять нечего: ни одной ссылки не передано."

    box = summary(results)
    lines = []
    n = len(results)
    lines.append("Проверено ссылок: %d." % n)

    if box[VIDEN]:
        lines.append("")
        lines.append("Эти ролики посторонние видят (%d):" % len(box[VIDEN]))
        for r in box[VIDEN]:
            lines.append("  · %s" % r["url"])

    if box[NE_VIDEN]:
        lines.append("")
        lines.append("Эти ролики посторонние НЕ видят (%d):"
                     % len(box[NE_VIDEN]))
        for r in box[NE_VIDEN]:
            lines.append("  · %s" % r["url"])
        lines.append("  Отчёт о публикации у таких роликов может быть успешным —"
                     " он ничего не доказывает.")
        lines.append("  Почему не видят, эта проверка не знает: так же выглядит"
                     " и снятый ролик, и ошибка в ссылке.")
        lines.append("  Что делать: откройте ссылку в браузере, где вы НЕ вошли"
                     " в TikTok (или попросите знакомого).")
        lines.append("  Увидите то же самое — значит для зрителей ролика нет, и"
                     " его надо выкладывать заново.")

    if box[NE_PROVERENO]:
        lines.append("")
        lines.append("Эти ролики проверить не удалось (%d):"
                     % len(box[NE_PROVERENO]))
        for r in box[NE_PROVERENO]:
            lines.append("  · %s" % r["url"])
            if r.get("why"):
                lines.append("      %s" % r["why"])
        lines.append("  Это НЕ значит, что их не видно. Значит, мы не получили"
                     " ответа — проверьте связь и повторите.")

    lines.append("")
    if not box[NE_VIDEN] and not box[NE_PROVERENO]:
        lines.append("Итог: все ролики открываются у посторонних.")
    elif box[NE_PROVERENO]:
        lines.append("Итог: неполный — часть ссылок осталась без ответа.")
    else:
        lines.append("Итог: часть роликов зрителям недоступна.")
    return "\n".join(lines)


def read_list(path) -> list:
    """Ссылки из файла: по одной в строке. Пустые строки и строки с решёткой
    в начале пропускаем — в такие списки люди пишут пометки."""
    out = []
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Проверить, видят ли посторонние наши ролики в TikTok")
    ap.add_argument("--url", action="append", default=None,
                    help="ссылка на ролик (можно повторить несколько раз)")
    ap.add_argument("--file", default=None,
                    help="файл со ссылками, по одной в строке")
    ap.add_argument("--pause", type=float, default=DEFAULT_PAUSE,
                    help="пауза между запросами, секунд")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help="сколько ждать ответа, секунд")
    args = ap.parse_args(argv)

    urls = list(args.url or [])
    if args.file:
        try:
            urls += read_list(args.file)
        except OSError as ex:
            print("Не с чего начать: не смог прочитать список ссылок (%s)." % ex)
            return 1
    if not urls:
        print("Не с чего начать: не сказано, что проверять.\n"
              "  Передайте ссылку через --url или файл со ссылками через --file.")
        return 1

    results = check_many(urls, timeout=args.timeout, pause=args.pause)
    print(render(results))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
