# THIRD_PARTY — сторонние компоненты офлайн-коробки «Контент-завода»

Владелец файла: ветка **OWNER** (слой упаковки). Метод — **по файлам LICENSE, а не по
описанию**: пакеты скачаны колёсами, распакованы, прочитаны их `METADATA`,
`LICENSE*/COPYING*/*bundled*/*ThirdPartyNotices*` и вложенные бинарники. Механизирует
это `installer/license_audit.py` (стоп-гейт GPL/AGPL). Разрешено (бриф A1 + CONTRACT.md
§3): **MIT / BSD / Apache-2.0 / SIL OFL / ISC** и подобные пермиссивные. **GPL/AGPL —
запрещены.** `protobuf==4.25.9` не трогать; pip только `==`.

---

## 🔴 ГРОМКО: НАЙДЕН GPL В ЦЕПОЧКЕ — ДВА БЛОКЕРА ПОСТАВКИ

Проверка реальных LICENSE-файлов колёс выявила **GPL-код внутри бинарных колёс**.
Это **прямое нарушение** запрета GPL/AGPL. Оба пакета — **не в зоне OWNER** (владелец
модулей — Антон/main); OWNER **флагует и предлагает замену, но не правит** чужой код.

### 1. `pillow_heif==1.1.1` (модуль **intake**) — binary wheel = **GPLv2**. БЛОКЕР.

Доказательство из самого пакета (`LICENSES_bundled.txt`, дословно):

> «**License for "pillow-heif" binary wheels: GPLv2, due to base library licenses.**»
> Name: **x265** — License: **GPLv2** — Files: `libx265.[dylib|so|dll]`

Внутри колеса физически лежит `libx265` (HEVC-энкодер, GPLv2) — на **всех** платформах
(`dll` = Windows тоже). `METADATA` пакета несёт классификатор
`License :: OSI Approved :: GNU General Public License v2 (GPLv2)`. Питон-обвязка сама
по себе BSD-3-Clause, но **распространяемое колесо — GPLv2**.

**Рекомендация владельцу intake:** заменить `pillow_heif` → **`pi-heif`** — это
«облегчённая» сборка того же проекта, **только декодирование** HEIC (для чтения фото с
iPhone энкодер x265 не нужен), бандлит лишь `libheif`+`libde265` (**LGPLv3**, без x265,
без GPL). API совместим. Так GPL уходит из цепочки, а чтение HEIC сохраняется.

### 2. `av==15.1.0` (тянется `faster-whisper`/`whisperx` в **engine-media**) — GPL в **WINDOWS**-колесе. БЛОКЕР ПОДТВЕРЖДЁН (BOX-23).

Раньше здесь стояло «в mac-колесе GPL, win проверить». **Проверено. Windows-колесо —
тоже GPL.** Проверялось именно целевое колесо, не mac:

```bash
pip download av==15.1.0 --platform win_amd64 --python-version 311 \
             --only-binary=:all: --no-deps -d /tmp/avwin
unzip -q av-15.1.0-cp311-cp311-win_amd64.whl -d x    # колесо = обычный zip
```

**Доказательство №1 — GPL-библиотеки физически лежат внутри win-колеса.** В `av.libs/`
(34 DLL, положены `delvewheel repair`, см. `av-15.1.0.dist-info/DELVEWHEEL`):

| Файл в колесе | Размер | Лицензия |
|---|---|---|
| `libx264-165-61acd10b….dll` | 2,4 МБ | **GPL-2.0-or-later** |
| `libx265-9e24aa06….dll` | 22,1 МБ | **GPLv2** |

**Доказательство №2 — это не «мёртвый груз», FFmpeg с ними слинкован.** Таблица
импорта `avcodec-61-1306a7df….dll` содержит обе библиотеки:

```
libx264-165.dll      libx264-165-61acd10b62783aea048e2d39227c2895.dll
libx265.dll          libx265-9e24aa06f62577bd43f0ac3911a2841c.dll
```

**Доказательство №3 — так собрана сама FFmpeg.** Строка конфигурации, зашитая в
`avutil-59-….dll`, содержит `--enable-libx264 --enable-libx265`.

