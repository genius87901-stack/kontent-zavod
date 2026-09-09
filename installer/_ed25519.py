# -*- coding: utf-8 -*-
"""
_ed25519 — компактная ЧИСТО-PYTHON реализация Ed25519 (подпись/проверка), stdlib-only.

ЗАЧЕМ. OTP-верификация копий «Контент-завода» (BOX-29, фаза 1) должна ехать в
офлайн-коробке БЕЗ нативных зависимостей: `pynacl`/`cryptography` — бинарные колёса
(libsodium/OpenSSL+cffi), которые (а) утяжеляют и без того проблемный вендоринг
win-колёс и (б) осложняют полную компиляцию Nuitka. Поэтому крипто-примитив — здесь,
на чистом Python поверх `hashlib.sha512`.

ЧЕСТНО ПРО БЕЗОПАСНОСТЬ.
  • ПРОВЕРКА подписи (`verify`) работает ТОЛЬКО с публичными данными (публичный ключ,
    сообщение, подпись) — секретозависимого тайминга нет, поэтому чистый-python
    верификатор криптографически корректен для нашей задачи.
  • ПОДПИСЬ (`sign`) секретозависима по таймингу; здесь она используется ТОЛЬКО в
    `license_gen.py` на машине владельца (не в коробке), где сторонний наблюдатель за
    таймингом невозможен. Это осознанный компромисс, задокументированный в дизайне.

ПРОИСХОЖДЕНИЕ. Математика — эталонная реализация Ed25519 Д. Бернстайна
(https://ed25519.cr.yp.to/python/ed25519.py, public domain / RFC 8032). Ускорено
заменой рекурсивного возведения в степень на встроенный `pow(b,e,m)` и
итеративным double-and-add в `scalarmult` — результат бит-в-бит совместим со
стандартным Ed25519 (в т.ч. с PyNaCl `SigningKey(seed)`), т.к. алгоритм тот же.

ГРАНИЦЫ (CONTRACT.md §3): только stdlib, кроссплатформенно, без секретов в git.

API:
    seed = keygen()                      # 32 случайных байта — приватное семя
    pub  = publickey(seed)               # 32 байта — публичный ключ
    sig  = sign(seed, message_bytes)     # 64 байта
    ok   = verify(pub, message_bytes, sig)   # bool (никогда не бросает)
"""
from __future__ import annotations

import hashlib
import secrets

# --- параметры кривой edwards25519 (RFC 8032) ----------------------------------
_b = 256
_q = 2 ** 255 - 19
_l = 2 ** 252 + 27742317777372353535851937790883648493


def _H(m: bytes) -> bytes:
    return hashlib.sha512(m).digest()


def _inv(x: int) -> int:
    return pow(x, _q - 2, _q)


_d = (-121665 * _inv(121666)) % _q
_I = pow(2, (_q - 1) // 4, _q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = pow(xx, (_q + 3) // 8, _q)
    if (x * x - xx) % _q != 0:
        x = (x * _I) % _q
    if x % 2 != 0:
        x = _q - x
    return x


_By = (4 * _inv(5)) % _q
_Bx = _xrecover(_By)
_B = (_Bx % _q, _By % _q)


def _edwards(P, Q):
    x1, y1 = P
    x2, y2 = Q
    denom = _d * x1 * x2 * y1 * y2
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + denom)
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - denom)
    return (x3 % _q, y3 % _q)


def _scalarmult(P, e: int):
    # Итеративный double-and-add (эквивалент рекурсивного эталона, без предела рекурсии).
    Q = (0, 1)  # нейтральный элемент
    while e > 0:
        if e & 1:
            Q = _edwards(Q, P)
        P = _edwards(P, P)
        e >>= 1
    return Q


def _bit(h: bytes, i: int) -> int:
    return (h[i // 8] >> (i % 8)) & 1


def _encodeint(y: int) -> bytes:
    return y.to_bytes(_b // 8, "little")


def _encodepoint(P) -> bytes:
    x, y = P
    bits = [(y >> i) & 1 for i in range(_b - 1)] + [x & 1]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8)) for i in range(_b // 8))


def _decodeint(s: bytes) -> int:
    return int.from_bytes(s, "little")


def _isoncurve(P) -> bool:
    x, y = P
    return (-x * x + y * y - 1 - _d * x * x * y * y) % _q == 0


def _decodepoint(s: bytes):
    y = int.from_bytes(s, "little") & ((1 << (_b - 1)) - 1)
    x = _xrecover(y)
    if (x & 1) != _bit(s, _b - 1):
        x = _q - x
    P = (x, y)
    if not _isoncurve(P):
        raise ValueError("point is not on curve")
    return P


def _Hint(m: bytes) -> int:
    return int.from_bytes(_H(m), "little")


def _secret_scalar(h: bytes) -> int:
    a = 2 ** (_b - 2)
    for i in range(3, _b - 2):
        a += 2 ** i * _bit(h, i)
    return a


# --- публичный API -------------------------------------------------------------
def keygen() -> bytes:
    """32 криптослучайных байта — приватное семя Ed25519 (хранит владелец)."""
    return secrets.token_bytes(32)


def publickey(seed: bytes) -> bytes:
    """Публичный ключ (32 байта) из приватного семени (32 байта)."""
    if len(seed) != 32:
        raise ValueError("seed должен быть 32 байта")
    h = _H(seed)
    a = _secret_scalar(h)
    A = _scalarmult(_B, a)
    return _encodepoint(A)


def sign(seed: bytes, message: bytes) -> bytes:
    """Подпись (64 байта). ТОЛЬКО для license_gen.py на машине владельца."""
    if len(seed) != 32:
        raise ValueError("seed должен быть 32 байта")
    h = _H(seed)
    a = _secret_scalar(h)
    pk = _encodepoint(_scalarmult(_B, a))
    r = _Hint(h[_b // 8:_b // 4] + message)
    R = _scalarmult(_B, r)
    S = (r + _Hint(_encodepoint(R) + pk + message) * a) % _l
    return _encodepoint(R) + _encodeint(S)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Проверка подписи. Никогда не бросает — возвращает False при любой ошибке."""
    try:
        if len(signature) != 64 or len(public_key) != 32:
            return False
        R = _decodepoint(signature[:32])
        A = _decodepoint(public_key)
        S = _decodeint(signature[32:])
        if S >= _l:
            return False
        h = _Hint(_encodepoint(R) + public_key + message)
        return _scalarmult(_B, S) == _edwards(R, _scalarmult(A, h))
    except (ValueError, IndexError):
        return False


if __name__ == "__main__":
    # Самопроверка: round-trip + отказ на битой подписи.
    sd = keygen()
    pk = publickey(sd)
    msg = b"kontent-zavod self-test"
    sg = sign(sd, msg)
    assert verify(pk, msg, sg), "round-trip не прошёл"
    assert not verify(pk, msg + b"x", sg), "битое сообщение прошло"
    assert not verify(pk, msg, bytes(64)), "нулевая подпись прошла"
    print("ed25519 self-test OK")
