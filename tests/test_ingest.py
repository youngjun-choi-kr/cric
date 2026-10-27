"""CelesTrak fetching: retries, the cached fallback, and the clear failure the live workflow skips on."""

import pytest
import requests

import cric.ingest.celestrak as ck


class _R:
    def __init__(self, code, text="1 x\n2 x\n"):
        self.status_code, self.text = code, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(ck.time, "sleep", lambda s: None)


def test_retries_through_timeouts_and_503(monkeypatch, tmp_path, no_sleep):
    answers = iter([requests.ConnectTimeout("t"), _R(503), _R(200, "fresh\n")])

    def get(url, timeout):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(ck.requests, "get", get)
    monkeypatch.setattr(ck, "DATA", tmp_path)
    assert ck.fetch_group("active").read_text() == "fresh\n"


def test_unreachable_uses_cache_or_raises(monkeypatch, tmp_path, no_sleep):
    def get(url, timeout):
        raise requests.ConnectTimeout("t")
    monkeypatch.setattr(ck.requests, "get", get)
    monkeypatch.setattr(ck, "DATA", tmp_path)
    with pytest.raises(ck.Unreachable):
        ck.fetch_group("active")
    cached = tmp_path / "celestrak_active.tle"
    cached.write_text("old\n")
    import os
    os.utime(cached, (0, 0))                      # stale, so it tries the network first
    assert ck.fetch_group("active").read_text() == "old\n"