**Что при этом заявляет пакет (и почему этому нельзя верить).** `METADATA` пишет
`License-Expression: BSD-3-Clause`, а `LICENSE.txt` — чистый BSD-3. Более того, строка
`libavcodec license:` внутри DLL говорит «LGPL version 3 or later». Всё это относится к
**обвязке PyAV и к исходникам FFmpeg**, но не к тому, что реально распространяется:
внутри колеса едут GPL-энкодеры x264/x265, и avcodec на них слинкован. Ровно поэтому
`license_audit.py` смотрит на **файлы и бинарники**, а не на поле `License` — по полю
`License` это колесо выглядит безупречно.

**Проверка не единична.** То же самое в соседних версиях (whisperx требует `av<16`,
faster-whisper — `av>=11`):

| Версия av | `libx264` | `libx265` |
|---|---|---|
| 12.3.0 win_amd64 | есть | есть |
| 13.1.0 win_amd64 | есть | есть |
| **15.1.0 win_amd64** | **есть** | **есть** |

Т. е. **простым «понизить версию» это не лечится** — GPL-энкодеры в бинарных колёсах
PyAV присутствуют во всём диапазоне, разрешённом whisperx.

**ВЕРДИКТ: `av` в текущем виде поставлять клиенту нельзя.** Не «вероятно», не
«проверить на Windows» — GPL внутри windows-колеса **есть**, доказано по файлам.

**Заодно починен сам гейт.** `license_audit.py` искал вложенные нативные библиотеки
только внутри каталога пакета (`av/`). Это верно для macOS (`delocate` → `av/.dylibs/`),
но на **Windows** `delvewheel` кладёт DLL **рядом**: `av.libs/`. Замер: по 97
скачанным win-колёсам гейт до правки давал **0 блокеров** — GPL-колесо прошло бы в
сборку. После правки (в обход добавлены `<pkg>.libs/`, `<name>.libs/`,
`<pkg>/.dylibs/`) — **1 блокер, ровно `av`**.

#### Пути решения (решает владелец **engine-media**; OWNER-упаковка флагует, но не правит)

Важный факт, снимающий половину проблемы: **завод сам `av` не использует.** В коде нет
ни одного `import av`; `talk_transcribe.py` декодирует дорожку **своим** путём — через
`imageio_ffmpeg.get_ffmpeg_exe()` (LGPL-бинарь) в numpy-массив, и уже массив отдаёт в
`whisperx`. `av` требуется только как **импортируемая зависимость** библиотек
(`faster_whisper.audio`, `whisperx.audio` импортируют его на верхнем уровне). То есть
GPL-код мы бы **распространяли, ни разу не вызвав**.

| # | Путь | Что делать | Цена | Риск |
|---|---|---|---|---|
| **A** | **Пересобрать `av` против LGPL-FFmpeg** (рекомендуется) | Один раз на Windows-build-машине собрать FFmpeg **без** `--enable-gpl`/`--enable-libx264`/`--enable-libx265` (или взять готовый LGPL-shared-билд), затем `PYAV_LIBRARY_PATH=… pip wheel av==15.1.0` и положить своё колесо в `installer/vendor/wheels`. Гейт после этого чист | 0,5–1 день разово, нужен MSVC/toolchain | средний: колесо своё, придётся пересобирать при обновлении av |
| **B** | **Взять `av` из conda-forge, вариант `ffmpeg=*=lgpl*`** | conda-forge публикует LGPL-вариант ffmpeg и линкованный с ним `av`; вытащить бинарники и переупаковать в wheel | ~0,5 дня | средний: смешение conda/pip в offline-venv |
| **C** | **Отвязать `av` совсем** | `faster_whisper`/`whisperx` импортируют `av` только ради `decode_audio`, которым мы не пользуемся. Технически возможно поставлять без `av` и предоставить минимальную заглушку модуля — но это патч чужой библиотеки | 1–2 часа | высокий: молча ломается на любом обновлении faster-whisper |
| **D** | Коммерческая лицензия x264/x265 | Купить у VideoLAN / MulticoreWare | деньги, месяцы | не рассматриваем |
| **E** | Сузить движок до `imageio-ffmpeg`-only ASR (убрать whisperx, оставить `faster-whisper` c ручным декодом) | `faster-whisper` тоже требует `av>=11` — **не помогает** | — | — |

