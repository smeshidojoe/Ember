"""Built-in browser cookie reader — no required dependencies.

Covers what is actually readable:
  - Firefox (any OS): cookies.sqlite — unencrypted, stdlib only;
  - Chromium family on Windows (Vivaldi, Opera, non-ABE Chrome/Edge/Brave):
    key from Local State via DPAPI, values are AES-256-GCM;
  - Chromium on macOS/Linux: key from Keychain/keyring, values AES-128-CBC.

App-Bound Encryption (modern Chrome/Edge/Brave, cookies prefixed v20) is
supported by nobody, including yt-dlp — we raise a clear error for it.

AES is taken from cryptography or pycryptodome if installed; otherwise a
built-in pure-Python implementation (fast enough for tiny cookie values).
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from typing import List, Optional

from .errors import EmberError


class NativeUnsupported(Exception):
    """The browser+OS combo is not covered by our reader — need a fallback."""


# ---------------------------------------------------------------------------
# AES-256-GCM: cryptography -> pycryptodome -> чистый Python
# ---------------------------------------------------------------------------

def aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(nonce, ciphertext + tag, None)
    except ImportError:
        pass
    try:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ciphertext, tag)
    except ImportError:
        pass
    try:
        from Cryptodome.Cipher import AES  # type: ignore
        return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ciphertext, tag)
    except ImportError:
        pass
    return _aes_gcm_decrypt_pure(key, nonce, ciphertext, tag)


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-CBC (macOS/Linux Chromium cookies, encrypted HLS). Backends:
    cryptography -> pycryptodome -> pure Python."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return d.update(data) + d.finalize()
    except ImportError:
        pass
    try:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except ImportError:
        pass
    try:
        from Cryptodome.Cipher import AES  # type: ignore
        return AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except ImportError:
        pass
    return _aes_cbc_decrypt_pure(key, iv, data)


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    n = data[-1]
    return data[:-n] if 1 <= n <= 16 else data


# --- чистый Python AES (FIPS-197) ---

def _gen_sbox():
    p = q = 1
    sbox = [0] * 256
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        p &= 0xFF
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        q &= 0xFF
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) \
            ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_SBOX = _gen_sbox()


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def _mul(a: int, b: int) -> int:
    res = 0
    for _ in range(8):
        if b & 1:
            res ^= a
        b >>= 1
        a = _xtime(a)
    return res & 0xFF


def _key_expansion(key: bytes):
    nk = len(key) // 4
    nr = {4: 10, 6: 12, 8: 14}[nk]
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


def _encrypt_block(block: bytes, w, nr: int) -> bytes:
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]

    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= w[rnd * 4 + c][r]

    add_round_key(0)
    for rnd in range(1, nr + 1):
        for r in range(4):
            for c in range(4):
                s[r][c] = _SBOX[s[r][c]]
        for r in range(1, 4):
            s[r] = s[r][r:] + s[r][:r]
        if rnd != nr:
            for c in range(4):
                col = [s[r][c] for r in range(4)]
                s[0][c] = _mul(col[0], 2) ^ _mul(col[1], 3) ^ col[2] ^ col[3]
                s[1][c] = col[0] ^ _mul(col[1], 2) ^ _mul(col[2], 3) ^ col[3]
                s[2][c] = col[0] ^ col[1] ^ _mul(col[2], 2) ^ _mul(col[3], 3)
                s[3][c] = _mul(col[0], 3) ^ col[1] ^ col[2] ^ _mul(col[3], 2)
        add_round_key(rnd)
    return bytes(s[r][c] for c in range(4) for r in range(4))


_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i


def _decrypt_block(block: bytes, w, nr: int) -> bytes:
    """Inverse AES cipher (for CBC)."""
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]

    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= w[rnd * 4 + c][r]

    add_round_key(nr)
    for rnd in range(nr - 1, -1, -1):
        for r in range(1, 4):                        # InvShiftRows: сдвиг вправо на r
            s[r] = s[r][-r:] + s[r][:-r]
        for r in range(4):                            # InvSubBytes
            for c in range(4):
                s[r][c] = _INV_SBOX[s[r][c]]
        add_round_key(rnd)
        if rnd != 0:                                  # InvMixColumns
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = _mul(a[0], 14) ^ _mul(a[1], 11) ^ _mul(a[2], 13) ^ _mul(a[3], 9)
                s[1][c] = _mul(a[0], 9) ^ _mul(a[1], 14) ^ _mul(a[2], 11) ^ _mul(a[3], 13)
                s[2][c] = _mul(a[0], 13) ^ _mul(a[1], 9) ^ _mul(a[2], 14) ^ _mul(a[3], 11)
                s[3][c] = _mul(a[0], 11) ^ _mul(a[1], 13) ^ _mul(a[2], 9) ^ _mul(a[3], 14)
    return bytes(s[r][c] for c in range(4) for r in range(4))


def _aes_cbc_decrypt_pure(key: bytes, iv: bytes, data: bytes) -> bytes:
    w, nr = _key_expansion(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        dec = _decrypt_block(block, w, nr)
        out += bytes(a ^ b for a, b in zip(dec, prev))
        prev = block
    return bytes(out)


def _gf_mult(x: int, y: int) -> int:
    r = 0xE1 << 120
    z = 0
    for i in range(128):
        if y & (1 << (127 - i)):
            z ^= x
        if x & 1:
            x = (x >> 1) ^ r
        else:
            x >>= 1
    return z


def _ghash(h: int, data: bytes) -> int:
    y = 0
    for i in range(0, len(data), 16):
        y ^= int.from_bytes(data[i:i + 16], "big")
        y = _gf_mult(y, h)
    return y


def _inc32(block: bytes) -> bytes:
    ctr = (int.from_bytes(block[12:], "big") + 1) & 0xFFFFFFFF
    return block[:12] + ctr.to_bytes(4, "big")


def _gctr(w, nr, icb: bytes, data: bytes) -> bytes:
    out = bytearray()
    cb = icb
    for i in range(0, len(data), 16):
        ks = _encrypt_block(cb, w, nr)
        chunk = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(chunk, ks))
        cb = _inc32(cb)
    return bytes(out)


def _aes_gcm_decrypt_pure(key: bytes, nonce: bytes, ct: bytes, tag: bytes) -> bytes:
    w, nr = _key_expansion(key)
    h = int.from_bytes(_encrypt_block(b"\x00" * 16, w, nr), "big")
    j0 = nonce + b"\x00\x00\x00\x01"          # 12-байтовый nonce
    plaintext = _gctr(w, nr, _inc32(j0), ct)
    pad = b"\x00" * ((-len(ct)) % 16)
    lenblock = (0).to_bytes(8, "big") + (len(ct) * 8).to_bytes(8, "big")
    s = _ghash(h, ct + pad + lenblock)
    computed = _gctr(w, nr, j0, s.to_bytes(16, "big"))
    if computed != tag:
        raise ValueError("GCM tag mismatch")
    return plaintext


# ---------------------------------------------------------------------------
# Windows DPAPI
# ---------------------------------------------------------------------------

def _dpapi_decrypt(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise EmberError("DPAPI could not decrypt the browser key")
    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return result


# ---------------------------------------------------------------------------
# чтение баз cookies
# ---------------------------------------------------------------------------

def _query(db_path: str, sql: str):
    """Read the DB via a temp copy (works around a lock held by the browser)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    try:
        shutil.copyfile(db_path, tmp.name)
        con = sqlite3.connect(f"file:{tmp.name}?immutable=1", uri=True)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def _matches(host: str, domains: List[str]) -> bool:
    return any(d in (host or "") for d in domains)


