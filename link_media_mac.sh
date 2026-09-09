#!/bin/bash
# Связать медиа-папки из Google Drive в папку завода (macOS-аналог link_media.ps1).
#
# На Windows медиа подключаются junction-ссылками (link_media.ps1). На маке — тем
# же приёмом, но через симлинки: код и тексты живут в git/коробке, а тяжёлые фото/
# видео/музыка лежат в общей папке Google Drive и подключаются ссылками, не копиями.
#
# ПАРИТЕТ С WINDOWS (BOX-18). Список папок здесь — ТОТ ЖЕ и в том же порядке, что
# в $folders у link_media.ps1. Если правишь один скрипт — правь и второй.
# Осознанные различия (mac-специфика, не расхождение):
#   • путь к Drive не зашит буквой диска, а ищется в ~/Library/CloudStorage/;
#   • симлинки вместо junction (на маке не нужен «Режим разработчика»);
#   • папка-приёмник из коробки (пустая, только с .keep) заменяется ссылкой —
#     на Windows этого нет, потому что link_media.ps1 в коробку не едет;
#   • во «плоскую» сборку video/photos берутся и файлы, и вложенные папки
#     (в .ps1 — только файлы): на Drive у владельца там встречаются подпапки.
#
# ПРЕДУСЛОВИЕ (делает человек, не агент): установлен Google Drive for Desktop и
# выполнен вход в ТОТ ЖЕ гугл-аккаунт, где лежит karusel_media. Тогда Drive
# монтируется в ~/Library/CloudStorage/GoogleDrive-<почта>/.
#
# ЗАПУСК:  bash link_media_mac.sh            # автопоиск karusel_media на Drive
#          bash link_media_mac.sh "/путь/к/karusel_media"   # указать вручную
#
# Шрифты: fonts/ ЕДЕТ В КОРОБКЕ вместе с движком (fonts/Onest.ttf + лицензия OFL).
# Этот скрипт трогает fonts/ только если своей папки нет — и в конце честно
# проверяет, что шрифт на месте: без Onest.ttf движок не нарисует ни одного кадра.
set -e
cd "$(dirname "$0")"