**Рекомендация OWNER: путь A.** Он единственный оставляет цепочку зависимостей штатной
(pip, `==`, офлайн-wheelhouse) и убирает GPL физически, а не на словах.

> Сборка `.exe` НЕ должна выпускаться, пока `license_audit.py` по вендоренному
> wheelhouse не вернёт **0 блокеров**. Сейчас по `av` он возвращает rc=1 — это верно.

---

## Зависимости pip (прямые, из `module.manifest.json`)

Колонка «Проверка»: **[wheel]** — лицензия прочитана из реального LICENSE/METADATA
скачанного колеса на маке; **[SPDX]** — известная лицензия проекта, **подтверждается на
win-колесе** гейтом при вендоринге (тяжёлые нативные пакеты на маке не ставились).

| Пакет | Версия | Лицензия | Вердикт | Проверка |
|---|---|---|---|---|
| requests | 2.32.5 | Apache-2.0 | OK | [wheel] |
| protobuf | 4.25.9 | BSD-3-Clause | OK | [wheel] |
| ctranslate2 | 4.8.1 | MIT | OK | [wheel] |
| faster-whisper | 1.2.1 | MIT | OK | [wheel] |
| imageio-ffmpeg | 0.6.0 | BSD-2-Clause (+ FFmpeg LGPL-бинарь, см. ниже) | OK | [wheel] |
| vk-api | 11.9.9 | Apache-2.0 | OK | [wheel] |
| onnxruntime | 1.19.2 | MIT | OK¹ | [wheel] |
| numpy | 2.0.2 | BSD-3-Clause (+ libquadmath GPL-3.0-**с-GCC-исключением**) | OK² | [wheel] |
| pillow | 11.3.0 | MIT-CMU (HPND) | OK³ | [wheel] |
| ~~pillow_heif~~ → **pi-heif** | 1.1.1 | BSD-3 обвязка / LGPLv3 (libheif, libde265) | **заменён владельцем intake** ⁴ | [wheel] |
| **av** | **15.1.0** | **BSD-3 заявлена / GPL по факту: x264+x265 в WIN-колесе** | **БЛОКЕР (подтверждён, BOX-23)** | [wheel win_amd64] |
| torch | 2.8.0 | BSD-3-Clause (CPU-сборка) | OK | [SPDX] |
| torchvision | 0.23.0 | BSD-3-Clause | OK | [SPDX] |
| torchaudio | 2.8.0 | BSD-2-Clause | OK | [SPDX] |
| transformers | 4.57.6 | Apache-2.0 | OK | [SPDX] |
| open-clip-torch | 2.32.0 | MIT | OK | [SPDX] |
| mediapipe | 0.10.14 | Apache-2.0 | OK | [SPDX] |
| opencv-python-headless | 4.11.0.86 | Apache-2.0 (OpenCV) / MIT (обвязка) | OK | [SPDX] |
| whisperx | 3.7.5 | BSD-2-Clause | OK | [SPDX] |
| aiogram | 3.27.0 | MIT | OK | [SPDX] |

¹ `onnxruntime` сам MIT; в его `ThirdPartyNotices.txt` встречается слово «Affero» — это
boilerplate текста **LGPL-2.1** одного из нотисов, а не лицензия пакета (гейт помечает
это WARN, не блокером). Проверить, что AGPL-компонент реально не залинкован, при
обновлении версии.
² `numpy` — BSD-3-Clause. Совпадение «AGPL» в `LICENSE.txt` — это раздел 13 текста
**GPLv3**, приложенного к **libquadmath/libgfortran** под **GNU GCC Runtime Library
Exception**, которое **явно разрешает** распространение вместе с не-GPL/проприетарным
софтом. Не блокер; зафиксировано.
³ `pillow` — MIT-CMU. Упоминание «GPL/LGPL» в LICENSE — это описание вариантов лицензии
**FreeType (FTL)**, а не лицензия Pillow. Не блокер.
⁴ `modules/intake/module.manifest.json` уже несёт `pi-heif==1.1.1` вместо
`pillow_heif` — блокер №1 закрыт владельцем intake; `installer/manifest.json`
пересобран под это (`installer/build_manifest.py --out installer/manifest.json`).