# --- Firefox ---

def _firefox_root() -> Optional[str]:
    if sys.platform == "win32":
        base = os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/Firefox")
    else:
        base = os.path.expanduser("~/.mozilla/firefox")
    return base if os.path.isdir(base) else None


def _firefox_cookies_db(profile: Optional[str]) -> Optional[str]:
    root = _firefox_root()
    if not root:
        return None
    if profile:
        for cand in (os.path.join(root, "Profiles", profile, "cookies.sqlite"),
                     os.path.join(root, profile, "cookies.sqlite")):
            if os.path.isfile(cand):
                return cand
    dbs = (glob.glob(os.path.join(root, "Profiles", "*", "cookies.sqlite"))
           + glob.glob(os.path.join(root, "*", "cookies.sqlite")))
    if not dbs:
        return None
    # предпочитаем default-release / default
    dbs.sort(key=lambda p: ("default-release" not in p, "default" not in p))
    return dbs[0]


def read_firefox(profile: Optional[str], domains: List[str]) -> dict:
    db = _firefox_cookies_db(profile)
    if not db:
        raise NativeUnsupported("firefox cookies db not found")
    rows = _query(db, "SELECT host, name, value FROM moz_cookies")
    return {name: value for host, name, value in rows if _matches(host, domains)}


# --- Chromium на Windows ---

_CHROMIUM_LOCAL = {
    "chrome": ["Google", "Chrome", "User Data"],
    "chromium": ["Chromium", "User Data"],
    "edge": ["Microsoft", "Edge", "User Data"],
    "brave": ["BraveSoftware", "Brave-Browser", "User Data"],
    "vivaldi": ["Vivaldi", "User Data"],
    "whale": ["Naver", "Naver Whale", "User Data"],
}


