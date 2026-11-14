"""M5: escalation ladder, quiet hours, rate limit, merging, redaction, remote commands, Telegram polling."""
import asyncio
import datetime
import time

import pytest

from jarvis import config, daemon, secrets
from jarvis.notify.notifier import ESCALATE_AFTER, Notifier


def at(hour, minute=0):
    return datetime.datetime(2026, 11, 16, hour, minute).timestamp()


class Ladder:
    def __init__(self, cfg, now, present=True):
        self.now, self.is_present, self.mac, self.phone = now, present, [], []
        self.n = Notifier(cfg, mac=self.mac.append, present=lambda: self.is_present,
                          remote=lambda text, buttons: self.phone.append((text, buttons)), clock=lambda: self.now)


def test_present_gets_mac_only(cfg):
    lad = Ladder(cfg, at(14))
    lad.n.notify("Disk almost full", "high")
    assert lad.mac == ["Disk almost full"] and lad.phone == []


def test_away_goes_to_phone_now(cfg):
    lad = Ladder(cfg, at(14), present=False)
    lad.n.notify("A1 — I need your OK: Move 3 files", "decision", buttons=[("✅ Confirm", "A1 yes")], key="A1")
    assert lad.phone == [("A1 — I need your OK: Move 3 files", [("✅ Confirm", "A1 yes")])]


def test_unanswered_decision_escalates_after_10_minutes(cfg):
    lad = Ladder(cfg, at(14))
    lad.n.notify("A1 — need OK", "decision", key="A1")
    lad.now += ESCALATE_AFTER - 1
    lad.n.tick(lambda key: True)
    assert lad.phone == []
    lad.now += 2
    lad.n.tick(lambda key: True)
    assert [t for t, _ in lad.phone] == ["A1 — need OK"]


def test_answered_decision_does_not_escalate(cfg):
    lad = Ladder(cfg, at(14))
    lad.n.notify("A2 — need OK", "decision", key="A2")
    lad.now += ESCALATE_AFTER + 1
    lad.n.tick(lambda key: False)
    assert lad.phone == []


def test_quiet_hours_hold_until_morning_digest(cfg):
    lad = Ladder(cfg, at(23, 45), present=False)
    lad.n.notify("Indexing failed")
    lad.n.notify("Battery at 8%", "high")
    assert lad.phone == []
    lad.now = at(7, 31) + 86400
    lad.n.tick(lambda key: True)
    assert len(lad.phone) == 1 and lad.phone[0][0].startswith("While you were away:")


def test_critical_ignores_quiet_hours(cfg):
    lad = Ladder(cfg, at(2), present=True)
    lad.n.notify("Security: unknown accounts", "critical")
    assert [t for t, _ in lad.phone] == ["Security: unknown accounts"]


def test_rate_limit_six_per_hour_then_held(cfg):
    lad = Ladder(cfg, at(14), present=False)
    for i in range(8):
        lad.n.notify(f"alert {i}")
    assert len(lad.phone) == 6 and lad.n.held == ["alert 6", "alert 7"]
    lad.now += 3601
    lad.n.tick(lambda key: True)
    assert len(lad.phone) == 7 and "alert 7" in lad.phone[-1][0]


def test_identical_alerts_within_30_min_merge(cfg):
    lad = Ladder(cfg, at(14), present=False)
    lad.n.notify("Disk low")
    lad.n.notify("Disk low")
    assert len(lad.phone) == 1


def test_secrets_never_leave(cfg):
    lad = Ladder(cfg, at(14), present=False)
    lad.n.notify("Saved note: my otp is 482913")
    assert "482913" not in lad.phone[0][0] and "[redacted]" in lad.phone[0][0]


def test_offline_phone_keeps_message(cfg):
    lad = Ladder(cfg, at(14), present=False)
    lad.n.remote = lambda text, buttons: (_ for _ in ()).throw(OSError("no internet"))
    lad.n.notify("Battery at 8%")
    assert lad.n.held == ["Battery at 8%"]


def test_secret_values_are_validated_before_the_keychain():
    with pytest.raises(ValueError):
        secrets.put("x", 'abc" ; rm -rf ~')


# --- daemon side ---

@pytest.fixture
def jarvis(cfg, tmp_path, monkeypatch):
    (tmp_path / "appdata").mkdir()
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "appdata")
    monkeypatch.setattr(daemon.ollama, "installed_models", lambda: set())
    j = daemon.Jarvis(cfg)
    j.notifier.mac = lambda text: None
    return j


def test_pending_action_reaches_notifier_with_buttons(jarvis, tmp_path):
    sent = []
    jarvis.notifier.notify = lambda text, urgency="normal", buttons=None, key="": sent.append((text, urgency, buttons))
    jarvis.executor.call("create_folder", {"path": str(tmp_path / "x")}, tainted=False, request_id="r")
    text, urgency, buttons = sent[0]
    assert text.startswith("A1 — I need your OK") and urgency == "decision" and buttons[0][1] == "A1 yes"


def test_remote_answer_confirms_exact_pending(jarvis, tmp_path):
    jarvis.executor.call("create_folder", {"path": str(tmp_path / "x")}, tainted=False, request_id="r")
    assert asyncio.run(jarvis.remote("A1 yes")).startswith("Created")
    assert (tmp_path / "x").is_dir()


def test_remote_free_text_never_acts(jarvis, tmp_path):
    reply = asyncio.run(jarvis.remote(f"move everything in {tmp_path} to trash"))
    assert reply.startswith("Saved as a note") and jarvis.pending.list() == []
    assert jarvis.activity.recent() == []


def test_remote_stop_and_status(jarvis):
    assert asyncio.run(jarvis.remote("stop")).startswith("Stopped everything")
    assert asyncio.run(jarvis.remote("status")).startswith("Paused")


class FakeTelegram:
    def __init__(self, batches):
        self.batches, self.sent, self.answered = list(batches), [], []

    def updates(self, offset, wait=50):
        if not self.batches:
            raise asyncio.CancelledError
        return self.batches.pop(0)

    def send(self, chat, text, buttons=None):
        self.sent.append((chat, text))

    def answer_button(self, cid, text=""):
        self.answered.append(cid)


def msg(uid, chat, text, age=0):
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": "private"}, "from": {"id": chat},
                                          "date": int(time.time() - age), "text": text}}


def test_telegram_loop_only_listens_to_owner(jarvis, tmp_path):
    jarvis.chat_id, alerts = 42, []
    jarvis.notifier.notify = lambda text, urgency="normal", **k: alerts.append(urgency)
    jarvis.executor.call("create_folder", {"path": str(tmp_path / "y")}, tainted=False, request_id="r")
    button = {"update_id": 9, "callback_query": {"id": "cb1", "data": "A1 yes", "from": {"id": 42},
                                                  "message": {"chat": {"id": 42}, "date": 0}}}
    jarvis.telegram = FakeTelegram([[msg(1, 7, "A1 yes"), msg(2, 7, "stop"), msg(3, 7, "hi"),
                                     msg(4, 42, "status", age=600), button]])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(daemon.telegram_loop(jarvis))
    assert (tmp_path / "y").is_dir()                       # only the owner's button press confirmed it
    assert alerts == ["decision", "critical"]               # the approval request, then 3 strangers -> one alert
    assert jarvis.telegram.answered == ["cb1"] and len(jarvis.telegram.sent) == 1  # old 'status' ignored