## Нативные бинарники внутри колёс (то, что реально распространяется)

| Библиотека | Откуда | Лицензия | Статус |
|---|---|---|---|
| FFmpeg (бинарь) | `imageio-ffmpeg` | **LGPL-2.1+** (сборка без GPL-энкодеров) | допустимо (динамич.) |
| libheif, libde265 | `pi-heif` (после замены) | **LGPLv3** | допустимо (динамич. линковка) |
| libaom, dav1d, SvtAv1 | `av`/heif | BSD-3 / BSD-2 | OK |
| **libx265** | `av` — **win_amd64-колесо**, `av.libs/libx265-9e24aa06….dll`, 22,1 МБ | **GPLv2** | **ЗАПРЕЩЕНО — убрать (BOX-23)** |
| **libx264** | `av` — **win_amd64-колесо**, `av.libs/libx264-165-61acd10b….dll`, 2,4 МБ | **GPL-2.0+** | **ЗАПРЕЩЕНО — убрать (BOX-23)** |

> **LGPL-примечание.** `libheif/libde265/FFmpeg` под **LGPL** едут как **отдельные
> динамические** библиотеки (`.dll`) — это совместимо с проприетарной поставкой при
> условии заменяемости библиотеки; бриф явно запрещает только **GPL/AGPL**. Решение
> «LGPL допустим» фиксируем здесь как осознанное. **GPL (x264/x265) — не допустим ни
> при каких условиях.**

## Модели (веса; едут в `.cache_models`, докладываются `install_models.py`)

| Роль | Репозиторий | Лицензия |
|---|---|---|
| Whisper ASR (faster-whisper) | `Systran/faster-whisper-<size>` | **MIT** |
| Форс-выравнивание ru | `jonatasgrosman/wav2vec2-large-xlsr-53-russian` | **Apache-2.0** |
| VAD (детектор речи, pyannote via whisperx) | зеркало whisperx (токен HF не нужен) | **MIT** |

Токен HuggingFace не нужен; платных API нет.

## Шрифты

| Файл | Роль | Лицензия | Файл лицензии |
|---|---|---|---|
| `fonts/Onest.ttf` | базовая семья движка (`engine4.FAMILY`) | **SIL OFL 1.1** | `fonts/OFL-Onest.txt` (проверен заголовок OFL 1.1) |
| `fonts/Inter/Inter-Variable.ttf` | пресеты «Деловой строгий», R31 | **SIL OFL 1.1** | `fonts/Inter/OFL.txt` |
| `fonts/Manrope/Manrope-Variable.ttf` | пресеты «Минимал светлый», R31 | **SIL OFL 1.1** | `fonts/Manrope/OFL.txt` |
| `fonts/GolosText/GolosText-Variable.ttf` | пресет «Премиум тёмный» | **SIL OFL 1.1** | `fonts/GolosText/OFL.txt` (проверено по name ID 13 бинарника) |
| `fonts/Rubik/Rubik-{Regular,Bold}.ttf` | пресеты «Дерзкий контрастный», «Энергичный молодёжный» | **SIL OFL 1.1** | `fonts/Rubik/OFL.txt` (проверено по name ID 13 бинарника) |
| `fonts/NotoSerif/NotoSerif-{Regular,Bold}.ttf` | пресет «Мягкий женственный» | **SIL OFL 1.1** | `fonts/NotoSerif/OFL.txt` (проверено по name ID 13 бинарника) |

Все гарнитуры вендорятся в репозиторий вместе с текстом лицензии в той же папке;
скачивание шрифтов в рантайме запрещено (правило коробки). Заголовок каждой
`OFL.txt` — строка copyright из name-таблицы самого бинарника, ниже — стандартный
текст OFL 1.1. Кириллица каждой гарнитуры проверена рендером:
`python styles/make_previews.py --brand` → `styles/preview/cyrillic_check.png`.

