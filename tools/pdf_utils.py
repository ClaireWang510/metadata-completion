"""Fetch arXiv PDF and extract first-page text / image."""
from __future__ import annotations
import base64
import io
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any

import httpx
from pypdf import PdfReader

try:  # resolve the optional renderer before tests/callers monkey-patch os.path
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover - environment dependent
    pdfium = None

import config
from cost import CallRecord, CostRecorder


ARXIV_PDF = "https://arxiv.org/pdf/{id}.pdf"


def download_pdf(arxiv_id: str, cache_dir: str, *, cost: CostRecorder) -> str | None:
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{arxiv_id}.pdf")
    if os.path.exists(path):
        return path
    url = ARXIV_PDF.format(id=arxiv_id)
    t0 = time.time(); ok = True
    try:
        with httpx.Client(timeout=60.0,
                          headers={"User-Agent": "meta-completion/0.1"}) as cli:
            r = cli.get(url, follow_redirects=True)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
    except Exception:
        ok = False
        path = None
    finally:
        cost.add(CallRecord(agent="PDFAgent", kind="http", model_or_url=url,
                            latency_ms=(time.time() - t0) * 1000, ok=ok))
    return path


def first_page_text(pdf_path: str, *, cost: CostRecorder,
                    max_chars: int = 12000) -> str:
    t0 = time.time(); text = ""
    try:
        r = PdfReader(pdf_path)
        pages = r.pages[:1]
        text = "\n".join(p.extract_text() or "" for p in pages)
        text = text[:max_chars]
    finally:
        cost.add(CallRecord(agent="PDFAgent", kind="pdf",
                            model_or_url=pdf_path,
                            latency_ms=(time.time() - t0) * 1000, ok=bool(text)))
    return text


def first_page_image(pdf_path: str, *, cost: CostRecorder) -> str | None:
    """Render page one as a PNG data URL for a genuine multimodal call."""
    t0 = time.time(); ok = False; error = ""
    try:
        if shutil.which("pdftoppm"):
            # Keep renderer scratch files in the caller's writable workspace;
            # managed environments may deny the OS-global temp directory.
            with tempfile.TemporaryDirectory(prefix=".pdf-render-", dir=os.getcwd()) as tmp:
                prefix = os.path.join(tmp, "page")
                subprocess.run(["pdftoppm", "-f", "1", "-singlefile", "-r", "144",
                                "-png", pdf_path, prefix], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=30)
                with open(prefix + ".png", "rb") as image_file:
                    payload = image_file.read()
        elif pdfium is not None:
            document = pdfium.PdfDocument(pdf_path)
            image = document[0].render(scale=2.0).to_pil()
            buffer = io.BytesIO(); image.save(buffer, format="PNG")
            payload = buffer.getvalue()
        else:
            return None
        ok = True
        return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
    except Exception as exc:  # optional renderer; text/LaTeX fallback remains available
        error = f"{type(exc).__name__}: {exc}"
        return None
    finally:
        cost.add(CallRecord(agent="PDFAgent", kind="pdf", model_or_url=pdf_path,
                            latency_ms=(time.time() - t0) * 1000, ok=ok,
                            extra={"operation": "render_first_page", "error": error}))
