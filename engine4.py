"""
ДВИЖОК v4 — по референсам.
Обложка: фото + белый текст + главная мысль цветом/плашкой.
Внутри: фото сверху / тёмная панель снизу, акцентные слова, карточки, списки.
Акцентный цвет — параметр (меняется от карусели к карусели).
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import io
import os

W, H = 1080, 1350
M = 72
F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inter", "extras", "ttf") + os.sep
_ONEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Onest.ttf")

# ── ШРИФТ ──
# Inter — статические файлы на каждое начертание. Onest — вариативный: один файл,
# начертание задаётся осью Weight (100–900), поэтому имя веса маппим в число.
# Лицензия Onest — OFL, лежит рядом: fonts/OFL-Onest.txt.
FAMILY = "Onest"          # семья по умолчанию; "Inter" — прежний вид
_WEIGHT = {"Regular": 400, "SemiBold": 600, "Bold": 700, "Black": 900}


def font(w, s, family=None):
    fam = family or FAMILY
    if fam == "Inter":
        return ImageFont.truetype(F + f"Inter-{w}.ttf", s)
    ft = ImageFont.truetype(_ONEST, s)
    ft.set_variation_by_axes([_WEIGHT[w]])
    return ft

# ── ПАЛИТРА: у каждого акцента РОВНО ОДНА координата ──
# Значение ниже — единственное, в котором цвет имеет право появиться на слайде.
# Производных яркостей нет: acc_ink() удалён (см. ПРОЕКТ.md, «ОДИН ЦВЕТ»).
# Акцент — только заливка (плашка, штрих, буллет, карточка, кнопка, обводка),
# текст поверх заливки — тёмный INK. Акцентным текстом на белом цвет не бывает:
# контраст к белому у всей палитры, кроме red, ниже 4.5 — он там не читается.
# checks.check_palette сверяет точное совпадение с этим значением.
ACCENTS = {
    "terracotta": (201, 113, 78),
    "lime":       (204, 255, 0),
    "mint":       (94, 214, 184),
    "orange":     (240, 138, 74),
    "red":        (228, 16, 43),
    "sky":        (108, 168, 255),
    "coral":      (223, 118, 86),   # кремовое семейство (engine_cream)
    "sand":       (214, 196, 170),  # редакционное семейство (engine_editorial)
    # ── Библиотека 18 стилей, этап 1/3 (2026-08-14) — _not_shipped, см.
    # styles/carousel_templates.json. Каждый новый акцент — под свой стиль,
    # как sand под editorial: цвет — часть замысла конкретной раскладки, не
    # взаимозаменяемая деталь.
    "signal":     (255, 59, 18),    # «Бетон» (engine_brutalist)
    "clay":       (217, 106, 84),   # «Дневник» (engine_scrapbook)
    "gold":       (201, 162, 75),   # «Манифест» (engine_quote)
    "bubblegum":  (255, 92, 138),   # «Стикер» (engine_sticker)
    "flash":      (255, 226, 61),   # «Реплика» (engine_meme)
    # ── Библиотека 18 стилей, этап 2/3 (2026-08-15) — те же правила: свой
    # акцент под свой стиль, значения — из engine-спеки karusel-styles-library.html.
    "azure":      (44, 107, 237),   # «Тишина» (engine_swiss)
    "amber":      (201, 162, 75),   # «Кадр» (engine_cinematic) — тот же тон, что gold
    "dune":       (217, 140, 74),   # «Панорама» (engine_panorama)
    "meadow":     (47, 181, 116),   # «Развилка» (engine_split)
    "cobalt":     (76, 141, 255),   # «Цифра» (engine_data)
    "sprout":     (47, 181, 116),   # «Чек-лист» (engine_checklist) — тот же тон, что meadow
    # ── Семейство «ЛЮБОВЬ», 31.08.2026 (референсы клиентки) ──
    # Сливочный работает ПЛАШКОЙ под ключевым словом: буква на нём тёмная, как
    # и велит правило «акцент — только заливка». На белом фоне он бы утонул
    # (контраст к белому 1.1), но семейство фотографическое: плашка всегда
    # лежит на затемнённом кадре, а не на листе.
    "vanilla":    (250, 238, 190),  # «Любовь 2» (engine_love, раскладка с плашкой)
}

# ── РОТАЦИЯ АКЦЕНТОВ (утверждено, карусель #3) ──
# Основной цикл для новых каруселей, по порядку, без повтора подряд:
# coral добавлен 02.08.2026 — кремовое семейство (engine_cream), стоит после red,
# на стыке с mint стиль не повторяется (photo→cream→white).
ACCENT_ROTATION = ["mint", "orange", "lime", "red", "coral"]

# ── СТИЛЬ ПРИВЯЗАН К ЦВЕТУ (решение Валерии) ──
# Причина — контраст, он у палитры переворачивается между фонами. На белом
# работает только red (4.61), lime тонет (1.14). На тёплом тёмном грейде ровно
# наоборот: lime 14.6, mint 9.6, а red слабее всех (3.60) и годится там только
# под КРУПНЫЙ текст (порог 3.0), мелким акцентным текстом в нём не пользуемся.
# Ротация при этом сама чередует стили: mint(белый) → orange(фото) →
# lime(белый) → red(фото).
ACCENT_STYLE = {
    "lime": "white", "sky": "white", "mint": "white",         # engine_white
    "terracotta": "photo", "orange": "photo", "red": "photo",  # engine_photo
    "coral": "cream",                                          # engine_cream
    "sand": "editorial",                                       # engine_editorial
    "signal": "brutalist",                                     # engine_brutalist
    "clay": "scrapbook",                                       # engine_scrapbook
    "gold": "quote",                                           # engine_quote
    "bubblegum": "sticker",                                    # engine_sticker
    "flash": "meme",                                           # engine_meme
    "azure": "swiss",                                          # engine_swiss
    "amber": "cinematic",                                      # engine_cinematic
    "dune": "panorama",                                        # engine_panorama
    "meadow": "split",                                         # engine_split
    "cobalt": "data",                                          # engine_data
    "sprout": "checklist",                                     # engine_checklist
    "vanilla": "love2",                                        # engine_love
}
# terracotta и sky — резерв (спецтемы), в основной ротации не участвуют.
# sand — редакционное семейство (11.08.2026): фото на весь кадр, заголовок
# антиквой, текст прибит к низу. Стиль монохромный, песочный тон работает
# надстрочкой и метками; в основную ротацию не введён — берётся по запросу,
# как терракота, пока по нему не наберётся вторая-третья карусель.
# Журнал использования: см. ПРОЕКТ.md
# ── ПОЛЕ ПЛАШКИ: ОДНА КОНСТАНТА НА ВЕСЬ ЗАВОД ──
# Плашка (==фраза==) шире буквы: подложка вылезает влево и вправо на CHIP_PAD.
# Правило полиграфии: оптический край строки держит ПОДЛОЖКА, а не буква —
# иначе слайд, где акцентное слово первое в строке, стоит левее всех остальных
# (штрих, буллеты, тело, счётчик выровнены по M = 72).
# Поэтому: первый чип строки рисуется от x строки, а его БУКВА — от x+CHIP_PAD;
# wrap_rich вычитает CHIP_PAD из доступной ширины, чтобы сдвиг не выехал вправо.
# До правки отступ был разным в семи местах (-8, -10, -12, -14) — замер W2-chip.
CHIP_PAD = 12

# ── КОНТРАСТ ЗАЛИВКИ К ФОНУ (WCAG 1.4.11) ──
# Формула та же, что в engine_photo.contrast (там она про ТЕКСТ). Здесь она про
# ЗАЛИВКУ: на белом фоне (252,251,249) акцент даёт lime 1.14, mint 1.72,
# sky 2.34, orange 2.41, coral 2.96 при норме 3.0 — плашка и акцентная карточка
# физически неотличимы от фона. Дублируем 5 строк, а не импортируем engine_photo:
# engine_photo сам импортирует engine4, встречный импорт замкнул бы круг.
CHIP_EDGE_MIN = 3.0


def _lum(c):
    r, g, b = [x/255 for x in c[:3]]
    f = lambda v: v/12.92 if v <= 0.03928 else ((v+0.055)/1.055) ** 2.4
    return 0.2126*f(r) + 0.7152*f(g) + 0.0722*f(b)


def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def chip_edge(fill, bg, ink=None):
    """Аргументы обводки для rounded_rectangle/ellipse: волосяная линия INK 2 px,
    если заливка неразличима на фоне. Пустой словарь — если контраст в норме
    (тогда форму держит сама заливка, обводка была бы грязью)."""
    if bg is None or contrast(fill, bg) >= CHIP_EDGE_MIN:
        return {}
    return {"outline": ink if ink is not None else INK, "width": 2}


PANEL = (24, 20, 18)      # тёмная панель
PANEL_D = (18, 15, 14)
INK   = (26, 22, 20)      # единственный «чёрный»: текст на белом и на заливке акцента
WHITE = (255, 255, 255)
GREY  = (168, 163, 158)
CARD  = (44, 40, 37)

U = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos") + os.sep


# ══════════ ПРОГРЕВ ОБЛАЧНОГО БАНКА ══════════
#
# ЗАЧЕМ. Банк фото и видео у всех — папка Google Диска (у Валерии junction на
# G:, у клиентов — своя синхронизируемая папка). Первое касание файла стоит
# секунды, повторное — 0.04 с: дальше Диск отдаёт из локального кэша. Карусель
# читает 7–8 фото ПОДРЯД: замерено 48 секунд сборки, из них 41 — ожидание Диска
# при 6.5 секунды собственно отрисовки.
#
# ЧЕСТНЫЙ ЗАМЕР 07.08.2026 — A/B на ХОЛОДНОМ кэше, 32 кадра, которых код не
# касался ни разу, два раунда со сменой порядка (иначе дрейф скорости Диска
# выдал бы себя за выигрыш):
#     8 файлов подряд       — 56.6 и 42.1 сек
#     те же 8 параллельно   — 43.3 и 44.0 сек   → x1.3 и x1.0
#
# ⚠️ ВЫИГРЫШ СКРОМНЫЙ, И ВОТ ПОЧЕМУ. Упирается не в задержку, а в КАНАЛ: прямая
# закачка из интернета в тот же час дала 0.49 МБ/с — ровно столько же выдаёт
# Диск восемью потоками. 19 МБ фото на карусель не приедут быстрее ~40 секунд,
# сколько потоков ни дай. Прежняя запись «восемь файлов приходят за время
# одного» замером НЕ подтвердилась.
#
# ЧТО ПРОГРЕВ ВСЁ-ТАКИ ДАЁТ. Он убирает ПАУЗЫ МЕЖДУ файлами (~1.5 сек на кадр:
# соединение, запрос, ожидание первого байта). На узком канале это 10–30%, на
# быстром — почти всё время ожидания, поэтому клиентам он полезен и оставлен.
# Но чтобы сборка перестала ждать Диск СОВСЕМ, банк надо держать скачанным
# локально: в Google Диске это «Открыть доступ офлайн» на папке с медиа.
#
# ПОЧЕМУ САМО, А НЕ КОМАНДОЙ. Клиент не должен знать слово «прогрев». Прогрев
# включается в момент ПЕРВОГО чтения любого фото и охватывает все кадры,
# назначенные этой карусели, — ни одной новой строки в сборочных скриптах.
_WARMED = set()


def _read_whole(path):
    """Протянуть файл через себя, чтобы Диск положил его в локальный кэш.
    Ошибки глушим: прогрев — ускорение, а не проверка. Файла нет или он занят —
    это забота того, кто будет его открывать по-настоящему."""
    try:
        with open(path, "rb") as f:
            while f.read(1 << 20):
                pass
    except OSError:
        pass


def prefetch(paths, workers=8):
    """Прогреть файлы ПАРАЛЛЕЛЬНО. Возвращает число прогретых."""
    todo = [p for p in dict.fromkeys(paths)
            if isinstance(p, str) and p and p not in _WARMED]
    if not todo:
        return 0
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as ex:
        list(ex.map(_read_whole, todo))
    _WARMED.update(todo)
    return len(todo)


class _PhotoMap(dict):
    """Карта кадров, которая помнит, что назначили ИМЕННО ЭТОЙ сборке.

    Греть весь банк нельзя — в нём полсотни путей, и прогрев вышел бы дороже
    выигрыша. Поэтому запоминаем ключи, присвоенные ПОСЛЕ импорта модуля (а это
    ровно те кадры, что выбрала Валерия для этой карусели), и греем только их.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._chosen = []

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if isinstance(value, str) and value not in self._chosen:
            self._chosen.append(value)

    def warm(self):
        """Прогреть выбранные кадры. Зовётся сама из fit() при первом чтении."""
        if not self._chosen:
            return 0
        n = prefetch(self._chosen)
        self._chosen = []
        return n