> SIL OFL 1.1 требует поставлять текст лицензии **вместе** со шрифтом — поэтому
> `installer/preflight.py` валит приёмку, если рядом с `.ttf` нет своего `OFL`-файла,
> и если гарнитура не описана в `installer/manifest.json → fonts[]`.
>
> **`fonts/Onest.ttf` не лежит в git** (`.gitignore`: `fonts/*` с исключениями для
> вендоренных семейств). На свежем клоне на build-машине его НЕ будет, и движок
> упадёт на `engine4._ONEST` — завод не нарисует ни кадра. Файл переносится на
> build-машину руками; `preflight.py` ловит это отдельной проверкой.

## Системные бинарники (не в архиве; мягкие)

| Бинарь | Лицензия | Роль |
|---|---|---|
| ffmpeg (системный) | необязателен — движок берёт из `imageio-ffmpeg` (LGPL) | — |
| yt-dlp | Unlicense (public domain) | только для `/niche-hooks` (этап 3) |

## Упаковка

| Компонент | Лицензия |
|---|---|
| Inno Setup 6 (`iscc`) | собственная разрешительная (модификация BSD-подобной; распространение установщика свободно) |
| Python (embeddable 3.11.9) | **PSF** (Python Software Foundation License) |
| pip (bootstrap-колесо) | MIT |
| **Microsoft Visual C++ Runtime** (`msvcp140*.dll`, `vcomp140.dll`, `concrt140.dll`) | **редистрибутив Microsoft**, см. ниже |

### Почему в коробке лежат DLL Microsoft (решение 04.08)

**Замер.** Ни embeddable-Python, ни одно из тяжёлых колёс (`torch`, `opencv-*`,
`onnxruntime`, `mediapipe`, `ctranslate2`, `av`) НЕ везёт C++-runtime — проверено
перебором содержимого `.whl`. Они рассчитывают на системный «Microsoft Visual C++
Redistributable». На чистой Windows его нет → `import torch` / `import cv2` падают
«DLL load failed», то есть завод мёртв.

**Почему нельзя просто попросить клиента доустановить.** Установка Redistributable
требует **прав администратора**, а установщик их намеренно не запрашивает
(`PrivilegesRequired=lowest`, ставится в профиль пользователя). Клиент на рабочем
или семейном компьютере без админа оказался бы в тупике без выхода.

**Решение.** Кладём runtime рядом с `python.exe` (**app-local deployment** —
способ, который Microsoft поддерживает штатно: каталог исполняемого файла входит в
путь поиска DLL). Права администратора не нужны, офлайн-обещание сохраняется.
Файлы входят в перечень редистрибутивов Microsoft и распространяются с приложением
на условиях лицензии Visual Studio.

- копирует — `build_box.py::stage_vcruntime` (источник: `System32` **сборочной**
  машины, поэтому собирать надо на машине с установленным Redistributable x64);
- проверяет — `preflight.py::check_vcruntime` (нет DLL → приёмка не пройдена);
- страхует у клиента — `installer/smoke_render.py` пробует `cv2`/`mediapipe`/`torch`
  и при `DLL load failed` объясняет причину по-русски вместо системной ошибки.

## Как воспроизвести проверку

```bash
# На подготовке (владелец): вендоринг win-колёс + гейт по факту
python installer/fetch_wheels.py                    # качает + прогоняет license_audit
# Отдельно — гейт по готовому wheelhouse:
python installer/license_audit.py --wheelhouse installer/vendor/wheels
# Гейт по установленному окружению у клиента:
python installer/license_audit.py --site .venv/Lib/site-packages
# Полная приёмка коробки (состав + секреты + манифест + лицензии + wheelhouse):
python installer/preflight.py --make-stage stage --fetch
```

Точечная перепроверка BOX-23 (любая ОС, нужен только интернет):

```bash
pip download av==15.1.0 --platform win_amd64 --python-version 311 \
             --only-binary=:all: --no-deps -d /tmp/avwin
cd /tmp/avwin && unzip -q av-15.1.0-cp311-cp311-win_amd64.whl -d x
ls x/av.libs | grep -i 'x26[45]'                 # → libx264-165-….dll, libx265-….dll
strings -a x/av.libs/avcodec-61-*.dll | grep -i 'libx26[45].*\.dll'   # → слинковано
```

`license_audit.py` возвращает **rc=1** при любом GPL/AGPL — сборка `.exe` должна на этом
останавливаться (см. `BUILD-RECIPE.md`).
