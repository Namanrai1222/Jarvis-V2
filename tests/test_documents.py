"""M3: extraction, the document index lifecycle, hybrid search with citations, upload, vision guard."""
import hashlib
import re

import docx
import pytest
from fastapi.testclient import TestClient

from jarvis import config, daemon
from jarvis.documents import extract, index
from jarvis.documents.index import DocIndex
from jarvis.policy.engine import decide
from jarvis.tools import REGISTRY
from jarvis.tools import docs as doc_tools


def make_pdf(path, pages):
    """Tiny valid PDF with one line of Helvetica text per page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objs)} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(out)


def fake_embed(texts):
    """Deterministic bag-of-words vectors: shared words -> similar vectors."""
    vecs = []
    for t in texts:
        v = [0.0] * 64
        for w in re.findall(r"\w+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
        vecs.append(v)
    return vecs


@pytest.fixture
def docs_dir(tmp_path):
    d = tmp_path / "College"
    (d / "node_modules").mkdir(parents=True)
    make_pdf(d / "GST_notes.pdf", ["Intro to taxation", "GST input tax credit rules apply to registered dealers"])
    doc = docx.Document()
    doc.add_paragraph("DBMS normalization: 1NF, 2NF, 3NF and BCNF.")
    doc.save(d / "dbms.docx")
    (d / "todo.md").write_text("# Todo\nFinish RepoRadar README")
    (d / "node_modules" / "junk.md").write_text("GST should never be indexed from here")
    (d / ".env").write_text("SECRET=GST")
    return d


@pytest.fixture
def idx(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "on_battery_low", lambda threshold=30: False)
    return DocIndex(str(tmp_path / "jarvis.db"), embed=fake_embed)


def test_extract_pdf_pages_and_docx(docs_dir):
    assert extract.pages(docs_dir / "GST_notes.pdf")[1] == (2, "GST input tax credit rules apply to registered dealers")
    assert "BCNF" in extract.pages(docs_dir / "dbms.docx")[0][1]
    with pytest.raises(ValueError):
        extract.pages(docs_dir / ".env")


def test_chunks_keep_pages_and_overlap():
    pieces = extract.chunks([(3, "a" * 1500 + "\n" + "b" * 1500)], size=2000, overlap=200)
    assert all(p == 3 for p, _ in pieces) and len(pieces) == 2


def test_index_lifecycle(idx, docs_dir, cfg):
    stats = idx.scan([str(docs_dir)], cfg)
    assert stats["indexed"] == 3 and idx.stats()["documents"] == 3  # node_modules and .env skipped
    assert idx.scan([str(docs_dir)], cfg)["unchanged"] == 3
    (docs_dir / "todo.md").unlink()
    assert idx.scan([str(docs_dir)], cfg)["removed"] == 1
    assert not any(h["path"].endswith("todo.md") for h in idx.search("RepoRadar README"))


def test_search_cites_file_and_page(idx, docs_dir, cfg):
    idx.scan([str(docs_dir)], cfg)
    hit = idx.search("input tax credit")[0]
    assert hit["path"].endswith("GST_notes.pdf") and hit["page"] == 2


def test_code_is_opt_in(idx, docs_dir, cfg):
    (docs_dir / "app.py").write_text("def gst_total(): pass")
    assert idx.scan([str(docs_dir)], cfg)["indexed"] == 3
    cfg["documents"]["include_code"] = True
    assert idx.scan([str(docs_dir)], cfg)["indexed"] == 1


def test_folders_outside_scope_are_not_indexed(idx, cfg, tmp_path):
    assert idx.scan(["/etc", "~/.ssh"], cfg)["indexed"] == 0


def test_low_battery_skips_indexing(tmp_path, cfg, monkeypatch, docs_dir):
    monkeypatch.setattr(index, "on_battery_low", lambda threshold=30: True)
    i = DocIndex(str(tmp_path / "b.db"))
    assert "skipped" in i.scan([str(docs_dir)], cfg)
    assert i.scan([str(docs_dir)], cfg, force=True)["indexed"] == 3


def test_search_documents_tool_is_untrusted_and_cites(idx, docs_dir, cfg, monkeypatch):
    idx.scan([str(docs_dir)], cfg)
    monkeypatch.setattr(doc_tools, "INDEX", idx)
    tool = REGISTRY["search_documents"]
    assert tool.untrusted_output and tool.tier == 0
    assert "[GST_notes.pdf · p. 2]" in tool.run({"query": "GST credit"}, cfg).text


def test_read_file_reads_pdf_with_page_markers(cfg, docs_dir):
    out = REGISTRY["read_file"].run({"path": str(docs_dir / "GST_notes.pdf")}, cfg).text
    assert "[page 2]" in out and "input tax credit" in out


def test_describe_image_only_takes_images_in_scope(cfg, docs_dir):
    tool = REGISTRY["describe_image"]
    assert decide(tool, {"path": str(docs_dir / "dbms.docx")}, tainted=False, paused=False, cfg=cfg).verdict == "block"
    assert decide(tool, {"path": "/etc/hosts.png"}, tainted=False, paused=False, cfg=cfg).verdict == "block"


def test_look_at_screen_is_tier1_so_tainted_requests_confirm(cfg):
    tool = REGISTRY["look_at_screen"]
    assert decide(tool, {}, tainted=True, paused=False, cfg=cfg).verdict == "confirm"


def test_upload_saves_to_inbox(tmp_path, monkeypatch):
    (tmp_path / "appdata").mkdir()
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "appdata")
    monkeypatch.setattr(config, "TOKEN_PATH", tmp_path / "appdata" / "token")
    monkeypatch.setattr(daemon, "INBOX", tmp_path / "Inbox")
    monkeypatch.setattr(daemon.ollama, "installed_models", lambda: set())
    monkeypatch.setattr(daemon.Path, "home", classmethod(lambda cls: tmp_path))
    app = daemon.build_app()
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    h = {"X-Jarvis-Token": (tmp_path / "appdata" / "token").read_text()}
    r = c.post("/api/upload?name=../../evil notes.pdf", content=b"%PDF-1.4", headers=h)
    assert r.status_code == 200 and r.json()["path"] == "~/Inbox/evil notes.pdf"
    assert (tmp_path / "Inbox" / "evil notes.pdf").exists()
    assert c.post("/api/upload?name=run.sh.exe", content=b"x", headers=h).status_code == 415
    assert c.post("/api/upload?name=a.pdf", content=b"x").status_code == 401
