# -*- coding: utf-8 -*-
"""
activation — активация копии «Контент-завода» (первый вопрос мастера установки, BOX-29).

ЗАЧЕМ. Коробку отдают за деньги. Активация привязывает копию к КЛИЕНТУ и к его
МАШИНЕ (не к соцсетям — соцаккаунтов сколько угодно). Полноценная схема:
  ОФЛАЙН-ядро (всегда):
    1) клиент получает файл license.key — подписанную владельцем (Ed25519) лицензию;
    2) activation.py проверяет подпись ПУБЛИЧНЫМ ключом (license_pubkey.py) локально;
    3) снимается отпечаток машины (machine_fingerprint) и пишется локальная запись
       config/activation.json с контрольной свёрткой (tamper-evidence);
    4) гейт require_activation() не даёт заводу работать без валидной активации.
  ОНЛАЙН-слой (включается, только если задан сервер лицензий — иначе спит):
    5) при активации и раз в неделю — сверка с реестром: жёсткий контроль числа
       машин (seats), ОТЗЫВ лицензии, продление. Работу офлайн НЕ блокирует: grace 30
       дней без связи. Нет сервера/нет интернета → чистый офлайн, как в п.1–4.

РЕЖИМ ГЕЙТА.
  • На КЛИЕНТСКОЙ сборке (штамп config/.client_build, ставит установщик) — ЖЁСТКИЙ:
    без валидной активации завод не рендерит и не публикует.
  • В нашем dev-checkout (штампа нет) — МЯГКИЙ: предупреждает, но не блокирует, чтобы
    разработка/наши машины не ломались. Принудительно жёстко: KZ_ENFORCE_ACTIVATION=1
    или файл config/activation.enforce.

ЧЕСТНО (см. docs/ДИЗАЙН_OTP_ВЕРИФИКАЦИИ.md §1). Подпись доказывает, что лицензию
выпустил владелец (неподделываемо). Клиентский гейт в читаемом .py обходится — поэтому
критичная часть КОМПИЛИРУЕТСЯ Nuitka (шаг build-пайплайна). Онлайн-реестр ловит шаринг
и возвраты. Всё вместе — «замок от честных» + бизнес-модель (обновления/поддержка/отзыв).

ОБРАТНАЯ СОВМЕСТИМОСТЬ. Мастер /install зовёт `--check "<КЛЮЧ>"` и `--json`. `--check`
умный: путь к license.key или JSON-лицензия → криптопроверка; старый код формата
KZ-XXXXX-… по-прежнему принимается локально (legacy, verified=False).

ГРАНИЦЫ (CONTRACT.md §3): только stdlib + вендоренный _ed25519 (чистый Python),
кроссплатформенно, без input(), без секретов в git. Ключ клиента (license.key) —
в чат не печатается; в маркере хранится подпись, не «пароль».

Запуск (из корня установки):
    python installer/activation.py --check "license.key"   # активировать по файлу лицензии
    python installer/activation.py --status                 # статус (по-русски)
    python installer/activation.py --json                   # машинная сводка (для мастера)
    python installer/activation.py --gate                   # проверить гейт (для завода/CI)
    python installer/activation.py --checkin                # онлайн-сверка (если задан сервер)
    python installer/activation.py --gen 3                  # legacy: N кодов формата (тесты)

Код возврата: 0 — успех/статус; 1 — отказ (формат/подпись/машина/отзыв); 2 — плохой аргумент.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _ed25519 as ed                     # noqa: E402  вендоренный Ed25519 (чистый Python)
import machine_fingerprint as mfp         # noqa: E402

try:
    from license_pubkey import PUBLIC_KEY_HEX   # noqa: E402
    _PUBKEY = bytes.fromhex(PUBLIC_KEY_HEX)
except Exception:                          # ключ ещё не сгенерирован (dev до --init)
    _PUBKEY = None

CHECKIN_INTERVAL_DAYS = 7      # как часто сверяться с сервером (при наличии сети)
GRACE_DAYS = 30               # сколько работать без успешной онлайн-сверки

# ── Legacy: человекочитаемый код формата KZ-XXXXX-… (принимается, но не крипто) ──
PREFIX = "KZ"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32 без I,L,O,U
GROUP = 5
NGROUPS_PAYLOAD = 3
_A = {c: i for i, c in enumerate(ALPHABET)}
_CANON_RE = re.compile(r"^KZ-([0-9A-Z]{5})-([0-9A-Z]{5})-([0-9A-Z]{5})-([0-9A-Z]{5})$")


def find_root(explicit: str | None = None) -> Path:
    """Корень установки — тот же контракт поиска, что и в остальных install-скриптах."""
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "installer" else Path.cwd()


def _marker_path(root: Path) -> Path:
    # Рантайм-маркер активации у клиента (как .state.json — в git/коробку НЕ входит).
    return root / "config" / "activation.json"


def _license_copy_path(root: Path) -> Path:
    return root / "config" / "license.key"


# ── Legacy-формат (нетронуто, для обратной совместимости и тестов) ──────────────
def normalize(raw: str) -> str:
    up = "".join(ch for ch in raw.upper() if ch in ALPHABET or ch == "K" or ch == "Z")
    if up.startswith("KZ"):
        up = up[2:]
    body = "".join(ch for ch in up if ch in ALPHABET)
    if len(body) != GROUP * (NGROUPS_PAYLOAD + 1):
        return raw.strip().upper()
    groups = [body[i:i + GROUP] for i in range(0, len(body), GROUP)]
    return PREFIX + "-" + "-".join(groups)


def _checksum(payload: str) -> str:
    s1 = 0
    s2 = 0
    for ch in payload:
        s1 = (s1 + _A.get(ch, 0) + 1) % 1021
        s2 = (s2 + s1) % 1021
    val = (s2 << 10) | s1
    val = (val * 33 + len(payload)) & 0xFFFFFF
    out = []
    for _ in range(GROUP):
        out.append(ALPHABET[val % 32])
        val //= 32
    return "".join(out)


def check_format(code: str) -> tuple[bool, str]:
    canon = normalize(code)
    m = _CANON_RE.match(canon)
    if not m:
        return False, ("Код не похож на ключ активации. Он выглядит так: "
                       "KZ-XXXXX-XXXXX-XXXXX-XXXXX (буквы A–Z и цифры). Проверьте, "
                       "не пропущены ли символы.")
    g1, g2, g3, csum = m.groups()
    if _checksum(g1 + g2 + g3) != csum:
        return False, ("Код набран, но контрольная часть не сходится — вероятно "
                       "опечатка в одном из символов. Продиктуйте/введите ещё раз.")
    return True, "Формат ключа корректен."


def make_code(seed: str) -> str:
    h = 1469598103934665603
    body = []
    st = seed
    while len(body) < GROUP * NGROUPS_PAYLOAD:
        for ch in st.encode("utf-8"):
            h = ((h ^ ch) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        v = h
        for _ in range(GROUP):
            body.append(ALPHABET[v % 32]); v //= 32
        st = st + "|"
    payload = "".join(body[:GROUP * NGROUPS_PAYLOAD])
    groups = [payload[i:i + GROUP] for i in range(0, len(payload), GROUP)]
    return PREFIX + "-" + "-".join(groups) + "-" + _checksum(payload)


# ── Крипто-лицензия: разбор и офлайн-проверка подписи ───────────────────────────
def _parse_license(text: str) -> dict | None:
    """Разобрать JSON-лицензию; None, если это не лицензия (например, legacy-код)."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(obj, dict) and "payload_b64" in obj and "sig_b64" in obj:
        return obj
    return None


