import time

from ember import cache


def test_set_get_roundtrip():
    cache.set("ember_test_key", {"a": 1}, ttl=60)
    assert cache.get("ember_test_key") == {"a": 1}
    cache.invalidate("ember_test_key")
    assert cache.get("ember_test_key") is None


def test_ttl_expiry():
    cache.set("ember_test_ttl", "v", ttl=-1)   # уже истёк
    assert cache.get("ember_test_ttl") is None


def test_get_or_set_calls_producer_once():
    cache.invalidate("ember_test_once")
    calls = []

    def producer():
        calls.append(1)
        return "value"

    assert cache.get_or_set("ember_test_once", 60, producer) == "value"
    assert cache.get_or_set("ember_test_once", 60, producer) == "value"
    assert len(calls) == 1
    cache.invalidate("ember_test_once")
