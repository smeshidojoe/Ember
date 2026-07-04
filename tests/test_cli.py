from ember.__main__ import _parse_cookies_arg, _parse_cookies_file


def test_parse_cookies_arg():
    assert _parse_cookies_arg("auth_token=AAA; ct0=BBB") == {
        "auth_token": "AAA", "ct0": "BBB"}


def test_parse_cookies_file(tmp_path):
    p = tmp_path / "cookies.txt"
    p.write_text(
        "# Netscape HTTP Cookie File\n"
        ".x.com\tTRUE\t/\tTRUE\t0\tauth_token\tAAA\n"
        ".x.com\tTRUE\t/\tFALSE\t0\tct0\tBBB\n",
        encoding="utf-8")
    assert _parse_cookies_file(str(p)) == {"auth_token": "AAA", "ct0": "BBB"}
