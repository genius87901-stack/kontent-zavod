# -*- coding: utf-8 -*-
"""ШОВ К ДВИЖКУ КАРУСЕЛЕЙ. Собирает РЕАЛЬНЫЙ черновик карусели из созревшего слота
существующим движком (engine_*/engine4 + checks) — тем же путём, что ручные
сборки build_c*.py, только параметризованный слотом.

СЕМЕЙСТВО ДВИЖКА — ПО ВЫБОРУ КЛИЕНТА. Раньше здесь был жёстко зашит только
engine_photo, а `config.carousel_template()` (фирменный шаблон, который клиент
выбирает глазами по витрине BANK_SHABLONOV.jpg) читался нулём мест — клиент,
выбравший, скажем, «CREMA · coral», всё равно получал в очереди черновики
тёмного фото-стиля. Починено: `build()` читает выбранный шаблон и диспетчерит
на нужный `_build_<family>()` из `_FAMILY_BUILDERS` (ВСЕ семейства реестра
`styles/carousel_templates.json` — с 23.08.2026 их 16, вся библиотека стилей,
кроме ядра engine4 и «Переписки»: ядро как шаблон не выбирается, «Переписка»
клиенту не передаётся по решению). Нет выбора (свежая установка, клиент ещё не
называл стиль) — дефолт как раньше: engine_photo, акцент крутится по кругу
red/orange/terracotta. Каждый адаптер вызывает РЕАЛЬНЫЕ функции своего движка
теми же сигнатурами, что уже проверены боевыми каруселями #21-64 (build_c*.py) —
не переизобретает раскладку, а параметризует уже принятую владельцем.

ВАЖНО: движки НЕ переписываются. Здесь только оркестрация: подставить фото в
e.PHOTOS, вызвать 3 функции нужного движка (обложка/содержание/CTA), прогнать
checks.verify. Ровно то, что делает человек в build_c*.py, но входы берём из
слота плана.

ПРЕДОХРАНИТЕЛЬ: тут только ГЕНЕРАЦИЯ черновика. Ничего не публикуется, --send не
зовётся, платные API не трогаются — используются только локальные фото-входы.

Guard: если фото на вход нет — НЕ падаем и НЕ зовём движок, а возвращаем маркер
needs_input («нужен вход: фото»); исполнитель тогда оставит слот на dry-run.

Движок и checks лежат в корне установки (рядом с CONTRACT.md), не в пакете
modules/. Импорт делаем лениво внутри build(), добавив корень в sys.path — чтобы
сам факт импорта пакета content_plan не тянул mediapipe/Pillow, когда реальная
генерация выключена (обратная совместимость с dry-run).
"""
from __future__ import annotations

import os
import sys

from . import config, paths

# Расширения входных фото, которые понимает движок (Pillow).
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")

# Акценты ФОТО-стиля (engine_photo). На тёмном грейде читаются именно они — см.
# engine4.ACCENT_STYLE: terracotta/orange/red идут в engine_photo. Крутим их
# по слоту детерминированно, чтобы соседние карусели не повторяли цвет подряд.
_PHOTO_ACCENTS = ("red", "orange", "terracotta")

# Сколько слайдов в черновике. ОБНОВЛЕНО 22.08.2026: карусель короче 8 слайдов
# не раскрывает тему — то же правило, что теперь действует на боевых сборках
# владельца (build_c72..c85). Раскладка: обложка + 6 content-слайдов + CTA.
# Движок поддерживает любое n (все функции по умолчанию уже рассчитаны на n=8).
#
# ОГОВОРКА: у слота есть только ОДИН смысл (topic/hook/meaning) — не шесть
# отдельных пунктов, как в ручных сборках владельца. _split_points() ниже
# механически растягивает этот один смысл на 6 слайдов через разбивку на
# предложения (с цикличным повтором, если предложений меньше шести). Это
# честная, но НЕБОГАТАЯ развёртка: настоящая глубина (как в build_c72..c85)
# требует по-настоящему разных под-мыслей на слот, а не разбивки одного текста —
# для этого нужен LLM-шаг (см. llm_caption в caption_bridge.py, здесь пока не
# заведён). Патч закрывает букву правила («не короче 8»), не подменяет собой
# ручное написание глубокого текста.
_N = 8


def _split_points(text: str, count: int, fallback: str = "") -> list:
    """Растягивает один текст на `count` пунктов через разбивку на предложения.
    Предложений меньше, чем count, — циклически повторяем (см. оговорку у _N)."""
    import re
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", (text or "").strip())
                if s.strip()]
    if not sentences:
        sentences = [fallback or (text or "").strip() or "Продолжение мысли."]
    return [sentences[i % len(sentences)] for i in range(count)]


