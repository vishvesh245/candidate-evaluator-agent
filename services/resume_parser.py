import base64
import io
import pdfplumber
from typing import Optional
from models.schemas import Attachment

MAX_PAGES = 5  # Don't process more than 5 pages

# Minimum chars from pdfplumber before we consider a PDF "scanned" and fall back to Claude
MIN_TEXT_LENGTH = 50

IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _is_docx(attachment: Attachment) -> bool:
    ct = attachment.content_type.lower()
    name = attachment.name.lower()
    return "wordprocessingml" in ct or "msword" in ct or name.endswith(".docx") or name.endswith(".doc")


def _is_image(attachment: Attachment) -> bool:
    ct = attachment.content_type.lower()
    name = attachment.name.lower()
    return ct in IMAGE_CONTENT_TYPES or any(name.endswith(ext) for ext in IMAGE_EXTENSIONS)


def _extract_docx_text(file_bytes: bytes) -> tuple[str, Optional[str]]:
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs).strip()
        if not text:
            return "", "The Word document appears to be empty or uses unsupported formatting."
        return text, None
    except ImportError:
        return "", "DOCX parsing unavailable — python-docx not installed."
    except Exception as e:
        return "", f"Could not read the Word document: {str(e)}"


def _extract_pdf_text(file_bytes: bytes) -> tuple[str, bool]:
    """Returns (text, is_scanned). is_scanned=True when pdfplumber found no text."""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) == 0:
                return "", True
            pages_to_read = min(len(pdf.pages), MAX_PAGES)
            parts = []
            for i in range(pages_to_read):
                page_text = pdf.pages[i].extract_text()
                if page_text:
                    parts.append(page_text)
            text = "\n".join(parts).strip()
            return text, len(text) < MIN_TEXT_LENGTH
    except Exception:
        return "", True


async def _extract_via_claude(file_bytes: bytes, media_type: str, filename: str) -> tuple[str, Optional[str]]:
    """Use Claude Vision to extract text from scanned PDFs and images."""
    try:
        import anthropic
        from config import settings

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        b64 = base64.b64encode(file_bytes).decode("utf-8")

        if media_type == "application/pdf":
            source_block = {"type": "base64", "media_type": "application/pdf", "data": b64}
            content_block = {"type": "document", "source": source_block}
        else:
            # Normalise media type for Claude
            if "jpeg" in media_type or "jpg" in media_type:
                media_type = "image/jpeg"
            elif "png" in media_type:
                media_type = "image/png"
            elif "gif" in media_type:
                media_type = "image/gif"
            elif "webp" in media_type:
                media_type = "image/webp"
            else:
                media_type = "image/png"  # fallback
            source_block = {"type": "base64", "media_type": media_type, "data": b64}
            content_block = {"type": "image", "source": source_block}

        response = await client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    content_block,
                    {
                        "type": "text",
                        "text": (
                            "This is a resume. Extract all text from it exactly as written. "
                            "Preserve section headers, job titles, dates, and bullet points. "
                            "Return only the extracted text, no commentary."
                        ),
                    },
                ],
            }],
        )
        text = response.content[0].text.strip()
        if not text:
            return "", f"Could not extract any text from {filename}."
        return text, None

    except Exception as e:
        return "", f"Could not read the resume ({filename}): {str(e)}"


async def parse_resume(attachment: Attachment) -> tuple[str, Optional[str]]:
    """
    Returns (extracted_text, error_message).
    error_message is None on success.
    Handles: PDF (text + scanned), DOCX/DOC, PNG/JPG/image formats.
    """
    try:
        file_bytes = base64.b64decode(attachment.content)
    except Exception:
        return "", "Could not decode the attachment — please re-attach your resume and try again."

    # DOCX / DOC
    if _is_docx(attachment):
        return _extract_docx_text(file_bytes)

    # Image (resume screenshot or photo)
    if _is_image(attachment):
        return await _extract_via_claude(file_bytes, attachment.content_type, attachment.name)

    # PDF — try pdfplumber first, fall back to Claude Vision for scanned
    text, is_scanned = _extract_pdf_text(file_bytes)
    if not is_scanned:
        return text, None

    # Scanned PDF — use Claude Vision
    return await _extract_via_claude(file_bytes, "application/pdf", attachment.name)
