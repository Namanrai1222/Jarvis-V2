"""Document search with citations, and vision: look at the screen, describe an image. All output is untrusted."""
import datetime
from pathlib import Path

from ..documents import extract
from ..llm import ollama
from ..policy.paths import PolicyError, display, resolve
from . import ToolResult, register

INDEX = None  # set by the daemon to its DocIndex
SCREENSHOTS = Path.home() / "Jarvis/Screenshots"


def _search(a: dict, cfg: dict) -> ToolResult:
    if INDEX is None:
        return ToolResult("The document index isn't running.")
    hits = INDEX.search(a["query"])
    if not hits:
        return ToolResult("No matching passages in the indexed documents.")
    out = []
    for h in hits:
        cite = Path(h["path"]).name + (f" · p. {h['page']}" if h["page"] else "")
        out.append(f"[{cite}] ({display(h['path'])})\n{h['text'][:1200]}")
    return ToolResult("\n\n".join(out))


register(
    name="search_documents",
    description="Search Naman's indexed documents (notes, PDFs, Word files, code) by meaning and keywords. "
                "Returns passages with file name and page to cite.",
    params={"query": {"type": "string"}}, required=["query"], tier=0, run=_search, untrusted_output=True,
    summary=lambda a: f"Search documents for '{a['query']}'",
)


def _describe(path: Path, question: str, cfg: dict) -> str:
    text = extract.ocr_image(path).strip()
    answer = ollama.describe_image(cfg["models"]["vision"], path.read_bytes(),
                                   question or "Describe what's in this image in 2-4 sentences.")
    return answer + (f"\n\nText found in the image (OCR):\n{text[:3000]}" if text else "")


def _look(a: dict, cfg: dict) -> ToolResult:
    import Quartz

    if not Quartz.CGPreflightScreenCaptureAccess():
        Quartz.CGRequestScreenCaptureAccess()  # shows the macOS prompt once
        return ToolResult("I need Screen Recording permission: System Settings > Privacy & Security > "
                          "Screen Recording, turn on Jarvis (python), then ask again.")
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS / f"screen-{datetime.datetime.now():%Y%m%d-%H%M%S}.png"
    import subprocess

    subprocess.run(["screencapture", "-x", "-m", str(path)], check=True, timeout=20)
    return ToolResult(f"Screenshot saved to {display(path)}.\n{_describe(path, a.get('question', ''), cfg)}")


register(
    name="look_at_screen",
    description="Take a screenshot of the main display and describe it (optionally answer a question about it).",
    params={"question": {"type": "string"}}, required=[], tier=1, run=_look, untrusted_output=True,
    summary=lambda a: "Take a screenshot and describe it",
)


def _check_image(a: dict, cfg: dict) -> dict:
    p = resolve(a.get("path", ""), cfg, must_exist=True)
    if p.suffix.lower() not in extract.IMAGE_EXT:
        raise PolicyError("that isn't an image; use read_file for documents")
    return {"path": str(p), "question": str(a.get("question") or "")}


register(
    name="describe_image",
    description="Describe an image file (photo, screenshot, diagram) or answer a question about it.",
    params={"path": {"type": "string"}, "question": {"type": "string"}}, required=["path"], tier=0,
    check=_check_image, run=lambda a, c: ToolResult(_describe(Path(a["path"]), a["question"], c)),
    untrusted_output=True, summary=lambda a: f"Describe {display(a['path'])}",
)