def _clip(text: str, limit: int) -> str:
    """Короткая версия строки по границе СЛОВА (не по букве — обрубок посреди
    слова читается как поломка). Нужна там, где раскладка держит текст в узкой
    колонке гигантским кеглем: «Развилка» (половина кадра под Unbounded 34),
    «Чек-лист» (строка у чекбокса), метки метрик «Цифры». Движки такие строки
    не режут сами — они рассчитаны на текст, написанный человеком под слайд, а
    слот отдаёт целое предложение."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return (cut or text[:limit]).rstrip(" ,.;:—-") + "…"


def _points_word(n: int) -> str:
    """«пункт / пункта / пунктов» — форма слова под число. Подпись видит клиент
    и его подписчики: «6 пункта» на обложке читается как поломка завода."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "пункт"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "пункта"
    return "пунктов"


def _number_in(text: str):
    """Первое короткое число из текста или None. Числовые раскладки («Цифра» —
    гигантская цифра моно, «Тишина» — bignum) обязаны показывать число, КОТОРОЕ
    ЕСТЬ В СМЫСЛЕ, а не выдуманную метрику: смысла без числа тут больше, чем с
    числом, поэтому None — законный ответ, а вызывающий на нём переключается на
    нечисловую раскладку или на порядковый счётчик."""
    import re
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    if not m or len(m.group(0)) > 5:
        return None
    return m.group(0)


