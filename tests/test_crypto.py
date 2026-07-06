"""Проверка встроенной (чистый Python) реализации AES-GCM по эталонным
векторам — чтобы фолбек-крипто работал без сторонних библиотек."""

from ember._browser_cookies import (_aes_cbc_decrypt_pure, _aes_gcm_decrypt_pure,
                                     _encrypt_block, _key_expansion,
                                     aes_cbc_decrypt, aes_gcm_decrypt)


def test_aes128_block_fips197():
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    w, nr = _key_expansion(key)
    assert _encrypt_block(pt, w, nr).hex() == "69c4e0d86a7b0430d8cdb78070b4c55a"


def test_aes256_gcm_empty_vector():
    # NIST: key=0*32, iv=0*12, plaintext empty
    tag = bytes.fromhex("530f8afbc74536b9a963b4f1c4cb738b")
    assert _aes_gcm_decrypt_pure(bytes(32), bytes(12), b"", tag) == b""


def test_aes256_gcm_block_vector():
    # NIST: key=0*32, iv=0*12, plaintext = 16 zero bytes
    ct = bytes.fromhex("cea7403d4d606b6e074ec5d3baf39d18")
    tag = bytes.fromhex("d0d1c8a799996bf0265b98b5d48ab919")
    assert _aes_gcm_decrypt_pure(bytes(32), bytes(12), ct, tag) == bytes(16)


def test_aes256_gcm_tag_mismatch_raises():
    try:
        _aes_gcm_decrypt_pure(bytes(32), bytes(12), b"", bytes(16))
    except ValueError:
        return
    raise AssertionError("expected ValueError on bad tag")


def test_public_backend_matches_pure():
    # публичная функция (cryptography/pycryptodome/pure) даёт тот же результат
    ct = bytes.fromhex("cea7403d4d606b6e074ec5d3baf39d18")
    tag = bytes.fromhex("d0d1c8a799996bf0265b98b5d48ab919")
    assert aes_gcm_decrypt(bytes(32), bytes(12), ct, tag) == bytes(16)


def test_aes128_cbc_nist_vector():
    # NIST SP800-38A F.2.2 AES-128-CBC
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    ct = bytes.fromhex("7649abac8119b246cee98e9b12e9197d")
    pt = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
    assert _aes_cbc_decrypt_pure(key, iv, ct) == pt
    assert aes_cbc_decrypt(key, iv, ct) == pt