PHOTOS = _PhotoMap({
 # ── обложечные (лицо крупно, живое) ──
 "podcast_red":  U+"aGaXCTBwV1YMHEo8kFyp3EfFekfEzp5npyqW9T9Sdbh31MJBkgM5_QGL9BWMVHDZLjn8df8VtITmCrDQy2awBqJq.jpg",
 "boat_red":     U+"6m7yZF8nst86_eShjuryzWULOClJ2ftrbgEqcGgoCqkbNPy_uul7S4d3BWrv2rAfFuLseJkyMtktRzibEsWNgXFx.jpg",
 "summit_sit":   U+"61VJ5qsWWiKrQaph5sKU8H8WczvJ20dYeTcSRq2awVobwV_qJ1MlhY5D9HFLzo19TW_HrV6R6WmaDPqXbzLbPS7M.jpg",
 "leopard":      U+"0e76LtWwCCf0EqssZMzs6KQwdG_G0YBrwCTaAZ3SI2_1f02srv960mPhU9_trJ10kDlsbxNvvztf06ALiPFyRCfx.jpg",
 # ── внутренние / фон под текст ──
 "summit_stand": U+"xUgZgklXSUGACQaU4bmEsVztXB8AekeNE8-S8I_VIPWWuRWlRtnTS5rXTlU4R5EsDYN_Zxq4ljy_gOXwJugoektu.jpg",
 "podcast_brown":U+"AGPWMPfhzk5RGi2a4mAFxgKjDkTzQZ5vVGP_aIv6Dh77i_WupuJb-UV4kzcfc3GhrpXTgLD7QohTBo3mHCrbagyp.jpg",
 "avanti":       U+"SrF8GAfutepmRuke4uzgpvEEub4JbxwVK0MpgyIhUKUtWtInJ-cNFwGRSAcTScSCBWXsFfhMKD4P-zIuO8R89UZp.jpg",
 "redcarpet":    U+"0tvRjOJTT-SBmUAmRiu87iXRg2DnT6Mad8Esvh_XnLEAf6mo0JIgiboUQIRy8Ghg5ER60Vw-cZtYxdB8KD17NKxZ.jpg",
 "car_red":      U+"ccTmufLTmFpbzzyUfLq3dB7t0efb66ZO1vIj0JLnbKAvVM09Etb8hOqGwJNnpkdZb_HFJteAbQSniui6j2CG6fI_.jpg",
 "car_cream":    U+"cC8MS2--Zazw-PbxY2nuJrufzyicxQHhj1FgjvyoQNC5KUnbXWZvryPndghLD15y56oadp0d6hqvl_PkgxmRpSiS.jpg",
 "restaurant":   U+"3BMIwrlDPlCIuZgPZRVS5xAmY3SPNBsyltMmCUcPYM3F7sG5tc04EYv1l9RLk6m7BvJ_qgPTsBbYNqDM6OWgFDeC.jpg",
 # ── «за кадром» / вдвоём ──
 "duo_studio":   U+"LrVbTEsQdC2_EF26KjK3e-ApLIGmlmw85GSCsJAGXTadN5WyIcvUIju3Nlt5S65ad1PfIoZnPvGFH2Le2GJL7Uay.jpg",
 "duo_table":    U+"_e2PCaRt3PZfkkHO_kujzXtApHJy7r4GwQYb03VUOv0uOZbMfSLkMA01zs5I-AiUk77SwjH4Zf88_omoXGLEffkV.jpg",
 "duo_wide":     U+"zd41UnyF5h8NkJ-zpdL9_ZrN8pm4QDoloxn7DSZiHjWVjc6u24-xaNl7qjCaU_55CpB2QA4GPU9s1KGATuJ-9k3V.jpg",
})
# ── вторая партия (19 фото), временные ключи до визуального тегирования ──
NEW = {
 "new01": "DcuboMvsXtCLzrl2ajkyLlOhbxl5xXElE2F5gcJddgC6r0CHJ6eSG__mt9KRkSHAfGt178z8hXCDhH62iBBdwZ8A.jpg",
 "new02": "Dg2dXyOnp-YABQRSrSDTUtIrUOoQBkKFX290mDmDY-93cVsruDG5eJCke8YWD-jLQISGXU8vlKiBerThRi7Nv1Pf.jpg",
 "new03": "Dj5Af72e69PDgT5e9ZAA7BCadfyQuKKVcYXgZojzk-51eBEMTKAAeRpN8Z7_2nCtzwsSgZlxmc9rLwHg0QRz2hTQ.jpg",
 "new04": "H6mjAnrA6XpdnIcZUHcsfUJdyF8Ii6BaGX5oYMfzNWNUKn6fmn0XyxvBIkFhQ3NB3zJA_81a-iQIHPTP-ybDPj0l.jpg",
 "new05": "HYg90HFnCPvOvrFb_xfxdVkuvtJefWSjamkHi2_MLuaMlN-wEeDB18NZ8j1ky1DnhHTpU4diD0240EM2aNRF9WzL.jpg",
 "new06": "HlsV2ZxKPNnAZeWCtAeskzUFpquS6hk5xjp1J6atMzwZOa-2bc_MgnGiAS-RHjlp6u90Gi2LUsRQVcmQOrpvkFxk.jpg",
 "new07": "Po964FD8hiyyiqBMwt4F4ua8UyBMY6HEJiFwYb9G-VUJg9vaR2Kso5p6tumjD2O89TJTfnxEmUSKWQeETsJYitIj.jpg",  # ГОРИЗОНТ
 "new08": "QsQrJoUpuvWmWHSnWDkidWbf2cGkVturXPGwG_oTkaMn2dRQZnx3S-4S_oiH3oWYRLHMDbt009MUDVnZ08z2G1My.jpg",
 "new09": "SX6_Es_bZ2__LlJGqkt6fHRxfly3VBCgoJv-1XAwl2lY-XUTNKwzh-Wgn9KxQ_jFQdN_jf4yvUBkHwZ1sLtAeAv7.jpg",
 "new10": "dZkrXkMFZHp_WYou982xOGfWc1lJqJBSHLUci8e3a1rILKO1wIE8SqUma1fsUACJVEznDqAIak0gebTb68UFLxbk.jpg",
 "new11": "idu-Kbg3dmqAJ8B860tTFOrFwYOcB423Jv06hw0Y6lP3t8LWLvb1_J4h6NMNSpfm7FerJxhxAa-UIT3VaVdl9i3q.jpg",
 "new12": "lrcC9KWGpSXjc8N2m0PIgdjjbTlkdE3aNzzsBGSCNyVl8nrRYHSeUsYI16sZZfgCf_LVMJXbde2T0M5RFO4QaLpl.jpg",
 "new13": "me5oAt2Hy8LX_9pHkAIh13sVZg3I9IqW8HiKp_bv7JseNrdbwR6q7gL4lY0G6Xw857tT74K0RLfMK4S2B7W9QR8Y.jpg",
 "new14": "nBUcUo2exyrkEpro3fiBrKG6S6UPm-j1aRkWE5Kh1ev-AoVAH1LcPTLEBGg-soQMzOeyXvfDDQoiUFyfme0-_V_n.jpg",
 "new15": "uA0OpDgLsSicOpMnvMvRpfb2iEbb3khd03xKfKwBUh_DP2fYPHpQRwZ7Zjm_GZGpEbvBZLEc9-qWXKi-3T5io6LL.jpg",
 "new16": "vNMBvb4xXeKTx5pkRBUZpeKb0Zl91CN-XHmll1foKM3V7BBa9W_L9-G9isWNsG7lq2y7MBTn-xXiidRVaxbeLfPY.jpg",  # ГОРИЗОНТ, маленький
 "new17": "ww_Qywu-LxEvE_hwOtdG6RBiGX7uLV-GM8OMAlZeFCVeuJapINf1i8fQg-aUEKFU9ri_B_fDzjGBPLVjIWaeCb2Q.jpg",
 "new18": "xeLnBS7GkowLLlnHougNX0V1b3FoxAsK5b1WK7JFGjvUBK6eNAhx6iYmpqPKb6M7PlQRTsN8dbRaHfJ2X2nf2Ftp.jpg",
 "new19": "zmqkulWsl8PiVLgueq2Z6Pqh3ovn_Zd4RJ0l560N4Cyrp3OcVIIuuP-pQlp9DhmaGOfBAAVQkZmiVPvjctFZnQ0u.jpg",
}
for k,v in NEW.items(): PHOTOS[k] = U+v