def _chromium_dirs(browser: str, profile: Optional[str]):
    """Return (local_state_path, cookies_db_path) for a browser on Windows."""
    if browser == "opera":
        base = os.path.join(os.environ.get("APPDATA", ""), "Opera Software", "Opera Stable")
        prof_dir = base
    else:
        parts = _CHROMIUM_LOCAL[browser]
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), *parts)
        prof_dir = os.path.join(base, profile or "Default")
    local_state = os.path.join(base, "Local State")
    for cookies in (os.path.join(prof_dir, "Network", "Cookies"),
                    os.path.join(prof_dir, "Cookies")):
        if os.path.isfile(cookies):
            return local_state, cookies
    return local_state, None


def _chromium_key(local_state: str) -> bytes:
    with open(local_state, encoding="utf-8") as f:
        state = json.load(f)
    enc = state.get("os_crypt", {}).get("encrypted_key")
    if not enc:
        raise EmberError("no os_crypt key in Local State")
    import base64
    blob = base64.b64decode(enc)
    if blob[:5] != b"DPAPI":
        raise EmberError("unexpected Local State key format")
    return _dpapi_decrypt(blob[5:])


def read_chromium_windows(browser: str, profile: Optional[str], domains: List[str]) -> dict:
    if sys.platform != "win32":
        raise NativeUnsupported("chromium native reader is Windows-only")
    local_state, cookies_db = _chromium_dirs(browser, profile)
    if not cookies_db or not os.path.isfile(local_state):
        raise NativeUnsupported(f"{browser} data not found")

    key = _chromium_key(local_state)
    # encrypted_value — бинарный BLOB; берём через hex(), чтобы sqlite3
    # не пытался декодировать его как UTF-8
    rows = _query(cookies_db,
                  "SELECT host_key, name, value, hex(encrypted_value) FROM cookies")
    out = {}
    abe_hit = False
    for host, name, plain, enc_hex in rows:
        if not _matches(host, domains):
            continue
        enc = bytes.fromhex(enc_hex) if enc_hex else b""
        if enc and enc[:3] in (b"v10", b"v11"):
            nonce, ct, tag = enc[3:15], enc[15:-16], enc[-16:]
            try:
                raw = aes_gcm_decrypt(key, nonce, ct, tag)
            except Exception:
                continue
            # новые Chromium добавляют спереди 32-байтовый SHA-256 хэш домена
            if raw[:32] == hashlib.sha256((host or "").encode()).digest():
                raw = raw[32:]
            out[name] = raw.decode("utf-8", "replace")
        elif enc and enc[:3] == b"v20":
            abe_hit = True            # App-Bound Encryption — не расшифровать
        elif plain:
            out[name] = plain
    if not out and abe_hit:
        raise EmberError(
            f"{browser} uses App-Bound Encryption (Chrome 127+); its cookies "
            "cannot be read (even yt-dlp can't). Use Firefox, a non-ABE browser "
            "(e.g. Vivaldi), --cookies-file, or manual --cookies")
    return out


# --- Chromium на macOS / Linux (ключ из keychain/keyring + AES-CBC) ---

def _strip_domain_hash(raw: bytes, host: str) -> bytes:
    if raw[:32] == hashlib.sha256((host or "").encode()).digest():
        return raw[32:]
    return raw


def _profile_dir(browser: str, base: str, profile: Optional[str]) -> str:
    return base if browser == "opera" else os.path.join(base, profile or "Default")


def _cookies_db_in(prof_dir: str) -> Optional[str]:
    for c in (os.path.join(prof_dir, "Network", "Cookies"),
              os.path.join(prof_dir, "Cookies")):
        if os.path.isfile(c):
            return c
    return None


def _read_cbc_cookies(db: str, domains: List[str], keys: dict) -> dict:
    """Shared mac/linux reader: v10/v11 values via AES-128-CBC (IV=16 spaces)."""
    iv = b" " * 16
    rows = _query(db, "SELECT host_key, name, value, hex(encrypted_value) FROM cookies")
    out = {}
    for host, name, plain, enc_hex in rows:
        if not _matches(host, domains):
            continue
        enc = bytes.fromhex(enc_hex) if enc_hex else b""
        key = keys.get(enc[:3])
        if enc[:3] in (b"v10", b"v11") and key:
            try:
                raw = _pkcs7_unpad(aes_cbc_decrypt(key, iv, enc[3:]))
            except Exception:
                continue
            out[name] = _strip_domain_hash(raw, host).decode("utf-8", "replace")
        elif not enc and plain:
            out[name] = plain
    return out


