from fastapi.testclient import TestClient

from jarvis.api import Message, create_app

TOKEN = "t0ken"


def make(base="http://127.0.0.1:8765"):
    app = create_app(TOKEN)

    @app.get("/api/health")
    async def health():
        return {"status": "ready"}

    @app.post("/api/message")
    async def message(msg: Message):
        return {"reply": f"echo {msg.text}"}

    @app.post("/api/stop")
    async def stop():
        return {"reply": "stopped"}

    return TestClient(app, base_url=base)


def test_every_route_needs_token():
    c = make()
    assert c.get("/api/health").status_code == 401
    assert c.post("/api/message", json={"text": "hi"}).status_code == 401
    assert c.post("/api/stop").status_code == 401
    assert c.get("/api/health", headers={"X-Jarvis-Token": "wrong"}).status_code == 401


def test_ok_with_token():
    c = make()
    r = c.post("/api/message", json={"text": "hi"}, headers={"X-Jarvis-Token": TOKEN})
    assert r.status_code == 200 and r.json()["reply"] == "echo hi"


def test_dns_rebinding_host_blocked():
    c = make("http://evil.example:8765")
    assert c.get("/api/health", headers={"X-Jarvis-Token": TOKEN}).status_code == 421


def test_foreign_origin_blocked():
    c = make()
    h = {"X-Jarvis-Token": TOKEN, "Origin": "https://evil.example"}
    assert c.post("/api/message", json={"text": "hi"}, headers=h).status_code == 403


def test_message_length_limit():
    c = make()
    r = c.post("/api/message", json={"text": "x" * 8001}, headers={"X-Jarvis-Token": TOKEN})
    assert r.status_code == 422


def test_rate_limit_but_kill_switch_still_works():
    c = make()
    h = {"X-Jarvis-Token": TOKEN}
    codes = [c.get("/api/health", headers=h).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
    assert c.post("/api/stop", headers=h).status_code == 200


def test_no_cors_headers():
    c = make()
    r = c.get("/api/health", headers={"X-Jarvis-Token": TOKEN, "Origin": "http://127.0.0.1:8765"})
    assert "access-control-allow-origin" not in r.headers