# ── третья партия (7 фото) ──
NEW3 = {
 "new20": "ChzOPFv9fj-paRQ5NdG0f-wYz5b671uSWRvyebU_yBdxNxWIrzg6u3KcUSn6rS9MYhts7kR0UDxNnd8YOf0cBn5B.jpg",
 "new21": "JKjo-9DaGbAzcI0Zj-Dj87yekPCbOtkl3My5EhxacQhDWbHbWilncO7eKZysHa3f0YtgzyaPpCgGhnzGLK8m-Miq.jpg",
 "new22": "Ldj94_6ECmKmTMzxiKWcM-JKqwjVIbF7k1MtYg6GBkDCK1M_b23hlIIpQ5vet_JkBeyatQyhU6YtdvEx1zOPpU0a.jpg",
 "new23": "RnKbPQvTUa2bmzgETQIPULFaosJCHvieuZVuRRCh7E-13RS__BDeuT-mRoaymXEFgz95Syt7tI4V66lAidhFFHTE.jpg",
 "new24": "iZjzyOtFcTbwkblQfSucTRLXneYMHE9rYxD6XYZdz56i5V9xAXnQWMRNTMgRvOa2uLzcMnsNhhH_EQvHXV1XrSjk.jpg",
 "new25": "mqnM1KdkpmS1aL_W3Ut9mTFdZ1S_QsYgCXMWeDg1DJpiOc-fcQHUq3B6UsY2wXXakLXYLTQlhS8DewCvlBW93IB2.jpg",
 "new26": "xIRiqhZIel9bAqsVWiOyWmVph9C999rjhLylusKEagrogZLTWPNwuPGRBRN3EoliJ4ZH2J8eB8PfzguS0I90sv5J.jpg",
}
for k,v in NEW3.items(): PHOTOS[k] = U+v

