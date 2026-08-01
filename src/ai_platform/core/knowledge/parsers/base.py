"""Document parsers — extract text from various file formats."""

from __future__ import annotations


async def parse_document(content: bytes, mime_type: str) -> str:
    """
    Parse a document into plain text based on MIME type.

    Supported formats: text/plain, text/markdown, application/pdf, text/html
    """
    if mime_type in ("text/plain", "text/markdown", ""):
        return content.decode("utf-8", errors="replace")

    if mime_type == "application/pdf":
        return await _parse_pdf(content)

    if mime_type == "text/html":
        return await _parse_html(content)

    if mime_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        return await _parse_docx(content)

    # Fallback: try as plain text
    return content.decode("utf-8", errors="replace")


async def _parse_pdf(content: bytes) -> str:
    """Parse PDF using pymupdf4llm."""
    try:
        import pymupdf4llm

        text = pymupdf4llm.to_markdown(content)
        return text
    except ImportError:
        # Fallback to PyMuPDF directly
        import fitz

        doc = fitz.open(stream=content, filetype="pdf")
        text = "\n\n".join(page.get_text() for page in doc)
        doc.close()
        return text


async def _parse_html(content: bytes) -> str:
    """Parse HTML using BeautifulSoup."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    # Remove script and style elements
    for element in soup(["script", "style"]):
        element.decompose()
    return soup.get_text(separator="\n", strip=True)


async def _parse_docx(content: bytes) -> str:
    """Parse DOCX using python-docx."""
    import io

    from docx import Document

    doc = Document(io.BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)