def verify_license(lic: dict) -> tuple[bool, str, dict | None]:
    """Проверить подпись лицензии ПУБЛИЧНЫМ ключом (офлайн). (ok, причина, payload)."""
    if _PUBKEY is None:
        return False, ("В коробке нет публичного ключа лицензий (license_pubkey.py). "
                       "Это сборочная ошибка — обратитесь к поставщику."), None
    try:
        payload_bytes = base64.b64decode(lic["payload_b64"])
        sig = base64.b64decode(lic["sig_b64"])
    except (KeyError, ValueError, TypeError):
        return False, "Файл лицензии повреждён (не читается подпись).", None
    if not ed.verify(_PUBKEY, payload_bytes, sig):
        return False, ("Подпись лицензии недействительна — файл не выпущен нашим "
                       "ключом или повреждён. Запросите лицензию заново."), None
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return False, "Лицензия подписана, но её содержимое нечитаемо.", None
    return True, "Лицензия подписана владельцем — подпись верна.", payload


# ── Локальная запись активации + tamper-evidence ────────────────────────────────
def _integrity(record: dict) -> str:
    """SHA-256 канонизированной записи БЕЗ поля integrity — контроль подмены на глаз."""
    body = {k: v for k, v in record.items() if k != "integrity"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()


def read_marker(root: Path) -> dict:
    p = _marker_path(root)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _persist(root: Path, record: dict) -> Path:
    record["integrity"] = _integrity(record)
    p = _marker_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


def write_license_marker(root: Path, lic: dict, payload: dict) -> Path:
    """Записать активацию по подписанной лицензии + отпечаток текущей машины."""
    fp, weak = mfp.fingerprint()
    record = {
        "schema": 2,
        "status": "local_ok",       # local_ok (офлайн) | verified (онлайн-подтверждён)
        "verified": False,          # true только после успешной онлайн-сверки
        "license": {
            "license_id": payload.get("license_id"),
            "customer": payload.get("customer"),
            "seats": payload.get("seats"),
            "edition": payload.get("edition"),
            "issued_at": payload.get("issued_at"),
            "expires": payload.get("expires"),
        },
        "license_b64": lic["payload_b64"],
        "sig_b64": lic["sig_b64"],
        "fingerprint": fp,
        "fingerprint_weak": weak,
        "seat": 1,                  # мягкий номер места; жёсткий контроль — на сервере
        "ts": int(time.time()),
        "server_status": None,      # None | "active" | "revoked" | "seats_exceeded"
        "last_checkin_ts": None,    # время последней УСПЕШНОЙ онлайн-сверки
    }
    # копия лицензии рядом — удобно поддержке; не секрет (подписанный публичный токен)
    try:
        _license_copy_path(root).parent.mkdir(parents=True, exist_ok=True)
        _license_copy_path(root).write_text(
            json.dumps(lic, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return _persist(root, record)


def write_legacy_marker(root: Path, code: str) -> Path:
    """Legacy: активация по коду формата (без крипто) — verified=False."""
    record = {
        "schema": 1,
        "status": "local_ok",
        "verified": False,
        "code": normalize(code),
        "ts": int(time.time()),
    }
    return _persist(root, record)


# ── Онлайн-слой (дремлет, пока не задан сервер) ─────────────────────────────────
def server_url(root: Path) -> str | None:
    """URL реестра лицензий, если задан. Иначе None → чистый офлайн."""
    env = os.environ.get("KZ_ACTIVATION_SERVER", "").strip()
    if env:
        return env.rstrip("/")
    f = root / "config" / "activation.server"
    if f.is_file():
        try:
            first = f.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return None
        url = first[0].strip() if first else ""
        return url.rstrip("/") or None
    return None


def _http_post_json(url: str, payload: dict, timeout: float = 4.0) -> dict:
    """POST JSON → dict. Ошибки сети — не бросаем, возвращаем {'_error': ...}."""
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        return json.loads(body)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return {"_error": str(e)}


def checkin(root: Path, *, force: bool = False) -> dict:
    """Онлайн-сверка с реестром (если задан сервер). Обновляет маркер. Не бросает.

    force=False → сверяется только если пора (прошёл интервал) или ещё ни разу.
    """
    url = server_url(root)
    if not url:
        return {"skipped": "no_server"}
    m = read_marker(root)
    if not m or m.get("schema", 1) < 2:
        return {"skipped": "not_activated"}
    last = m.get("last_checkin_ts")
    now = int(time.time())
    if (not force) and last and (now - last) < CHECKIN_INTERVAL_DAYS * 86400:
        return {"skipped": "not_due", "last_checkin_ts": last}
    fp, _weak = mfp.fingerprint()
    payload = {
        "license_id": (m.get("license") or {}).get("license_id"),
        "fingerprint": fp,
        "edition": (m.get("license") or {}).get("edition"),
        "license_b64": m.get("license_b64"),
        "sig_b64": m.get("sig_b64"),
    }
    resp = _http_post_json(url + "/checkin", payload)
    if resp.get("_error"):
        return {"ok": False, "network": False, "error": resp["_error"]}
    status = resp.get("status")           # "active" | "revoked" | "seats_exceeded"
    m["server_status"] = status
    if status == "active":
        m["last_checkin_ts"] = now
        m["verified"] = True
        m["status"] = "verified"
    else:
        m["verified"] = False
        m["status"] = "local_ok"
    _persist(root, m)
    return {"ok": status == "active", "network": True, "status": status}


def server_activate(root: Path, payload: dict, lic: dict) -> dict:
    """При активации: зарегистрировать машину в реестре (жёсткий контроль seats/отзыв).

    Возвращает {network, ok, status, reason} либо {skipped:'no_server'} (нет сервера →
    офлайн-активация допустима) либо {network:False} при недоступном сервере.
    """
    url = server_url(root)
    if not url:
        return {"skipped": "no_server"}
    fp, _weak = mfp.fingerprint()
    body = {
        "license_id": payload.get("license_id"),
        "fingerprint": fp,
        "edition": payload.get("edition"),
        "seats": payload.get("seats"),
        "license_b64": lic["payload_b64"],
        "sig_b64": lic["sig_b64"],
    }
    resp = _http_post_json(url + "/activate", body)
    if resp.get("_error"):
        return {"network": False, "error": resp["_error"]}
    return {"network": True, "ok": resp.get("ok", False),
            "status": resp.get("status"), "reason": resp.get("reason")}


# ── Активация (вызывается мастером через --check) ────────────────────────────────
def activate(root: Path, value: str) -> tuple[int, dict]:
    """Умная активация: файл/JSON лицензии → крипто (+сервер, если задан); иначе legacy-код."""
    text = value
    src = Path(value)
    try:
        if src.is_file():
            text = src.read_text(encoding="utf-8-sig")
    except OSError:
        text = value

    lic = _parse_license(text)
    if lic is not None:
        ok, reason, payload = verify_license(lic)
        if not ok:
            return 1, {"ok": False, "status": "rejected", "verified": False,
                       "reason": reason}
        # Онлайн-слой: если задан сервер — жёстко проверяем seats/отзыв ДО записи.
        srv = server_activate(root, payload, lic)
        if srv.get("network") and not srv.get("ok"):
            human = {
                "seats_exceeded": "Достигнут лимит машин по лицензии. Освободите место "
                                  "или обратитесь к поставщику.",
                "revoked": "Лицензия отозвана. Свяжитесь с поставщиком.",
            }.get(srv.get("status"),
                  f"Реестр отклонил активацию: {srv.get('reason') or srv.get('status')}")
            return 1, {"ok": False, "status": srv.get("status") or "rejected",
                       "verified": False, "reason": human}
        write_license_marker(root, lic, payload)
        online_ok = bool(srv.get("network") and srv.get("ok"))
        if online_ok:
            m = read_marker(root)
            m["server_status"] = "active"
            m["verified"] = True
            m["status"] = "verified"
            m["last_checkin_ts"] = int(time.time())
            _persist(root, m)
        status_now = "verified" if online_ok else "local_ok"
        return 0, {"ok": True, "status": status_now, "verified": online_ok,
                   "reason": reason, "license_id": payload.get("license_id"),
                   "customer": payload.get("customer"), "seats": payload.get("seats"),
                   "online": bool(srv.get("network"))}

    # Не лицензия → legacy-код формата.
    ok, reason = check_format(value)
    if not ok:
        return 1, {"ok": False, "status": "rejected", "verified": False, "reason": reason}
    write_legacy_marker(root, value)
    return 0, {"ok": True, "status": "local_ok", "verified": False,
               "reason": reason, "legacy": True}


# ── ГЕЙТ: можно ли заводу работать ──────────────────────────────────────────────
def require_activation(root: Path) -> tuple[bool, str, str]:
    """Проверка активации при старте завода. (ok, статус, сообщение-по-русски).

    Статусы: verified | local_ok | legacy | none | machine_mismatch | bad_signature |
             tampered | no_pubkey | revoked | checkin_overdue.
    Сеть НЕ трогает (быстро): читает уже сохранённый server_status/сроки.
    """
    m = read_marker(root)
    if not m:
        return False, "none", ("Копия не активирована. Введите ключ активации "
                               "(license.key), выданный при покупке.")
    schema = m.get("schema", 1)

    if schema >= 2:
        # 1) целостность записи
        if m.get("integrity") and _integrity(m) != m["integrity"]:
            return False, "tampered", ("Запись активации изменена вручную — "
                                       "повторите активацию файлом license.key.")
        # 2) подпись лицензии
        if _PUBKEY is None:
            return False, "no_pubkey", ("В сборке нет публичного ключа лицензий — "
                                        "сборочная ошибка.")
        try:
            payload_bytes = base64.b64decode(m["license_b64"])
            sig = base64.b64decode(m["sig_b64"])
        except (KeyError, ValueError, TypeError):
            return False, "bad_signature", "Данные лицензии в записи повреждены."
        if not ed.verify(_PUBKEY, payload_bytes, sig):
            return False, "bad_signature", ("Подпись лицензии недействительна — "
                                            "повторите активацию.")
        # 3) привязка к машине
        fp, _weak = mfp.fingerprint()
        if m.get("fingerprint") and fp != m["fingerprint"]:
            return False, "machine_mismatch", ("Активация сделана на другой машине. "
                                               "Активируйте копию на этом компьютере "
                                               "(лицензия допускает несколько машин).")
        # 4) онлайн-слой (только если задан сервер)
        if server_url(root):
            if m.get("server_status") == "revoked":
                return False, "revoked", ("Лицензия отозвана. Свяжитесь с "
                                          "поставщиком для восстановления.")
            # grace: без успешной сверки дольше GRACE_DAYS — в жёстком режиме стоп
            if is_enforced(root):
                ref = m.get("last_checkin_ts") or m.get("ts") or 0
                if (int(time.time()) - ref) > GRACE_DAYS * 86400:
                    return False, "checkin_overdue", (
                        "Нужна онлайн-сверка лицензии (раз в 30 дней). Подключите "
                        "интернет — сверка пройдёт автоматически.")
        status = "verified" if m.get("verified") else "local_ok"
        return True, status, "Копия активирована."

    # Legacy-маркер (код формата) — мягко пускаем, помечаем как legacy.
    if m.get("status") == "local_ok":
        return True, "legacy", "Активировано по коду формата (без криптопривязки)."
    return False, "none", "Копия не активирована."


def is_enforced(root: Path) -> bool:
    """Жёсткий ли гейт.

    ЖЁСТКО, если это КЛИЕНТСКАЯ сборка (штамп config/.client_build, ставит установщик),
    ЛИБО env KZ_ENFORCE_ACTIVATION=1, ЛИБО файл config/activation.enforce.
    Иначе (наш dev-checkout) — МЯГКО, чтобы разработка не ломалась.
    """
    if os.environ.get("KZ_ENFORCE_ACTIVATION", "").strip() in ("1", "true", "yes", "on"):
        return True
    if (root / "config" / "activation.enforce").exists():
        return True
    if (root / "config" / ".client_build").exists():
        return True
    return False


def gate(root: Path) -> bool:
    """Удобная обёртка для завода: True — можно работать.

    Жёсткий режим (клиентская сборка): при отказе возвращает False.
    Мягкий режим (dev): при отказе печатает предупреждение в stderr, но пускает.
    """
    ok, status, msg = require_activation(root)
    if ok:
        return True
    if is_enforced(root):
        print(f"[активация] ОТКАЗ ({status}): {msg}", file=sys.stderr)
        return False
    print(f"[активация] предупреждение ({status}): {msg} "
          f"[мягкий режим — работа не блокируется]", file=sys.stderr)
    return True


def status_text(root: Path) -> str:
    m = read_marker(root)
    lines = ["=== АКТИВАЦИЯ «КОНТЕНТ-ЗАВОДА» ===", ""]
    if not m:
        lines.append("Активация ещё не выполнена.")
        lines.append("Мастер спросит ключ активации первым шагом — передайте файл "
                     "license.key, который вам выдали при покупке.")
        return "\n".join(lines)
    schema = m.get("schema", 1)
    if schema >= 2:
        ok, status, msg = require_activation(root)
        lic = m.get("license", {})
        if ok and status == "verified":
            lines.append("Активировано (подтверждено на сервере лицензий).")
        elif ok:
            lines.append("Активировано локально (подпись лицензии верна, привязано к машине).")
            if server_url(root):
                lines.append("Онлайн-подтверждение появится при следующем выходе в сеть.")
            else:
                lines.append("Работает офлайн — сервер лицензий не задан.")
        else:
            lines.append(f"Активация НЕ действительна: {msg}")
        if lic.get("license_id"):
            lines.append("")
            lines.append(f"Лицензия: {lic.get('license_id')} · клиент: {lic.get('customer')}")
            lines.append(f"Машин по лицензии: {lic.get('seats')} · "
                         f"{'бессрочная' if not lic.get('expires') else 'до ' + str(lic.get('expires'))}")
    else:
        lines.append("Активировано локально по коду формата (устаревший способ, без "
                     "криптопривязки). Рекомендуется активировать файлом license.key.")
    lines.append("")
    lines.append("Ключ хранится скрыто в config/activation.json и в переписке не показывается.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Активация коробки (первый шаг мастера).")
    ap.add_argument("--root", help="Корень установки (по умолчанию — авто-поиск).")
    ap.add_argument("--check", metavar="KEY", help="Активировать: путь к license.key / JSON / legacy-код.")
    ap.add_argument("--status", action="store_true", help="Показать статус активации (по-русски).")
    ap.add_argument("--json", action="store_true", help="Машинная сводка (для мастера).")
    ap.add_argument("--gate", action="store_true", help="Проверить гейт активации (для завода/CI).")
    ap.add_argument("--checkin", action="store_true", help="Онлайн-сверка с реестром (если задан сервер).")
    ap.add_argument("--force", action="store_true", help="С --checkin: сверяться, даже если рано.")
    ap.add_argument("--gen", type=int, metavar="N", help="Legacy: выпустить N кодов формата (тесты).")
    args = ap.parse_args(argv)

    root = find_root(args.root)

    if args.gen is not None:
        if args.gen < 1:
            print("N должно быть ≥ 1."); return 2
        for i in range(args.gen):
            print(make_code(f"seed-{time.time_ns()}-{i}"))
        return 0

    if args.checkin:
        res = checkin(root, force=args.force)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif res.get("skipped") == "no_server":
            print("Сервер лицензий не задан — сверка не нужна (офлайн-режим).")
        elif res.get("network") is False:
            print("Нет связи с сервером лицензий — работаем офлайн (grace до 30 дней).")
        elif res.get("ok"):
            print("Сверка пройдена: лицензия активна.")
        else:
            print(f"Сверка: статус «{res.get('status')}».")
        return 0 if (res.get("ok") or res.get("skipped")) else 1

    if args.gate:
        ok, status, msg = require_activation(root)
        if args.json:
            print(json.dumps({"ok": ok, "status": status, "message": msg,
                              "enforced": is_enforced(root)}, ensure_ascii=False, indent=2))
        else:
            print(("OK: " if ok else "ОТКАЗ: ") + msg + f"  [{status}]")
        return 0 if ok else 1

    if args.check:
        code, info = activate(root, args.check)
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        elif info["ok"]:
            print("Ключ принят." if info["status"] != "verified"
                  else "Ключ подтверждён на сервере.")
        else:
            print(info["reason"])
        return code

    if args.json:
        print(json.dumps(read_marker(root), ensure_ascii=False, indent=2))
        return 0

    print(status_text(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