# алиасы под старые демо-вызовы, чтобы движок не падал
PHOTOS["studio"]  = PHOTOS["podcast_red"]
PHOTOS["car"]     = PHOTOS["car_red"]
PHOTOS["dubai"]   = PHOTOS["boat_red"]
PHOTOS["conf"]    = PHOTOS["summit_sit"]
PHOTOS["redsuit"] = PHOTOS["podcast_red"]

# Банк заполнен — всё, что назначено ВЫШЕ, это склад, а не выбор для карусели.
# Забываем его: греть будем только кадры, назначенные сборочным скриптом ПОСЛЕ
# импорта модуля.
PHOTOS._chosen.clear()

# ─── НИК АККАУНТА В ШАПКЕ СЛАЙДА ─────────────────────────────────────────────
#
# Ник печатается на самой картинке (см. nick() ниже) в раскладках cover,
# photo_list, compare и bignum. Он РАЗНЫЙ у каждого владельца коробки, поэтому
# в ядре рендера ему не место: коробка продана шести клиентам, и зашитое здесь
# значение уезжает на их материалы.
#
# Порядок разрешения (первое непустое):
#   1. os.environ["FACTORY_ACCOUNT_NICK"]  — разовый прогон / CI;
#   2. config/factory.config.json, ключ account_nick — ответ клиента;
#   3. память мастера (config/answers.memory.json) — тем же ключом;
#   4. NICK ниже — прежнее значение.
#
# ⚠️ NICK — НЕ «правильный ник проекта», а ЗАМОРОЖЕННОЕ ПРЕЖНЕЕ ЗНАЧЕНИЕ.
# Оно оставлено ровно для одного: правило байт-в-байт. Пока настройки нет,
# карусель обязана пересобираться той же картинкой, что и до этой правки, —
# иначе уже собранные и утверждённые материалы поедут. Аккаунт проекта —
# @chirkova.valeria (ПРОЕКТ.md, первая строка), и что подставить сюда вместо
# @dayanamaksim, решает владелец: см. отчёт по этой правке.
NICK = "@dayanamaksim"

