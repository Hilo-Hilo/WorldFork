from __future__ import annotations

from io import BytesIO

from sqlalchemy.orm import Session

from app.db import models
from app.storage.artifact_store import ArtifactStore

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
except ModuleNotFoundError:
    letter = None
    canvas = None


def render_markdown_pdf(
    db: Session,
    *,
    big_bang_id,
    relative_path: str,
    title: str,
    markdown: str,
) -> models.Artifact:
    encoded = render_markdown_pdf_bytes(title=title, markdown=markdown)
    return ArtifactStore().write_bytes(
        db,
        big_bang_id=big_bang_id,
        relative_path=relative_path,
        body=encoded,
        kind="report_pdf",
        content_type="application/pdf",
        debug_only=False,
    )


def render_markdown_pdf_bytes(*, title: str, markdown: str) -> bytes:
    if canvas is None or letter is None:
        raise RuntimeError("PDF rendering dependency reportlab is not installed")
    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 72
    page.setFont("Helvetica-Bold", 14)
    page.drawString(72, y, title[:90])
    y -= 28
    page.setFont("Helvetica", 10)
    for line in markdown.splitlines():
        if y < 72:
            page.showPage()
            page.setFont("Helvetica", 10)
            y = height - 72
        page.drawString(72, y, line[:110])
        y -= 14
    page.save()
    return buffer.getvalue()