MEDIA="$1"
if [ -z "$MEDIA" ]; then
  # ищем karusel_media в любом смонтированном Google Drive
  for gd in "$HOME"/Library/CloudStorage/GoogleDrive-*; do
    [ -d "$gd" ] || continue
    for cand in "$gd/My Drive/karusel_media" "$gd/Мой диск/karusel_media" \
                "$gd"/Shared\ drives/*/karusel_media; do
      [ -d "$cand" ] && { MEDIA="$cand"; break 2; }
    done
  done
fi

if [ -z "$MEDIA" ] || [ ! -d "$MEDIA" ]; then
  echo "✗ karusel_media на Google Drive не найдена."
  echo "  1) Установи Google Drive for Desktop и войди в аккаунт с karusel_media."
  echo "  2) Дождись, пока папка появится (значок Drive → синхронизировано)."
  echo "  3) Повтори, либо укажи путь: bash link_media_mac.sh \"/полный/путь/karusel_media\""
  exit 1
fi
echo "✓ karusel_media: $MEDIA"

# Пустая ли папка-приёмник из коробки: внутри нет ничего, кроме служебных .keep
# и мусора вроде .DS_Store. Такую можно спокойно снести и заменить ссылкой.
is_empty_receiver() {
  local dir="$1" f
  [ -d "$dir" ] || return 1
  for f in "$dir"/* "$dir"/.[!.]*; do
    [ -e "$f" ] || continue
    case "$(basename "$f")" in
      .keep|.DS_Store) continue ;;
      *) return 1 ;;
    esac
  done
  return 0
}

# Снести пустой приёмник из коробки, чтобы освободить имя под симлинк.
# Свою НЕпустую папку не трогает никогда (возвращает 0 — с set -e это безопасно).
drop_receiver() {
  local dir="$1"
  is_empty_receiver "$dir" || return 0
  rm -f "$dir/.keep" "$dir/.DS_Store"
  rmdir "$dir" 2>/dev/null || true
}

# Собрать плоскую папку из подпапок по авторам, если на Drive нет плоской.
# Медиа на Drive реорганизовали в «Anton video»/«Valeriya video» (и фото так же),
# а весь код завода ждёт плоский video/<имя>. Восстанавливаем плоский вид ссылками.
build_flat() {
  local name="$1"; shift            # напр. video
  # 1) если на Drive есть плоская папка — старое поведение (симлинк на неё)
  if [ -d "$MEDIA/$name" ]; then
    [ -L "$name" ] && rm "$name"
    drop_receiver "$name"
    [ -e "$name" ] && { echo "  ! $name существует и не ссылка — пропускаю"; return; }
    ln -s "$MEDIA/$name" "$name" && echo "  → $name (симлинк на плоскую папку)"
    return
  fi
  # 2) иначе собираем локальную папку из подпапок авторов ($@)
  local any=0 s
  for s in "$@"; do [ -d "$MEDIA/$s" ] && any=1; done
  if [ "$any" = 0 ]; then
    echo "  ! ни $MEDIA/$name, ни подпапок авторов нет — пропускаю $name"; return
  fi
  [ -L "$name" ] && rm "$name"            # снять старый повисший симлинк
  [ -e "$name" ] && [ ! -d "$name" ] && { echo "  ! $name существует и не папка — пропускаю"; return; }
  mkdir -p "$name"
  local n=0 dup=0 f base
  for s in "$@"; do
    [ -d "$MEDIA/$s" ] || continue
    for f in "$MEDIA/$s"/*; do
      [ -e "$f" ] || continue
      base="$(basename "$f")"
      if [ -e "$name/$base" ]; then dup=$((dup+1)); continue; fi
      ln -s "$f" "$name/$base" && n=$((n+1))
    done
  done
  echo "  → $name/ собрана из подпапок авторов: $n ссылок, дублей пропущено: $dup"
}

# ── Список папок — ПАРИТЕТ с $folders в link_media.ps1 (тот же состав и порядок).
# fonts тут не случайно: без fonts/Onest.ttf движок не рисует ни одного кадра.
FOLDERS=(music music_lib music_cache music_preview talking primer fonts inter
         stock karusel_ROST files_extracted out_c3 out_c4 out_c5 out_c6 out_c7
         out_c8 out_reels out_talk out_test "фото для слайдов")

skipped_absent=()
for d in "${FOLDERS[@]}"; do
  if [ ! -d "$MEDIA/$d" ]; then
    skipped_absent+=("$d")          # нет на Диске — это норма, копим для сводки
    continue
  fi
  [ -L "$d" ] && rm "$d"                       # снять старую (в т.ч. повисшую) ссылку
  drop_receiver "$d"                           # пустой приёмник из коробки — под замену
  if [ -e "$d" ]; then
    echo "  ! $d уже есть своей папкой (не ссылка) — оставляю как есть"
    continue
  fi
  ln -s "$MEDIA/$d" "$d" && echo "  → $d"
done

if [ "${#skipped_absent[@]}" -gt 0 ]; then
  echo "  · нет на Диске, пропущено: ${skipped_absent[*]}"
fi

# video и photos — плоский вид из папок по авторам (или плоская папка, если есть)
build_flat video  "Anton video"  "Valeriya video"
build_flat photos "Anton photo"  "Valeriya photos"

# .env с ключами тоже держим на Drive и подключаем ссылкой (в git он не идёт)
if [ -f "$MEDIA/.env" ] && [ ! -e ".env" ]; then
  ln -s "$MEDIA/.env" .env && echo "  → .env"
fi

echo
# ── Шрифт — критичная проверка, а не украшение ────────────────────────────────
# engine4._ONEST жёстко указывает на fonts/Onest.ttf. Нет файла — рендер падает
# на первом же кадре, и понять причину по ошибке Pillow клиент не сможет.
if [ -f "fonts/Onest.ttf" ]; then
  echo "✓ шрифт fonts/Onest.ttf на месте — движок нарисует кадры."
else
  echo "✗ НЕТ fonts/Onest.ttf — движок каруселей НЕ нарисует ни одного кадра."
  echo "  Шрифт входит в коробку (папка fonts/, лицензия OFL — fonts/OFL-Onest.txt)."
  echo "  Что делать: распакуй архив завода заново целиком либо положи Onest.ttf"
  echo "  в папку fonts/. После этого запусти проверку установки ещё раз."
fi

echo
echo "Готово. Проверка исходников для обкатки:"
ls -la talking/ 2>/dev/null | head -6 || echo "  папка talking пуста — ждём съёмку Валерии"