# Ник читается ЛЕНИВО, в момент отрисовки, а не на импорте. engine4 импортируют
# 173 файла; чтение конфига на импорте подмешало бы .env в окружение каждого из
# них (factory_config.load делает это по контракту) — побочный эффект там, где
# его раньше не было. Отрисовок ника за сборку единицы, кэша достаточно.
_NICK_CACHE = []          # [] — ещё не читали; [значение] — прочитали


def _factory_config():
    """install/factory_config.py как модуль, БЕЗ правки sys.path.

    Канонический ридер настроек коробки (CONTRACT §3) лежит в install/ и на
    PYTHONPATH не попадает. Класть install/ в sys.path нельзя: engine4 —
    транзитивная зависимость почти всего репозитория, и его импорт не имеет
    права менять правила поиска модулей у 173 вызывающих. Поэтому грузим по
    пути. Нет файла (движок вынут из коробки, dev-запуск) — None, и ник
    остаётся прежним.
    """
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "install", "factory_config.py"),
                 os.path.join(os.path.dirname(here), "install", "factory_config.py")):
        if not os.path.isfile(cand):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_kz_factory_config", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:                                   # noqa: BLE001
            return None
    return None


def account_nick():
    """Ник, который печатается в шапке слайда. Настройки нет — прежнее значение.

    Ошибки чтения конфига НЕ роняют сборку: отсутствие настройки — штатный
    режим (CONTRACT §3), а не поломка.
    """
    if _NICK_CACHE:
        return _NICK_CACHE[0]
    value = ""
    env = os.environ.get("FACTORY_ACCOUNT_NICK", "")
    if env.strip():
        value = env.strip()
    else:
        fc = _factory_config()
        if fc is not None:
            try:
                value = str(fc.answer("account_nick", "") or "").strip()
            except Exception:                               # noqa: BLE001
                value = ""
    _NICK_CACHE.append(value or NICK)
    return _NICK_CACHE[0]

