import base64
import io
import pdfplumber
from typing import Optional
from models.schemas import Attachment


MAX_PAGES = 5  # Don't process more than 5 pages — resumes shouldn't be longer


def parse_resume(attachment: Attachment) -> tuple[str, Optional[str]]:
    """
    Returns (extracted_text, error_message).
    error_message is None on success.
    """
    try:
        pdf_bytes = base64.b64decode(attachment.content)
    except Exception:
        return "", "Could not decode the attachment. Please make sure your resume is attached as a valid PDF file."

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) == 0:
                return "", "The PDF appears to be empty."

            pages_to_read = min(len(pdf.pages), MAX_PAGES)
            text_parts = []

            for i in range(pages_to_read):
                page_text = pdf.pages[i].extract_text()
                if page_text:
                    text_parts.append(page_text)

            full_text = "\n".join(text_parts).strip()

            if not full_text:
                return "", (
                    "We couldn't extract text from your resume PDF. "
                    "This usually means it's a scanned image rather than a text-based PDF. "
                    "Please re-export your resume as a text-based PDF from Word, Google Docs, or a similar tool."
                )

            return full_text, None

    except Exception as e:
        return "", f"There was an issue reading your PDF: {str(e)}. Please make sure it's a valid, non-password-protected PDF."
