"""Text out of files: PDF per page, DOCX, text and code, and macOS Vision OCR for images and scanned PDF pages."""
from pathlib import Path

CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".sql", ".sh",
            ".html", ".css"}
TEXT_EXT = {".md", ".txt", ".csv", ".rst", ".tex"} | CODE_EXT
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".tiff"}
DOC_EXT = {".pdf", ".docx"} | TEXT_EXT | IMAGE_EXT
MAX_TEXT_BYTES = 1_000_000
MAX_DOC_BYTES = 30_000_000


def pages(path: Path) -> list[tuple[int | None, str]]:
    """[(page number or None, text)]. Raises ValueError for unsupported or oversized files."""
    ext = path.suffix.lower()
    size = path.stat().st_size
    if ext not in DOC_EXT:
        raise ValueError(f"{path.suffix or 'this'} files aren't supported")
    if size > (MAX_TEXT_BYTES if ext in TEXT_EXT else MAX_DOC_BYTES):
        raise ValueError(f"{path.name} is too large ({size // 1_000_000} MB)")
    if ext == ".pdf":
        return _pdf(path)
    if ext == ".docx":
        import docx

        paras = [p.text for p in docx.Document(str(path)).paragraphs if p.text.strip()]
        return [(None, "\n".join(paras))]
    if ext in IMAGE_EXT:
        return [(None, ocr_image(path))]
    return [(None, path.read_text(errors="replace"))]


def _pdf(path: Path) -> list[tuple[int | None, str]]:
    from pypdf import PdfReader

    out = []
    for i, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = (page.extract_text() or "").strip()
        if len(text) < 20:  # probably a scanned page: read it with OCR instead
            text = ocr_pdf_page(path, i) or text
        out.append((i, text))
    return out


def _recognize(cg_image) -> str:
    import Vision

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    ok, _ = handler.performRequests_error_([request], None)
    if not ok:
        return ""
    return "\n".join(o.topCandidates_(1)[0].string() for o in (request.results() or []))


def ocr_image(path: Path) -> str:
    import Quartz
    from Foundation import NSURL

    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)
    image = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None) if src else None
    return _recognize(image) if image else ""


def ocr_pdf_page(path: Path, number: int) -> str:
    import Quartz
    from Foundation import NSURL

    doc = Quartz.CGPDFDocumentCreateWithURL(NSURL.fileURLWithPath_(str(path)))
    page = Quartz.CGPDFDocumentGetPage(doc, number) if doc else None
    if page is None:
        return ""
    box = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
    scale = 2.0  # render at 2x so small print is readable
    w, h = int(box.size.width * scale), int(box.size.height * scale)
    ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, 0, Quartz.CGColorSpaceCreateDeviceRGB(),
                                       Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
    Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w, h))
    Quartz.CGContextScaleCTM(ctx, scale, scale)
    Quartz.CGContextDrawPDFPage(ctx, page)
    return _recognize(Quartz.CGBitmapContextCreateImage(ctx))


def chunks(page_texts: list[tuple[int | None, str]], size: int = 2000, overlap: int = 200) -> list[tuple[int | None, str]]:
    """Split into ~500-token pieces on paragraph boundaries, keeping the page number."""
    out = []
    for page, text in page_texts:
        text = text.strip()
        while text:
            if len(text) <= size:
                out.append((page, text))
                break
            cut = text.rfind("\n", size // 2, size)
            cut = cut if cut > 0 else size
            out.append((page, text[:cut].strip()))
            text = text[max(cut - overlap, 1):].strip()
    return out