_CHROMIUM_MAC = {
    "chrome": "Google/Chrome", "chromium": "Chromium", "edge": "Microsoft Edge",
    "brave": "BraveSoftware/Brave-Browser", "vivaldi": "Vivaldi",
    "opera": "com.operasoftware.Opera", "whale": "Naver/Whale",
}
_MAC_KEYCHAIN = {
    "chrome": ("Chrome Safe Storage", "Chrome"),
    "chromium": ("Chromium Safe Storage", "Chromium"),
    "edge": ("Microsoft Edge Safe Storage", "Microsoft Edge"),
    "brave": ("Brave Safe Storage", "Brave"),
    "vivaldi": ("Vivaldi Safe Storage", "Vivaldi"),
    "opera": ("Opera Safe Storage", "Opera"),
    "whale": ("Whale Safe Storage", "Whale"),
}


def _mac_chromium_key(browser: str) -> Optional[bytes]:
    import subprocess
    label, account = _MAC_KEYCHAIN.get(browser, (None, None))
    if not label:
        return None
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-w", "-a", account, "-s", label],
            capture_output=True, timeout=15)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    password = r.stdout.rstrip(b"\n")
    return hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)


def read_chromium_macos(browser: str, profile: Optional[str], domains: List[str]) -> dict:
    base = _CHROMIUM_MAC.get(browser)
    if not base:
        raise NativeUnsupported(f"no macOS path for {browser}")
    root = os.path.expanduser("~/Library/Application Support")
    db = _cookies_db_in(_profile_dir(browser, os.path.join(root, base), profile))
    if not db:
        raise NativeUnsupported(f"{browser} data not found")
    key = _mac_chromium_key(browser)
    if not key:
        raise EmberError(
            f"could not read the {browser} key from the macOS Keychain "
            "(you may need to allow access)")
    return _read_cbc_cookies(db, domains, {b"v10": key, b"v11": key})


_CHROMIUM_LINUX = {
    "chrome": "google-chrome", "chromium": "chromium", "edge": "microsoft-edge",
    "brave": "BraveSoftware/Brave-Browser", "vivaldi": "vivaldi", "opera": "opera",
}
_LINUX_APP = {
    "chrome": "Chrome", "chromium": "Chromium", "edge": "Microsoft Edge",
    "brave": "Brave", "vivaldi": "Vivaldi", "opera": "Opera",
}


def _linux_keyring_password(browser: str) -> Optional[bytes]:
    try:
        import secretstorage
    except ImportError:
        return None
    label = f"{_LINUX_APP.get(browser, '')} Safe Storage"
    try:
        conn = secretstorage.dbus_init()
        try:
            collection = secretstorage.get_default_collection(conn)
            for item in collection.get_all_items():
                if item.get_label() == label:
                    return item.get_secret()
        finally:
            conn.close()
    except Exception:
        return None
    return None


def read_chromium_linux(browser: str, profile: Optional[str], domains: List[str]) -> dict:
    base = _CHROMIUM_LINUX.get(browser)
    if not base:
        raise NativeUnsupported(f"no Linux path for {browser}")
    root = os.path.expanduser("~/.config")
    db = _cookies_db_in(_profile_dir(browser, os.path.join(root, base), profile))
    if not db:
        raise NativeUnsupported(f"{browser} data not found")
    # v10 — фиксированный пароль "peanuts"; v11 — пароль из системного keyring
    key_v10 = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)
    keyring_pw = _linux_keyring_password(browser)
    key_v11 = (hashlib.pbkdf2_hmac("sha1", keyring_pw, b"saltysalt", 1, 16)
               if keyring_pw else None)
    return _read_cbc_cookies(db, domains, {b"v10": key_v10, b"v11": key_v11})


# ---------------------------------------------------------------------------
# точка входа
# ---------------------------------------------------------------------------

_CHROMIUM = set(_CHROMIUM_LOCAL) | {"opera"}


def native_cookies(browser: str, profile: Optional[str], domains: List[str]) -> dict:
    """Read cookies with the built-in reader. NativeUnsupported if the
    browser+OS combo is beyond us (the caller then uses a fallback)."""
    if browser == "firefox":
        return read_firefox(profile, domains)
    if browser in _CHROMIUM:
        if sys.platform == "win32":
            return read_chromium_windows(browser, profile, domains)
        if sys.platform == "darwin":
            return read_chromium_macos(browser, profile, domains)
        if sys.platform.startswith("linux"):
            return read_chromium_linux(browser, profile, domains)
    raise NativeUnsupported(f"no native reader for {browser} on {sys.platform}")
