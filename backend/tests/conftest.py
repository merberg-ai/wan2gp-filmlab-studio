import asyncio
import dataclasses
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import renders
from app.config import Settings
from app.director.gateway import GatewayClient
from app.main import Studio, create_app
from app.wangp.client import WanGPClient

from .fakes import FakeGateway, FakeVision, FakeWanGP, make_test_image, make_test_video

PASSWORD = "correct horse battery"


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    return make_test_video(tmp_path_factory.mktemp("vid") / "clip.mp4")


@pytest.fixture(scope="session")
def image(tmp_path_factory):
    """A small PNG: what WanGP 'generates' and what tests upload as references."""
    return make_test_image(tmp_path_factory.mktemp("img") / "ref.png")


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(renders, "POLL_WAIT_S", 0.01)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", media_dir=tmp_path / "media", static_dir=None, backup_dir=tmp_path / "backups",
                    gateway_token="test-token", wangp_url="http://wangp.test/mcp",
                    gateway_url="http://gateway.test", openclaw_config=tmp_path / "openclaw.json",
                    agent_workspace=tmp_path / "agent-workspace")


def fake_gpu():
    return {"gpu": "Test GPU", "load": 37.0, "vram_used_gb": 6.5, "vram_total_gb": 16.0, "temp_c": 51.0, "power_w": 120.5}


def build(settings, fake_wangp, fake_gateway, fake_vision=None):
    wangp = WanGPClient(settings.wangp_url, http=httpx.AsyncClient(transport=fake_wangp.transport()))
    gateway = GatewayClient(settings.gateway_url, settings.gateway_token, "director",
                            http=httpx.AsyncClient(transport=fake_gateway.transport()))
    studio = Studio(settings, wangp=wangp, gateway=gateway,
                    vision_transport=(fake_vision or FakeVision()).transport(), gpu_query=fake_gpu)
    return create_app(settings, studio=studio), studio


class Harness:
    def __init__(self, client: TestClient, studio: Studio, wangp: FakeWanGP, gateway: FakeGateway):
        self.c, self.studio, self.wangp, self.gateway = client, studio, wangp, gateway
        self.csrf = None
        self.closed = False

    def close(self):
        """Stop this backend instance (simulates a backend restart)."""
        if not self.closed:
            self.closed = True
            self.c.__exit__(None, None, None)

    def login(self):
        self.c.portal.call(_set_owner, self.studio)
        r = self.c.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]
        return self

    def post(self, url, json=None):
        return self.c.post(url, json=json, headers={"x-csrf-token": self.csrf})

    def wait_for(self, predicate, timeout=10.0, what="condition"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {what}")

    def project(self, pid):
        return self.c.get(f"/api/projects/{pid}").json()


async def _set_owner(studio):
    from app.auth import set_owner
    await set_owner(studio.db, "owner", PASSWORD)
    await asyncio.wait_for(studio.wangp_ready.wait(), 5)


@pytest.fixture
def make_harness(settings, video, image):
    opened = []

    def factory(scenario="normal", client_addr="127.0.0.1", **overrides):
        fw, fg, fv = FakeWanGP(video, scenario, image), FakeGateway(), FakeVision()
        app, studio = build(dataclasses.replace(settings, **overrides), fw, fg, fv)
        client = TestClient(app, client=(client_addr, 50000))
        client.__enter__()
        harness = Harness(client, studio, fw, fg)
        harness.vision = fv
        opened.append(harness)
        return harness

    yield factory
    for h in opened:
        h.close()