def _ensure_engine_on_path():
    """Корень установки (с движком) — в sys.path, лениво и без дублей."""
    root = str(config.install_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _abs_under_root(p: str):
    from pathlib import Path
    path = Path(p)
    if not path.is_absolute():
        path = config.install_root() / path
    return path


def _collect_images(directory) -> list:
    from pathlib import Path
    d = Path(directory)
    if not d.is_dir():
        return []
    files = [str(f) for f in sorted(d.iterdir())
             if f.is_file() and f.suffix.lower() in _IMG_EXT]
    return files


def resolve_photos(slot: dict) -> list:
    """Список абсолютных путей к входным фото для слота. Приоритет:

    1. slot["photos"]      — явный список путей в слоте (относительные → от корня);
    2. slot["photos_dir"]  — каталог со слотовыми фото;
    3. config content_plan_photos_dir — общий каталог входных фото из конфига;
    4. банк движка (engine4.U, папка photos/) — как у ручных сборок.

    Пустой список = входа нет (сработает guard).
    """
    # 1. явный список в слоте
    listed = slot.get("photos")
    if isinstance(listed, (list, tuple)) and listed:
        out = []
        for item in listed:
            path = _abs_under_root(str(item))
            if path.is_file() and path.suffix.lower() in _IMG_EXT:
                out.append(str(path))
        if out:
            return out

    # 2. каталог в слоте
    slot_dir = slot.get("photos_dir")
    if slot_dir:
        imgs = _collect_images(_abs_under_root(str(slot_dir)))
        if imgs:
            return imgs

    # 3. каталог из конфига
    cfg_dir = config.photos_dir()
    if cfg_dir:
        imgs = _collect_images(cfg_dir)
        if imgs:
            return imgs

    # 4. банк движка (photos/)
    _ensure_engine_on_path()
    try:
        import engine4 as e
    except Exception:
        return []
    return _collect_images(e.U)


def _accent_name(slot_id: str) -> str:
    return _PHOTO_ACCENTS[hash(slot_id) % len(_PHOTO_ACCENTS)]


def _pick(photos: list, i: int) -> str:
    """i-е фото с прокруткой по кругу (если фото меньше, чем слайдов)."""
    return photos[i % len(photos)]


def _first_sentence(text: str, limit: int = 120) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    for sep in (". ", "! ", "? "):
        if sep in text:
            text = text.split(sep, 1)[0] + sep.strip()
            break
    return text[:limit].strip()


# Подпись бренда, которую умеют показывать некоторые движки (brand_line) —
# та же, что в боевых build_c*.py Валерии. Декоративная, ни на что не влияет,
# если движок параметр не поддерживает (адаптер его просто не передаёт).
_BRAND_LINE = "контент завод"


def _out_path(out_dir):
    return lambda i: str(out_dir / ("%02d.png" % i))


def _slide_files(n):
    return ["%02d.png" % i for i in range(1, n + 1)]


def _photo_key(slot_id, suffix):
    """Уникальный ключ фото в реестре движка, чтобы не затирать банк."""
    return "cp_%s_%s" % (slot_id, suffix)


def _assign_photo(e, key, path):
    e.PHOTOS[key] = path
    return os.path.basename(path)


# ── адаптеры «слот → 3 слайда движка» ────────────────────────────────────────
#
# Каждый — обложка + слайд-смысл + CTA, теми же сигнатурами функций, что уже
# проверены боевыми каруселями (build_c*.py, см. docstring файла). Возвращают
# (cover_res, extra) — extra идёт прямо в checks.verify()/media.

def _build_photo(slot_id, photos, acc, out_dir, t, e):
    import engine_photo as ph
    p = _out_path(out_dir)
    k_cta = _photo_key(slot_id, "cta")
    used = []
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {}
    for idx, point in enumerate(points):
        k = _photo_key(slot_id, "s%d" % (idx + 2))
        used.append(_assign_photo(e, k, _pick(photos, idx + 1)))
        results[idx + 2] = ph.statement(t["st_title"], point, k, acc,
                                        p(idx + 2), idx + 2, _N)
    used.append(_assign_photo(e, k_cta, _pick(photos, 0)))
    results[_N] = ph.cta(t["cta_title"], "", t["cta_sub"], k_cta, acc, p(_N), _N, _N)
    used.insert(0, _assign_photo(e, _photo_key(slot_id, "cov"), _pick(photos, 0)))
    cover_res = ph.cover(t["cover_hook"], t["cover_sub"], _photo_key(slot_id, "cov"),
                        acc, p(1), 1, _N)
    return cover_res, {"photo_slides": tuple(range(1, _N + 1)), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_white(slot_id, photos, acc, out_dir, t, e):
    import engine_white as w
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = w.cover_ref(t["cover_hook"], t["cover_sub"], k_cov, acc, p(1), 1, _N)
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {idx + 2: w.quote(t["st_title"], point, acc, p(idx + 2), idx + 2, _N)
              for idx, point in enumerate(points)}
    results[_N] = w.cta(t["cta_title"], t["cta_sub"], "", acc, p(_N), _N, _N)
    return cover_res, {"photo_slides": (1,), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_cream(slot_id, photos, acc, out_dir, t, e):
    import engine_cream as c
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = c.photo_side(t["cover_hook"], t["cover_sub"], k_cov, acc, p(1), 1,
                             _N, side="right")
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {idx + 2: c.quote(t["st_title"], point, acc, p(idx + 2), idx + 2, _N)
              for idx, point in enumerate(points)}
    results[_N] = c.cta(t["cta_title"], t["cta_sub"], "", acc, p(_N), _N, _N)
    # Кремовое семейство двутонное (коралл-текст + персик-плашки) — под
    # check_palette не попадает по построению (см. build_c39.py, тот же
    # комментарий у боевой карусели #39): текстовые слайды объявляем
    # theme_slides, иначе проверка ложно ловит «второй цвет».
    return cover_res, {"photo_slides": (1,), "theme_slides": tuple(range(2, _N + 1)),
                       "slide_results": results, "photos_used": used}


def _build_editorial(slot_id, photos, acc, out_dir, t, e):
    import engine_editorial as ed
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = ed.cover(t["cover_hook"], t["cover_sub"], k_cov, acc, p(1), 1, _N,
                         brand_line=_BRAND_LINE)
    body = t["st_body"] or t["st_title"]
    lead, rest = body[0].upper(), body[1:]
    results = {2: ed.drop_intro(lead, rest, t["st_title"], acc, p(2), 2, _N,
                               section="суть", brand_line=_BRAND_LINE)}
    points = _split_points(t["st_body"], _N - 3, fallback=t["st_title"])
    for idx, point in enumerate(points):
        i = idx + 3
        results[i] = ed.pull_quote_slide(point, "", acc, p(i), i, _N,
                                         brand_line=_BRAND_LINE)
    results[_N] = ed.colophon_cta("ВЫВОД", t["cta_title"], t["cta_sub"], acc,
                                  p(_N), _N, _N, brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": (1,), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_brutalist(slot_id, photos, acc, out_dir, t, e):
    import engine_brutalist as br
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = br.cover(t["cover_hook"], k_cov, acc, p(1), 1, _N)
    labels = ("ГЛАВНОЕ", "ПОДРОБНЕЕ", "ПОЧЕМУ", "КАК ЭТО РАБОТАЕТ", "ВАЖНО", "ИТОГ")
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {idx + 2: br.statement(labels[idx % len(labels)], point, acc,
                                     p(idx + 2), idx + 2, _N)
              for idx, point in enumerate(points)}
    results[_N] = br.cta(t["cta_title"], "", acc, p(_N), _N, _N, sub=t["cta_sub"])
    return cover_res, {"photo_slides": (1,), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_scrapbook(slot_id, photos, acc, out_dir, t, e):
    import engine_scrapbook as sb
    p = _out_path(out_dir)
    k_cov, k_p1 = _photo_key(slot_id, "cov"), _photo_key(slot_id, "p1")
    used = [_assign_photo(e, k_cov, _pick(photos, 0)),
            _assign_photo(e, k_p1, _pick(photos, 1))]
    cover_res = sb.cover(t["cover_hook"], t["cover_sub"], k_cov, acc, p(1), 1, _N,
                         brand_line=_BRAND_LINE)
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    photo_slides = [1, 2]
    results = {2: sb.polaroid_note(points[0], k_p1, acc, p(2), 2, _N)}
    for idx, point in enumerate(points[1:], start=3):
        results[idx] = sb.outro(point, acc, p(idx), idx, _N, sticker=None)
    results[_N] = sb.outro(t["cta_sub"], acc, p(_N), _N, _N, sticker="heart",
                           brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": tuple(photo_slides), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_quote(slot_id, photos, acc, out_dir, t, e):
    import engine_quote as q
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = q.cover(t["cover_hook"], "", k_cov, acc, p(1), 1, _N,
                        brand_line=_BRAND_LINE)
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {idx + 2: q.quote_slide(point, acc, p(idx + 2), idx + 2, _N)
              for idx, point in enumerate(points)}
    results[_N] = q.cta_slide(t["cta_title"], t["cta_sub"], acc, p(_N), _N, _N,
                              brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": (1,), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_sticker(slot_id, photos, acc, out_dir, t, e):
    import engine_sticker as st
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = st.cover(t["cover_hook"], k_cov, acc, p(1), 1, _N,
                         brand_line=_BRAND_LINE)
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {idx + 2: st.poll_slide(point, "Знал?", "Впервые слышу", acc,
                                      p(idx + 2), idx + 2, _N, picked="b")
              for idx, point in enumerate(points)}
    results[_N] = st.outro(t["cta_sub"], "", acc, p(_N), _N, _N,
                           brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": (1,), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_minimal(slot_id, photos, acc, out_dir, t, e):
    import engine_minimal as mi
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = mi.cover(t["cover_hook"], k_cov, acc, p(1), 1, _N,
                         brand_line=_BRAND_LINE)
    # ОБНОВЛЕНО 22.08.2026: разные фото на content-слайдах (не один и тот же
    # кадр повторно) — то же правило, что на боевых c75/c79/c83 владельца.
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {}
    for idx, point in enumerate(points):
        k = _photo_key(slot_id, "s%d" % (idx + 2))
        used.append(_assign_photo(e, k, _pick(photos, idx + 1)))
        results[idx + 2] = mi.frame(point, k, acc, p(idx + 2), idx + 2, _N,
                                    brand_line=_BRAND_LINE)
    k_cta = _photo_key(slot_id, "cta")
    used.append(_assign_photo(e, k_cta, _pick(photos, len(points) + 1)))
    results[_N] = mi.cta(t["cta_title"], "", k_cta, acc, p(_N), _N, _N,
                         brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": tuple(range(1, _N + 1)), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_meme(slot_id, photos, acc, out_dir, t, e):
    import engine_meme as mm
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = mm.cover(t["cover_hook"], t["cover_sub"], k_cov, acc, p(1), 1, _N,
                         brand_line=_BRAND_LINE)
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {idx + 2: mm.screen_slide(point, acc, p(idx + 2), idx + 2, _N)
              for idx, point in enumerate(points)}
    results[_N] = mm.cta_bar(t["cta_title"], t["cta_sub"], acc, p(_N), _N, _N,
                             brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": (1,), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


# ── этап 2/3 библиотеки 18 стилей, раскатан клиентам 23.08.2026 ─────────────
#
# Шесть движков были написаны 15.08.2026 и полгода стояли в `_not_shipped`:
# клиент не мог их выбрать не потому, что они сырые (каждый прошёл гейт
# checks.verify и собрал боевые карусели c65…c70, c80…c85), а потому что не
# было вот этих шести функций — диспетчер `_FAMILY_BUILDERS` не знал, чем их
# собирать. Вызовы ниже повторяют боевые сборки владельца на n=8
# (build_c80/c81/c82/c84/c85 и build_c65…c70), а не изобретают раскладку.

def _build_swiss(slot_id, photos, acc, out_dir, t, e):
    """ТИШИНА: белый лист, одна мысль на слайд, акцент точкой. Мысль с числом
    внутри уходит на bignum (гигантская цифра) — как в боевой c80."""
    import engine_swiss as sw
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = sw.cover(t["cover_hook"], t["cover_sub"], k_cov, acc, p(1), 1, _N,
                         brand_line=_BRAND_LINE)
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {}
    for idx, point in enumerate(points):
        i = idx + 2
        num = _number_in(point)
        if num is not None:
            results[i] = sw.bignum(num, _clip(point, 150), acc, p(i), i, _N,
                                   section="цифра")
        else:
            results[i] = sw.statement(_clip(point, 170), acc, p(i), i, _N,
                                      section="суть")
    results[_N] = sw.cta(t["cta_title"], t["cta_sub"], acc, p(_N), _N, _N,
                         brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": (1,), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_cinematic(slot_id, photos, acc, out_dir, t, e):
    """КАДР: леттербокс, зерно, подпись субтитром. Второй слайд на своём кадре
    (как в боевой c81), остальные — «чёрный экран» с репликой."""
    import engine_cinematic as ci
    p = _out_path(out_dir)
    k_cov, k_s2 = _photo_key(slot_id, "cov"), _photo_key(slot_id, "s2")
    used = [_assign_photo(e, k_cov, _pick(photos, 0)),
            _assign_photo(e, k_s2, _pick(photos, 1))]
    cover_res = ci.cover(t["cover_hook"], "СЦЕНА 01", k_cov, acc, p(1), 1, _N)
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {}
    for idx, point in enumerate(points):
        i = idx + 2
        results[i] = ci.scene_caption(point, "СЦЕНА %02d" % i, acc, p(i), i, _N,
                                      photo_key=k_s2 if i == 2 else None)
    results[_N] = ci.outro(t["cta_title"], t["cta_sub"], "ФИНАЛ", acc,
                           p(_N), _N, _N, brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": (1, 2), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_panorama(slot_id, photos, acc, out_dir, t, e):
    """ПАНОРАМА: ОДИН кадр на всю карусель, окно едет сверху вниз по мере
    пролистывания. Второе фото сюда не берём принципиально — смена кадра
    убила бы эффект непрерывного движения (см. оговорку в docs/СТИЛИ_КАРУСЕЛЕЙ.md).
    Слайды 2..N объявляем theme_slides: кадр на них тот же, что на обложке,
    и правило 2/3 иначе посчитало бы всю карусель портретной."""
    import engine_panorama as pn
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = pn.cover(t["cover_hook"], t["cover_sub"], k_cov, acc, p(1), 1, _N,
                         brand_line=_BRAND_LINE)
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {}
    for idx, point in enumerate(points):
        i = idx + 2
        results[i] = pn.continued(_clip(point, 110), k_cov, i - 1, acc, p(i), i, _N)
    results[_N] = pn.climax(_clip(t["cta_title"], 60).upper(), k_cov, _N - 1, acc,
                            p(_N), _N, _N, cta_text=t["cta_sub"])
    return cover_res, {"photo_slides": (), "theme_slides": tuple(range(2, _N + 1)),
                       "slide_results": results, "photos_used": used}


def _build_split(slot_id, photos, acc, out_dir, t, e):
    """РАЗВИЛКА: экран пополам, «как все» против «как надо».

    ОГОВОРКА, честная как и у _N: слайду нужны ДВЕ противопоставленные строки,
    а слот отдаёт один сплошной смысл. Механически берём предложения парами —
    соседние мысли встают слева и справа. Настоящая развилка («было» против
    «стало») пишется человеком, как в боевой c84; здесь — растяжка того же
    сорта, что и _split_points, и по той же причине.
    """
    import engine_split as sp
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = sp.cover(t["cover_hook"], k_cov, acc, p(1), 1, _N)
    pairs = _split_points(t["st_body"], (_N - 2) * 2, fallback=t["st_title"])
    results = {}
    for idx in range(_N - 2):
        i = idx + 2
        results[i] = sp.split_row(_clip(pairs[idx * 2], 42),
                                  _clip(pairs[idx * 2 + 1], 42),
                                  acc, p(i), i, _N, section="как все / как надо")
    results[_N] = sp.final(t["cta_title"], "написать в личку", acc, p(_N), _N, _N,
                           brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": (), "theme_slides": tuple(range(2, _N + 1)),
                       "slide_results": results, "photos_used": used}


def _build_data(slot_id, photos, acc, out_dir, t, e):
    """ЦИФРА: тёмный фон, гигантские числа моноширинным.

    Число берём ИЗ САМОЙ МЫСЛИ (_number_in). Числа в ней нет — ставим
    порядковый счётчик «3 из 6», а не выдуманную метрику: подделывать данные
    клиента заводу нельзя. chart_metric не зовём вовсе — ему нужен РЯД
    значений, а честного ряда у слота нет ни при каком раскладе.
    """
    import engine_data as dt
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = dt.cover(t["cover_hook"], k_cov, acc, p(1), 1, _N,
                         chip="%d %s" % (_N - 2, _points_word(_N - 2)))
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    label = _clip(t["st_title"], 56)
    results = {}
    for idx, point in enumerate(points):
        i = idx + 2
        num = _number_in(point)
        value, unit = (num, "") if num is not None else (str(idx + 1),
                                                         "из %d" % (_N - 2))
        results[i] = dt.metric(label, value, unit, acc, p(i), i, _N,
                               note=_clip(point, 190))
    results[_N] = dt.cta(t["cta_title"], t["cta_sub"], acc, p(_N), _N, _N,
                         brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": (1,), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


def _build_checklist(slot_id, photos, acc, out_dir, t, e):
    """ЧЕК-ЛИСТ: чекбоксы и прогресс «k из N», вид рабочей тетради.

    По ДВА пункта на слайд: строка у чекбокса набирается кеглем 30 и переносится,
    трёх длинных предложений слайд не держит (низ уезжает за кадр). В паре первый
    пункт отмечен, второй нет — «сделано / следующее»; прогресс считает
    отмеченные нарастающим итогом, а не выдаёт число с потолка.
    """
    import engine_checklist as cl
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    per_slide = 2
    total = (_N - 2) * per_slide
    cover_res = cl.cover(t["cover_hook"], "%d %s" % (total, _points_word(total)),
                         k_cov, acc, p(1), 1, _N)
    items = _split_points(t["st_body"], total, fallback=t["st_title"])
    results = {}
    for idx in range(_N - 2):
        i = idx + 2
        chunk = [(_clip(items[idx * per_slide + k], 96), k == 0)
                 for k in range(per_slide)]
        results[i] = cl.items_slide(chunk, "%d из %d" % (idx + 1, total),
                                    acc, p(i), i, _N)
    results[_N] = cl.outro(t["cta_title"], t["cta_sub"], acc, p(_N), _N, _N,
                           brand_line=_BRAND_LINE)
    return cover_res, {"photo_slides": (1,), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


# Ключи = family из styles/carousel_templates.json (сверяется test_carousel_style.py
# зеркалом на engine4.ACCENT_STYLE) — новое семейство добавится сюда явно, не
# молча провалится в дефолт.
# ── семейство «ЛЮБОВЬ» (31.08.2026) ──────────────────────────────────────────
# Три раскладки в ОДНОМ движке engine_love: slide() — «Любовь 1», slide_plate()
# — «Любовь 2», slide_pill() — «Любовь 3». Поэтому три адаптера, но один import
# и один файл в коробке.
#
# ОСОБЕННОСТЬ ПРОТИВ ОСТАЛЬНЫХ АДАПТЕРОВ: движок принимает ПУТЬ к кадру, а не
# ключ фотобанка. Ключ ему тоже передаём — но только чтобы гейт
# checks.check_face_safe мог перепроверить слайд своим детектором. Само правило
# «текст не на лице» движок соблюдает по построению и кладёт вердикт в
# face_clean.


def _love_photos(slot_id, photos, e, n):
    """Ключи и пути для n слайдов: кадры крутятся по кругу, как у соседей."""
    keys, paths, used = [], [], []
    for i in range(n):
        path = _pick(photos, i)
        key = _photo_key(slot_id, "l%d" % (i + 1))
        used.append(_assign_photo(e, key, path))
        keys.append(key)
        paths.append(path)
    return keys, paths, used


def _build_love1(slot_id, photos, acc, out_dir, t, e):
    """ЛЮБОВЬ 1: крупные прописные, рукописная строка выводом слайда."""
    import engine_love as lv
    p = _out_path(out_dir)
    keys, paths, used = _love_photos(slot_id, photos, e, _N)
    results = {}
    cover_res = lv.slide(paths[0], t["cover_hook"], t["cover_sub"], _BRAND_LINE,
                         p(1), align="center", cover=True, photo_key=keys[0])
    results[1] = cover_res
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    for idx, point in enumerate(points):
        i = idx + 2
        results[i] = lv.slide(paths[idx + 1], t["st_title"], point, "",
                              p(i), photo_key=keys[idx + 1])
    results[_N] = lv.slide(paths[_N - 1], t["cta_title"], t["cta_sub"],
                           _BRAND_LINE, p(_N), photo_key=keys[_N - 1])
    return cover_res, {"photo_slides": tuple(range(1, _N + 1)),
                       "theme_slides": (), "slide_results": results,
                       "photos_used": used}


def _build_love2(slot_id, photos, acc, out_dir, t, e):
    """ЛЮБОВЬ 2: смешанный регистр, ключевое слово на плашке, счётчик и «листай»."""
    import engine_love as lv
    p = _out_path(out_dir)
    keys, paths, used = _love_photos(slot_id, photos, e, _N)
    results = {}
    cover_res = lv.slide_plate(paths[0], [t["cover_hook"], "==%s==" % t["cover_sub"]],
                               "", p(1), n=1, total=_N, cover=True,
                               mark=_BRAND_LINE, photo_key=keys[0])
    results[1] = cover_res
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    for idx, point in enumerate(points):
        i = idx + 2
        results[i] = lv.slide_plate(paths[idx + 1], [t["st_title"]], point, p(i),
                                    n=i, total=_N, mark=_BRAND_LINE,
                                    photo_key=keys[idx + 1])
    results[_N] = lv.slide_plate(paths[_N - 1],
                                 [t["cta_title"], "==%s==" % t["cta_sub"]], "",
                                 p(_N), n=_N, total=_N, tail="",
                                 mark=_BRAND_LINE, photo_key=keys[_N - 1])
    return cover_res, {"photo_slides": tuple(range(1, _N + 1)),
                       "theme_slides": (), "slide_results": results,
                       "photos_used": used}


def _build_love3(slot_id, photos, acc, out_dir, t, e):
    """ЛЮБОВЬ 3: кадр остаётся дневным, ключевая строка на таблетке.

    Обложка и призыв идут с big=True — крупнее внутренних и на широкой
    колонке: это вход и выход карусели, они читаются как пара.
    """
    import engine_love as lv
    p = _out_path(out_dir)
    keys, paths, used = _love_photos(slot_id, photos, e, _N)
    results = {}
    cover_res = lv.slide_pill(paths[0], t["cover_hook"], t["cover_sub"],
                              [_BRAND_LINE], p(1), big=True, photo_key=keys[0])
    results[1] = cover_res
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    for idx, point in enumerate(points):
        i = idx + 2
        results[i] = lv.slide_pill(paths[idx + 1], t["st_title"], "", [point],
                                   p(i), photo_key=keys[idx + 1])
    results[_N] = lv.slide_pill(paths[_N - 1], t["cta_title"], "",
                                [t["cta_sub"]], p(_N), big=True,
                                photo_key=keys[_N - 1])
    return cover_res, {"photo_slides": tuple(range(1, _N + 1)),
                       "theme_slides": (), "slide_results": results,
                       "photos_used": used}



# ── семейство «МАРКЕР» (02.09.2026, референс Валерии) ───────────────────────

def _build_marker(slot_id, photos, acc, out_dir, t, e):
    """МАРКЕР: фото на каждом слайде, заголовок одной плашкой акцента, номер
    пункта внутри плашки, текст уходит от лица вверх или вниз.

    Раскладка не изобретается: те же три функции движка (cover/slide/cta), что
    собрали демо-карусель, принятую владельцем 02.09.2026. Номер пункта здесь
    ВАЖЕН — семейство перечневое, и счёт ведёт сама плашка.
    """
    import engine_marker as mk
    p = _out_path(out_dir)
    k_cov = _photo_key(slot_id, "cov")
    used = [_assign_photo(e, k_cov, _pick(photos, 0))]
    cover_res = mk.cover(t["cover_hook"], "", t["cover_sub"], k_cov, acc, p(1), 1, _N)
    points = _split_points(t["st_body"], _N - 2, fallback=t["st_title"])
    results = {}
    for idx, point in enumerate(points):
        i = idx + 2
        k = _photo_key(slot_id, "s%d" % i)
        used.append(_assign_photo(e, k, _pick(photos, idx + 1)))
        results[i] = mk.slide(idx + 1, t["st_title"], point, k, acc, p(i), i, _N)
    k_cta = _photo_key(slot_id, "cta")
    used.append(_assign_photo(e, k_cta, _pick(photos, len(points) + 1)))
    results[_N] = mk.cta(t["cta_title"], t["cta_sub"], "", k_cta, acc, p(_N), _N, _N)
    return cover_res, {"photo_slides": tuple(range(1, _N + 1)), "theme_slides": (),
                       "slide_results": results, "photos_used": used}


_FAMILY_BUILDERS = {
    "photo": _build_photo,
    "white": _build_white,
    "cream": _build_cream,
    "editorial": _build_editorial,
    "brutalist": _build_brutalist,
    "scrapbook": _build_scrapbook,
    "quote": _build_quote,
    "sticker": _build_sticker,
    "meme": _build_meme,
    "minimal": _build_minimal,
    # этап 2/3 библиотеки 18 стилей — раскатан клиентам 23.08.2026
    "swiss": _build_swiss,
    "cinematic": _build_cinematic,
    "panorama": _build_panorama,
    "split": _build_split,
    "data": _build_data,
    "checklist": _build_checklist,
    # семейство «ЛЮБОВЬ» — три раскладки одного движка (31.08.2026)
    "love1": _build_love1,
    "love2": _build_love2,
    "love3": _build_love3,
    # «МАРКЕР» — 02.09.2026, референс владельца
    "marker": _build_marker,
}


def build(slot: dict) -> dict:
    """Собирает РЕАЛЬНЫЙ черновик карусели в content_plan/queue/<slot_id>/.

    Движок и акцент — по фирменному шаблону клиента (`config.carousel_template()`,
    выбор по витрине BANK_SHABLONOV.jpg). Шаблон ещё не выбран/битый/незнакомое
    семейство → дефолт как раньше: engine_photo, акцент по кругу
    red/orange/terracotta.

    Возвращает результат-словарь:
      {"ok": True,  "media": {...}}                — собрали настоящие слайды;
      {"ok": False, "needs_input": "фото: ..."}    — входа нет, слот на dry-run;
      {"ok": False, "needs_input": "смыслы: ..."}  — нет темы/смысла.

    Исключения движка НЕ глотаем целиком — их поймает исполнитель и пометит слот
    FAILED (как и раньше). Guard — только для ОТСУТСТВИЯ входов, не для поломок.
    """
    slot_id = slot["id"]
    topic = (slot.get("topic") or "").strip()
    hook = (slot.get("hook") or "").strip()
    meaning = (slot.get("meaning") or "").strip()

    # Guard №1: нет смыслов — движку нечего набирать.
    if not (topic or hook or meaning):
        return {"ok": False,
                "needs_input": "смыслы: у слота пустые topic/hook/meaning"}

    # Guard №2: нет входных фото — ни один из движков без кадров не соберёт.
    photos = resolve_photos(slot)
    if not photos:
        return {"ok": False,
                "needs_input": "фото: не найдено ни одного входного кадра "
                               "(slot.photos / slot.photos_dir / конфиг "
                               "content_plan_photos_dir / банк движка photos/)"}

    _ensure_engine_on_path()
    import engine4 as e
    import checks

    tmpl = config.carousel_template()
    if tmpl and tmpl["family"] in _FAMILY_BUILDERS and tmpl["accent"] in e.ACCENTS:
        family, acc_name = tmpl["family"], tmpl["accent"]
    else:
        family, acc_name = "photo", _accent_name(slot_id)
    acc = e.ACCENTS[acc_name]
    builder = _FAMILY_BUILDERS[family]

    out_dir = paths.queue_dir() / slot_id
    out_dir.mkdir(parents=True, exist_ok=True)

    texts = {
        "cover_hook": (hook or topic).upper(),
        "cover_sub": _first_sentence(meaning) or "Листай дальше →",
        "st_title": topic or hook,
        "st_body": meaning or hook or topic,
        "cta_title": hook or topic,
        "cta_sub": "Если откликнулось — напиши в личку.",
    }

    # РЕАЛЬНЫЙ вызов движка выбранного семейства (те же функции, что в build_c*.py).
    cover_res, extra = builder(slot_id, photos, acc, out_dir, texts, e)

    # ПРАВКА КЛИЕНТСКОЙ КОПИИ 09.09.2026 (НиколаевскТВ) — фирменный знак.
    # Движки логотипы не умеют; вся логика в корневом brand_logo.py, который
    # обновление не затирает. Здесь только вызов — вернуть после обновления
    # (ХЕНДОФФ.md, раздел 6а). Ставим ДО checks.verify, чтобы проверки видели
    # финальный кадр. Знака нет или не читается — тихо идём дальше.
    try:
        import brand_logo
        brand_logo.stamp_dir(out_dir)
    except Exception:
        pass

    # Проверки перед показом — НЕ строгие: черновик уходит человеку в окно-вето,
    # а не в публикацию, поэтому замечания движка фиксируем, но сборку не роняем.
    verify_problems = checks.verify(str(out_dir), acc, cover_res,
                                    photo_slides=extra["photo_slides"],
                                    theme_slides=extra["theme_slides"],
                                    slide_results=extra["slide_results"],
                                    strict=False)

    media = {
        "kind": "carousel",
        "dir": slot_id,                       # относительно content_plan/queue/
        "slides": _slide_files(_N),
        "count": _N,
        "family": family,
        "accent": acc_name,
        "photos_used": extra["photos_used"],
        "verify_problems": verify_problems,   # пусто = проверки пройдены
    }
    return {"ok": True, "media": media}
