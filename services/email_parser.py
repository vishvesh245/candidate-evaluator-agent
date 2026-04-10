import re
from typing import Optional
from models.schemas import ParsedApplication, Attachment


GITHUB_PATTERN = re.compile(
    r"https?://(?:www\.)?github\.com/([a-zA-Z0-9_-]+)(?:/[^\s\"'\n>.,;)]*)*/?(?:\s|$|\"|\n|>|[.,;)])",
    re.IGNORECASE,
)
GITHUB_BARE_PATTERN = re.compile(
    r"(?:^|\s)github\.com/([a-zA-Z0-9_-]+)(?:/[^\s.,;)]*)*/?(?:\s|$|[.,;)])",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(
    r"https?://[^\s\"'<>\n]+",
    re.IGNORECASE,
)

# Domains to exclude when detecting portfolio URL
EXCLUDED_DOMAINS = {"github.com", "linkedin.com", "twitter.com", "x.com", "mailto:"}

# Cloud storage domains that indicate a resume link instead of attachment
CLOUD_RESUME_DOMAINS = (
    "drive.google.com",
    "docs.google.com",
    "dropbox.com",
    "1drv.ms",              # OneDrive short URL
    "onedrive.live.com",
    "icloud.com",
    "box.com",
    "notion.so",
)


def extract_cloud_resume_url(text: str) -> Optional[str]:
    """Detect if candidate pasted a cloud storage link instead of attaching their resume."""
    urls = URL_PATTERN.findall(text)
    for url in urls:
        if any(domain in url.lower() for domain in CLOUD_RESUME_DOMAINS):
            return url.rstrip(".,;)")
    return None


def extract_github_url(text: str) -> Optional[str]:
    match = GITHUB_PATTERN.search(text)
    if match:
        return f"https://github.com/{match.group(1)}"
    match = GITHUB_BARE_PATTERN.search(text)
    if match:
        return f"https://github.com/{match.group(1)}"
    return None


def extract_portfolio_url(text: str, github_url: Optional[str]) -> Optional[str]:
    urls = URL_PATTERN.findall(text)
    for url in urls:
        skip = False
        for domain in EXCLUDED_DOMAINS:
            if domain in url.lower():
                skip = True
                break
        if skip:
            continue
        if github_url and url.rstrip("/") == github_url.rstrip("/"):
            continue
        # Clean trailing punctuation
        url = url.rstrip(".,;)")
        return url
    return None


IGNORED_EXTENSIONS = {".svg", ".ico"}  # tiny inline assets, skip silently

# Attachment types we'll treat as a resume
RESUME_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
    "text/plain",
}
RESUME_EXTENSIONS = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".rtf"}


def find_resume_attachment(attachments: list[dict]) -> Optional[Attachment]:
    """
    Returns the first attachment that looks like a resume.
    Accepts PDF, DOCX/DOC, images, and plain text. Ignores decorative assets.
    """
    for att in attachments:
        content_type = att.get("ContentType", "").lower()
        name = att.get("Name", "")
        name_lower = name.lower()

        # Skip tiny inline decorative assets
        if any(name_lower.endswith(ext) for ext in IGNORED_EXTENSIONS):
            continue

        is_resume_type = (
            any(ct in content_type for ct in ("pdf", "msword", "wordprocessingml", "text/plain"))
            or content_type in RESUME_CONTENT_TYPES
            or any(name_lower.endswith(ext) for ext in RESUME_EXTENSIONS)
        )

        if is_resume_type:
            return Attachment(
                name=name or "resume",
                content=att.get("Content", ""),
                content_type=att.get("ContentType", "application/octet-stream"),
                content_length=att.get("ContentLength", 0),
            )

    return None


def parse_inbound_email(payload: dict) -> ParsedApplication:
    from_full = payload.get("FromFull", {})
    sender_email = from_full.get("Email", payload.get("From", ""))
    sender_name = from_full.get("Name", sender_email.split("@")[0])

    subject = payload.get("Subject", "")
    body_text = payload.get("TextBody", "") or payload.get("HtmlBody", "")

    # Strip basic HTML tags if only HTML body
    # First extract href URLs so links inside <a> tags aren't lost
    if not payload.get("TextBody") and payload.get("HtmlBody"):
        href_urls = re.findall(r'href=["\']([^"\']+)["\']', body_text, re.IGNORECASE)
        body_text = re.sub(r"<[^>]+>", " ", body_text)
        body_text = re.sub(r"\s+", " ", body_text).strip()
        # Append extracted hrefs so GitHub/portfolio URLs are still detectable
        if href_urls:
            body_text += " " + " ".join(href_urls)

    # Search both subject + body for links
    full_text = f"{subject}\n{body_text}"

    github_url = extract_github_url(full_text)
    portfolio_url = extract_portfolio_url(full_text, github_url)

    attachments = payload.get("Attachments", [])
    resume_attachment = find_resume_attachment(attachments)
    cloud_resume_url = extract_cloud_resume_url(full_text) if not resume_attachment else None

    return ParsedApplication(
        sender_email=sender_email.lower().strip(),
        sender_name=sender_name.strip(),
        subject=subject,
        body_text=body_text,
        resume_attachment=resume_attachment,
        cloud_resume_url=cloud_resume_url,
        github_url=github_url,
        portfolio_url=portfolio_url,
        message_id=payload.get("MessageID", ""),
    )
