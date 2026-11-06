"""The installer's model warm-up: list FilmLab's default WanGP models and download the missing ones."""

import asyncio

import httpx

from app import warmup
from app.db import Database
from app.wangp.client import WanGPClient
from tests.fakes import FakeWanGP


def _run(fw, tmp_path, yes, every=False, monkeypatch=None):
    out: list[str] = []
    monkeypatch.setattr(warmup, "POLL_S", 0.01)

    async def go():
        client = WanGPClient("http://wangp.test/mcp", http=httpx.AsyncClient(transport=fw.transport(), base_url="http://wangp.test"))
        return await warmup.warmup(yes, every, say=out.append, client=client, db=Database(tmp_path / "w.sqlite"))
    return asyncio.run(go()), out


def test_lists_then_downloads_only_missing_models(tmp_path, video, image, monkeypatch):
    fw = FakeWanGP(video, "normal", image)
    code, out = _run(fw, tmp_path, yes=False, monkeypatch=monkeypatch)
    text = "\n".join(out)
    assert code == 0 and "video shots without references: MiniMax H3 FL2VA Pruned PDD 8 Steps ... NOT DOWNLOADED" in text
    assert "character voices (Creator): Qwen3 1.7B ... installed" in text and "run with --yes" in text
    assert not [c for c in fw.calls if c[0] == "wangp_generate"]  # listing downloads nothing

    code, out = _run(fw, tmp_path, yes=True, monkeypatch=monkeypatch)
    gens = [a for n, a in fw.calls if n == "wangp_generate"]
    assert code == 0 and len(gens) == 1 and gens[0]["settings"]["video_length"] == 17, out
    assert gens[0]["settings"]["model_type"] == "minimax_h3_fl2va_pruned_pdd" and "Warm-up finished." in out[-1]


def test_every_model_once_with_references(tmp_path, video, image, monkeypatch):
    fw = FakeWanGP(video, "normal", image)
    code, out = _run(fw, tmp_path, yes=True, every=True, monkeypatch=monkeypatch)
    gens = [a["settings"] for n, a in fw.calls if n == "wangp_generate"]
    assert code == 0 and len(gens) == 6, out
    refs = [g for g in gens if g.get("image_refs")]
    assert {g["model_type"] for g in refs} == {"minimax_h3_ref2va_pruned_pdd", "flux2_klein_9b"}
    assert {g["model_type"] for g in gens} >= {"qwen3_tts_voicedesign", "ace_step_v1_5_turbo_lm_1_7b"}


def test_says_plainly_when_wangp_is_not_running(tmp_path, monkeypatch):
    out: list[str] = []

    async def go():
        dead = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
        return await warmup.warmup(False, False, say=out.append, client=WanGPClient("http://wangp.test/mcp", http=dead),
                                   db=Database(tmp_path / "w.sqlite"))
    assert asyncio.run(go()) == 2 and "doesn't answer" in out[0]