# ─── разметка: *акцент*  ==плашка==  **белый жирный** ───
import re as _re
_MARK = _re.compile(r'==(.+?)==|\*\*(.+?)\*\*|\*(.+?)\*')
def tokens(text):
    out, pos = [], 0
    for m in _MARK.finditer(text):
        if m.start() > pos:
            for w in text[pos:m.start()].split(): out.append((w, ""))
        if   m.group(1) is not None: st, c = "chip", m.group(1)
        elif m.group(2) is not None: st, c = "bold", m.group(2)
        else:                        st, c = "acc",  m.group(3)
        for w in c.split(): out.append((w, st))
        pos = m.end()
    if pos < len(text):
        for w in text[pos:].split(): out.append((w, ""))
    # приклеиваем одиночную пунктуацию к предыдущему слову (сохраняя стиль)
    merged = []
    for w, st in out:
        if merged and _re.fullmatch(r'[.,!?;:»)\]]+', w):
            pw, pst = merged[-1]; merged[-1] = (pw + w, pst)
        else:
            merged.append((w, st))
    return merged

def _is_chip(st, acc_as_chip=False):
    """Рисуется ли этот токен плашкой. На белом семействе *слово* — тоже плашка
    (acc_as_chip=True), на фото — цветной текст."""
    return st == "chip" or (st == "acc" and acc_as_chip)


def wrap_rich(d, text, f, maxw, fbold=None, acc_as_chip=False):
    # Явный перенос: символ "\n" в тексте — жёсткий разрыв строки. Без "\n"
    # поведение прежнее байт-в-байт (одна часть = как раньше по ширине).
    # Строка С ПЛАШКОЙ верстается по maxw-CHIP_PAD: подложку первого чипа мы
    # выносим влево до поля, значит вся строка едет на CHIP_PAD вправо, и без
    # этого запаса она вылезла бы за правое поле. Строки без плашек считаются
    # как раньше, до пикселя.
    lines = []
    sp = d.textlength(" ", font=f)
    for part in text.split("\n"):
        cur, curw, cpad = [], 0, 0
        for w_, st in tokens(part):
            ff = fbold if (st == "bold" and fbold) else f
            ww = d.textlength(w_, font=ff)
            add = ww if not cur else ww + sp
            pad = CHIP_PAD if (cpad or _is_chip(st, acc_as_chip)) else 0
            if curw + add <= maxw - pad:
                cur.append((w_, st, ff)); curw += add; cpad = pad
            else:
                if cur: lines.append(cur)
                cur, curw = [(w_, st, ff)], ww
                cpad = CHIP_PAD if _is_chip(st, acc_as_chip) else 0
        if cur: lines.append(cur)
    return lines

def draw_rich(d, lines, f, x, y, lh, acc, base=WHITE, shadow=False, acc_as_chip=False,
              bg=None):
    # acc_as_chip=True — для белых слайдов: акцент там живёт только заливкой,
    # поэтому *слово* рисуется плашкой, а не цветным текстом (см. ПРОЕКТ.md).
    # bg — цвет фона под плашкой. Передан → плашка получит волосяную обводку,
    # если её заливка на этом фоне неразличима (см. chip_edge).
    sp = d.textlength(" ", font=f)
    edge = chip_edge(acc, bg)
    for line in lines:
        # первый чип строки: подложка встаёт РОВНО ПО ПОЛЮ, буква — правее на
        # CHIP_PAD. Оптический край строки держит подложка.
        cx = x + CHIP_PAD if (line and _is_chip(line[0][1], acc_as_chip)) else x
        for w_, st, ff in line:
            ww = d.textlength(w_, font=ff)
            if _is_chip(st, acc_as_chip):
                asc, desc = ff.getmetrics()
                d.rounded_rectangle([cx-CHIP_PAD, y-2, cx+ww+CHIP_PAD, y+asc+desc-8],
                                    8, fill=acc, **edge)
                d.text((cx, y), w_, font=ff, fill=INK)
            else:
                col = acc if st == "acc" else base
                if shadow:
                    d.text((cx+2, y+3), w_, font=ff, fill=(0,0,0,140))
                d.text((cx, y), w_, font=ff, fill=col)
            cx += ww + sp
        y += lh
    return y

def wrap(d, t, f, mw):
    ws, ls, c = t.split(), [], ""
    for w_ in ws:
        s = (c+" "+w_).strip()
        if d.textlength(s, font=f) <= mw: c = s
        else:
            if c: ls.append(c)
            c = w_
    if c: ls.append(c)
    return ls

def dash(d, x, y, acc, w=48):
    d.rectangle([x, y, x+w, y+5], fill=acc)

def counter(d, i, n=8):
    f = font("Bold", 24); t = f"{i}/{n}"
    tw = d.textlength(t, font=f); x, y = W-M-tw, 50
    d.rounded_rectangle([x-18, y-10, x+tw+18, y+34], 22, fill=(0,0,0,110))
    d.text((x, y), t, font=f, fill=WHITE)

def nick(d, col=(200,196,192)):
    # Ник берём у account_nick(), а не у константы: у каждой коробки он свой.
    # Настройки нет — account_nick() вернёт NICK, и слайд выйдет прежним.
    d.text((M, 56), account_nick(), font=font("SemiBold", 22), fill=col)

