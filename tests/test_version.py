"""Version handling: the /api/version endpoint and the semver comparison
behind the GitHub update banner."""
from __future__ import annotations

import asyncio
import math

from fastapi.testclient import TestClient

import app as appmod


def test_ver_tuple_parses_only_full_semver():
    assert appmod._ver_tuple("1.0.0") == (1, 0, 0)
    assert appmod._ver_tuple("v1.2.3") == (1, 2, 3)
    assert appmod._ver_tuple("v1.2.3-rc1") == (1, 2, 3)
    # non-semver tags (v2, v1.2, "release") are skipped, not compared
    assert appmod._ver_tuple("v2") == ()
    assert appmod._ver_tuple("release-1") == ()


def test_ver_tuple_ordering():
    assert appmod._ver_tuple("v1.10.0") > appmod._ver_tuple("v1.9.8")
    assert appmod._ver_tuple("v2.0.0") > appmod._ver_tuple("v1.99.99")


def test_version_endpoint_without_repo_skips_check(monkeypatch):
    monkeypatch.setattr(appmod, "HQALL_REPO", "")
    j = asyncio.run(appmod.version_endpoint())
    assert j["ok"] is True
    assert j["version"] == appmod.VERSION
    assert j["latest"] is None
    assert j["update_available"] is False
    assert j["reason"] == "HQALL_REPO not set"


def test_version_endpoint_reports_future_version_as_update(monkeypatch):
    monkeypatch.setattr(appmod, "HQALL_REPO", "someone/hqall")
    # cheat: an installed version from the days of yore is always behind
    monkeypatch.setattr(appmod, "VERSION", "0.1.0")
    monkeypatch.setattr(appmod, "cache", appmod.cache.__class__())
    monkeypatch.setattr(appmod, "client", _FakeClient())

    j = asyncio.run(appmod.version_endpoint())
    assert j["update_available"] is True
    assert j["latest"] == "v2.0.0"
    assert j["release_url"] == "https://github.com/someone/hqall/releases/latest"


def test_ttl_cache_discards_expired_entries():
    cache = appmod.cache.__class__()
    cache.put("stale", {"value": 1}, ttl=0.01)
    assert cache.get("stale") == {"value": 1}
    # once the entry has expired, it should be purged on access and not remain
    # in the cache dictionary.
    cached = cache.get("stale")
    assert cached == {"value": 1}
    # make it definitely stale so the next access triggers eviction
    cache._d["stale"] = (0.0, {"value": 2})
    assert cache.get("stale") is None
    assert "stale" not in cache._d


def test_set_origin_sanitizes_nonfinite_coordinates():
    before = dict(appmod.STATE["origin"])
    appmod.STATE["origin"] = {"lat": 62.6, "lon": 25.3, "label": "Central Finland"}

    asyncio.run(appmod.set_origin({"lat": float("nan"), "lon": float("inf"), "label": "bad"}))

    origin = appmod.STATE["origin"]
    assert math.isfinite(origin["lat"])
    assert math.isfinite(origin["lon"])
    assert origin["lat"] == 62.6
    assert origin["lon"] == 25.3
    assert origin["label"] == "bad"
    appmod.STATE["origin"] = before


def test_app_uses_lifespan_context_for_startup_and_shutdown():
    assert appmod.app.router.lifespan is not None


def test_health_and_ready_endpoints_report_runtime_status():
    tc = TestClient(appmod.app)
    health = tc.get("/api/health")
    ready = tc.get("/api/ready")
    assert health.status_code == 200
    assert ready.status_code == 200
    assert health.json()["ok"] is True
    assert ready.json()["ok"] is True
    assert "uptime_s" in health.json()
    assert "uptime_s" in ready.json()


class _FakeResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"tag_name": "v2.0.0"}


class _FakeClient:
    async def get(self, url, headers=None, timeout=None):
        return _FakeResp()


def test_index_serves_html_and_bookmark_ui():
    tc = TestClient(appmod.app)
    res = tc.get("/")
    assert res.status_code == 200
    assert "hqall" in res.text
    assert "bookmark-btn" in res.text
    assert "bookmark-modal" in res.text