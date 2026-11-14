from jarvis.agent import fastpath


def test_open_multiple_apps(cfg):
    assert fastpath.match("Open VS Code and Chrome.", cfg).calls == [
        ("open_app", {"app": "com.microsoft.VSCode"}), ("open_app", {"app": "com.google.Chrome"})]


def test_hinglish_open(cfg):
    assert fastpath.parse_open("chrome aur spotify kholo", cfg["apps"]) == (
        ["com.google.Chrome", "com.spotify.client"], [])


def test_anything_unrecognised_goes_to_llm(cfg):
    assert fastpath.match("open terminal", cfg) is None
    assert fastpath.match("open VS Code and Chrome, and start my internship playlist", cfg) is None


def test_open_site_and_search(cfg):
    assert fastpath.match("open youtube.com", cfg).calls == [("open_url", {"url": "youtube.com"})]
    assert fastpath.match("search for dbms normalization", cfg).calls == [
        ("web_search", {"query": "dbms normalization"})]


def test_time(cfg):
    assert fastpath.match("what time is it?", cfg).reply.startswith("It's ")


def test_falls_through_to_llm(cfg):
    assert fastpath.match("tell me a joke", cfg) is None