def to_srgb(img):
    """Привести кадр к sRGB, если он снят в другом пространстве. Нет профиля —
    вернуть как есть.

    23.08.2026. Валерия: «фото теряют яркость». iPhone снимает в **Display P3** —
    охват шире sRGB, и профиль лежит в файле. Мы его игнорировали: числа
    оставались P3, а Instagram и любой браузер читают их как sRGB. Один и тот же
    код цвета в узком пространстве — цвет бледнее задуманного, поэтому кадр и
    выцветал.
    ЗАМЕР (IMG_0278.HEIC): насыщенность 61.3 → 70.8 (+15%), яркость и контраст
    на месте (141.1/66.8 → 140.7/66.9) — это не «поднять красок», а перестать
    врать о том, в каком пространстве снят кадр.

    Профиля нет — НЕ трогаем: старая половина банка приехала из облака без
    профиля, и о её пространстве мы не знаем ничего. Гадать там — портить.
    """
    icc = img.info.get("icc_profile")
    if not icc:
        return img.convert("RGB") if img.mode != "RGB" else img
    try:
        from PIL import ImageCms
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(src) or "").strip().lower():
            return img.convert("RGB") if img.mode != "RGB" else img
        return ImageCms.profileToProfile(
            img, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
    except Exception:
        # Битый или экзотический профиль — кадр важнее точности цвета.
        return img.convert("RGB") if img.mode != "RGB" else img


def photo_keys_for(path):
    """Ключи реестра PHOTOS, ведущие на этот путь. Пусто — путь не из реестра.

    Обратный поиск: у одного файла ключей бывает несколько (podcast_red и его
    псевдонимы studio/redsuit — один и тот же кадр)."""
    return sorted(k for k, v in PHOTOS.items() if v == path)


def photo_registry_problems():
    """Ключи реестра, ведущие на отсутствующие файлы: [(ключ, путь), ...].

    Отдельной функцией, а НЕ проверкой при импорте. Банк `photos/` — личный
    архив владельца и клиентам не уезжает НИ ОДНИМ патчем (сверено по каналу
    28.08.2026): проверка при загрузке уронила бы движок у каждого клиента,
    у которого этой папки нет и быть не должно. Здесь её зовут тогда, когда
    она уместна — сборка витрины стилей, тест, доктор."""
    return [(k, v) for k, v in sorted(PHOTOS.items())
            if isinstance(v, str) and not os.path.exists(v)]


def fit(path, box_h=H, dark=0.0):
    # Первое же чтение кадра тянет с Диска ВСЕ кадры этой карусели — разом и
    # параллельно. Дальше файлы берутся из локального кэша за сотые доли
    # секунды. Клиенту делать ничего не нужно: прогрев включается сам.
    PHOTOS.warm()
    # ОТКАЗ НАЗЫВАЕТ КЛЮЧ, А НЕ ТОЛЬКО ФАЙЛ. Реестр PHOTOS протухает: файл
    # переименовали или удалили, а ключ остался. Ключ разрешался в путь БЕЗ
    # проверки, и падало здесь, в момент сборки, обычным FileNotFoundError на
    # имени вида aGaXCTBw…jpg — по такому сообщению не понять ни какой ключ
    # виноват, ни что вообще сломалось (замер 23.08.2026: 8 ключей из 45
    # мертвы, включая обложечный podcast_red). Тот же класс молчания, что
    # МАК-Б3: файл есть в белом списке, но не на диске.
    if isinstance(path, str) and not os.path.exists(path):
        ключи = photo_keys_for(path)
        raise FileNotFoundError(
            "кадра нет на диске: %s\n"
            "%sПроверить весь реестр разом: engine4.photo_registry_problems()"
            % (path,
               ("В реестре PHOTOS на него ведут ключи: %s — почини их или "
                "верни файл.\n" % ", ".join(ключи)) if ключи else
               "В реестре PHOTOS такого пути нет — путь передан напрямую.\n"))
    img = to_srgb(Image.open(path))
    tr = W/box_h; iw, ih = img.size
    if iw/ih > tr:
        nw = int(ih*tr); img = img.crop(((iw-nw)//2, 0, (iw-nw)//2+nw, ih))
    else:
        nh = int(iw/tr); img = img.crop((0, 0, iw, nh))
    img = img.resize((W, box_h), Image.LANCZOS)
    if dark: img = Image.blend(img, Image.new("RGB",(W,box_h),(10,8,7)), dark)
    return img


# ══════════ 1. ОБЛОЖКА: фото + белый текст + акцент ══════════
def cover(hook, sub, photo, acc, out, i=1, n=8, pos="mid"):
    img = fit(PHOTOS[photo], H, dark=0.18)
    # локальное затемнение под текстом
    ov = Image.new("RGBA", (W, H), (0,0,0,0))
    od = ImageDraw.Draw(ov)
    top = int(H*0.42) if pos=="mid" else int(H*0.58)
    for y in range(top, H):
        a = int(150 * min(1, (y-top)/(H*0.22)))
        od.line([(0,y),(W,y)], fill=(8,6,5,min(a,150)))
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

    d = ImageDraw.Draw(img)
    counter(d, i, n); nick(d)
    fh, fs = font("Black", 66), font("Regular", 30)
    lines = wrap_rich(d, hook, fh, W-2*M)
    lh = 78
    y = top + 60
    y = draw_rich(d, lines, fh, M, y, lh, acc, shadow=True)
    y += 16
    for l in wrap(d, sub, fs, W-2*M):
        d.text((M+2, y+2), l, font=fs, fill=(0,0,0,120))
        d.text((M, y), l, font=fs, fill=(228,224,220)); y += 44
    img.save(out)


# ══════════ 2. СПЛИТ: фото сверху / панель снизу ══════════
def split(title, body, photo, acc, out, i=2, n=8, cut=0.44):
    img = Image.new("RGB", (W, H), PANEL)
    ch = int(H*cut)
    img.paste(fit(PHOTOS[photo], ch, dark=0.05), (0, 0))
    d = ImageDraw.Draw(img)
    counter(d, i, n)
    y = ch + 54
    dash(d, M, y, acc); y += 34
    fh, fb, fbold = font("Black", 50), font("Regular", 31), font("Bold", 31)
    y = draw_rich(d, wrap_rich(d, title, fh, W-2*M), fh, M, y, 62, acc) + 26
    y = draw_rich(d, wrap_rich(d, body, fb, W-2*M, fbold), fb, M, y, 48, acc, base=GREY)
    img.save(out)


# ══════════ 3. ФОТО НА ВЕСЬ + СПИСОК ══════════
def photo_list(title, intro, items, photo, acc, out, i=3, n=8):
    img = fit(PHOTOS[photo], H, dark=0.55)
    d = ImageDraw.Draw(img)
    counter(d, i, n); nick(d)
    fh, fi, fl = font("Black", 52), font("Regular", 30), font("Bold", 34)
    y = 330
    y = draw_rich(d, wrap_rich(d, title, fh, W-2*M), fh, M, y, 64, acc, shadow=True) + 20
    for l in wrap(d, intro, fi, W-2*M):
        d.text((M, y), l, font=fi, fill=(206,202,198)); y += 44
    y += 24
    for it in items:
        d.text((M, y+4), "–", font=fl, fill=acc)
        d.text((M+38, y), it, font=fl, fill=WHITE); y += 56
    img.save(out)


# ══════════ 4. СРАВНЕНИЕ КАРТОЧКАМИ ══════════
def compare(title, old_a, old_b, new, photo, acc, out, i=4, n=8):
    img = fit(PHOTOS[photo], H, dark=0.62)
    d = ImageDraw.Draw(img)
    counter(d, i, n); nick(d)
    fh = font("Black", 50)
    y = 300
    dash(d, M, y, acc); y += 32
    y = draw_rich(d, wrap_rich(d, title, fh, W-2*M), fh, M, y, 62, acc, shadow=True) + 34

    fk, fn_, fv = font("Bold", 22), font("Regular", 28), font("Black", 44)
    cw = (W-2*M-24)//2
    for k, (lbl, nm, val) in enumerate([old_a, old_b]):
        x = M + k*(cw+24)
        d.rounded_rectangle([x, y, x+cw, y+180], 10, fill=(38,35,33))
        d.text((x+24, y+22), lbl, font=fk, fill=(140,135,130))
        d.text((x+24, y+60), nm, font=fn_, fill=(206,202,198))
        d.text((x+24, y+104), val, font=fv, fill=(150,145,140))
    y += 180 + 22

    lbl, nm, val = new
    d.rounded_rectangle([M, y, W-M, y+180], 10, fill=(58,38,30), outline=acc, width=2)
    d.text((M+24, y+22), lbl, font=fk, fill=acc)
    d.text((M+24, y+60), nm, font=fn_, fill=(226,222,218))
    d.text((M+24, y+104), val, font=fv, fill=acc)
    img.save(out)


# ══════════ 5. ЦИФРА ВНУТРИ ЗАГОЛОВКА ══════════
def bignum(pre, num, post, body, photo, acc, out, i=5, n=8):
    img = fit(PHOTOS[photo], H, dark=0.58)
    d = ImageDraw.Draw(img)
    counter(d, i, n); nick(d)
    fp, fn_, fb, fbold = font("Black", 56), font("Black", 128), font("Regular", 30), font("Bold", 30)
    y = 330
    for l in wrap(d, pre, fp, W-2*M):
        d.text((M+2, y+3), l, font=fp, fill=(0,0,0,130))
        d.text((M, y), l, font=fp, fill=WHITE); y += 68
    d.text((M+3, y+4), num, font=fn_, fill=(0,0,0,140))
    d.text((M, y), num, font=fn_, fill=acc); y += 142
    for l in wrap(d, post, fp, W-2*M):
        d.text((M+2, y+3), l, font=fp, fill=(0,0,0,130))
        d.text((M, y), l, font=fp, fill=WHITE); y += 68
    y += 26
    dash(d, M, y, acc); y += 34
    y = draw_rich(d, wrap_rich(d, body, fb, W-2*M, fbold), fb, M, y, 48, acc, base=(200,196,192))
    img.save(out)


# ══════════ 6. CTA ══════════
def cta(title, sub, word, acc, out, i=8, n=8):
    img = Image.new("RGB", (W, H), PANEL_D)
    d = ImageDraw.Draw(img)
    counter(d, i, n)
    fh, fs, fw = font("Black", 52), font("Regular", 30), font("Black", 52)

    # центрируем
    hl = wrap(d, title, fh, W-2*M-40)
    sl = wrap(d, sub, fs, W-2*M-40)
    total = 5 + 40 + len(hl)*64 + 40 + len(sl)*46 + 60 + 170
    y = (H - total)//2

    dw = 56
    d.rectangle([(W-dw)//2, y, (W+dw)//2, y+5], fill=acc); y += 40
    for l in hl:
        lw = d.textlength(l, font=fh)
        d.text(((W-lw)//2, y), l, font=fh, fill=acc); y += 64
    y += 40
    for l in sl:
        lw = d.textlength(l, font=fs)
        d.text(((W-lw)//2, y), l, font=fs, fill=(170,165,160)); y += 46
    y += 60

    bw, bh = 430, 170
    bx = (W-bw)//2
    d.rectangle([bx, y, bx+bw, y+bh], fill=(48,32,26), outline=acc, width=2)
    ww = d.textlength(word, font=fw)
    d.text((bx+(bw-ww)//2, y+40), word, font=fw, fill=WHITE)
    f2 = font("Regular", 26)
    w2 = d.textlength("написать в директ", font=f2)
    d.text((bx+(bw-w2)//2, y+112), "написать в директ", font=f2, fill=(160,155,150))
    img.save(out)
