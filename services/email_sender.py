import httpx
from models.schemas import ParsedApplication, EvaluationResult
from config import settings

BREVO_API = "https://api.brevo.com/v3/smtp/email"
FROM_NAME = "Company Residency"
FROM_EMAIL = "vishpan245@gmail.com"


async def _send(to_email: str, to_name: str, subject: str, text_body: str, html_body: str):
    recipient = settings.test_email_override if settings.test_email_override else to_email
    headers = {
        "api-key": settings.brevo_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "sender": {"name": FROM_NAME, "email": FROM_EMAIL},
        "to": [{"email": recipient, "name": to_name}],
        "subject": subject,
        "textContent": text_body,
        "htmlContent": html_body,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(BREVO_API, json=payload, headers=headers)
        resp.raise_for_status()


async def send_pass_email(application: ParsedApplication, evaluation: EvaluationResult):
    name = application.sender_name.split()[0] if application.sender_name else "there"
    subject = "Your application to Company's Builder Residency — Next Steps"

    text = f"""Hi {name},

Thank you for applying to the Company's Builder Residency.

{evaluation.email_summary}

We'd like to move forward with your application. Someone from the team will reach out within 2 business days to schedule a short conversation.

Looking forward to it.

— The Company Team
"""

    html = f"""<p>Hi {name},</p>
<p>Thank you for applying to the Company's Builder Residency.</p>
<p>{evaluation.email_summary}</p>
<p>We'd like to move forward with your application. Someone from the team will reach out within 2 business days to schedule a short conversation.</p>
<p>Looking forward to it.</p>
<p>— The Company Team</p>
"""

    await _send(application.sender_email, application.sender_name, subject, text, html)


async def send_fail_email(application: ParsedApplication, evaluation: EvaluationResult):
    name = application.sender_name.split()[0] if application.sender_name else "there"
    subject = "Your application to Company's Builder Residency"

    body = evaluation.email_summary or "Your profile doesn't match what we're looking for in this cohort."

    text = f"""Hi {name},

Thank you for taking the time to apply to the Company's Builder Residency.

After reviewing your application, we won't be moving forward at this time. {body}

We appreciate your interest and wish you the best in what you're building.

Best,
The Company Team
"""

    html = f"""<p>Hi {name},</p>
<p>Thank you for taking the time to apply to the Company's Builder Residency.</p>
<p>After reviewing your application, we won't be moving forward at this time. {body}</p>
<p>We appreciate your interest and wish you the best in what you're building.</p>
<p>Best,<br>The Company Team</p>
"""

    await _send(application.sender_email, application.sender_name, subject, text, html)


async def send_incomplete_email(application: ParsedApplication, missing: list[str]):
    name = application.sender_name.split()[0] if application.sender_name else "there"
    subject = "Your application to Company's Builder Residency — Missing Information"

    missing_list = "\n".join(f"- {item}" for item in missing)
    missing_html = "".join(f"<li>{item}</li>" for item in missing)

    text = f"""Hi {name},

Thanks for applying to the Company's Builder Residency. We received your email but noticed your application is missing the following:

{missing_list}

Please reply to this email with the missing items and we'll get your application reviewed.

— The Company Team
"""

    html = f"""<p>Hi {name},</p>
<p>Thanks for applying to the Company's Builder Residency. We received your email but noticed your application is missing the following:</p>
<ul>{missing_html}</ul>
<p>Please reply to this email with the missing items and we'll get your application reviewed.</p>
<p>— The Company Team</p>
"""

    await _send(application.sender_email, application.sender_name, subject, text, html)


async def send_duplicate_email(application: ParsedApplication):
    name = application.sender_name.split()[0] if application.sender_name else "there"
    subject = "Re: Your application to Company's Builder Residency"

    text = f"""Hi {name},

We already have your application on file. We'll be in touch if there's a fit.

— The Company Team
"""

    html = f"""<p>Hi {name},</p>
<p>We already have your application on file. We'll be in touch if there's a fit.</p>
<p>— The Company Team</p>
"""

    await _send(application.sender_email, application.sender_name, subject, text, html)


async def send_non_application_email(application: ParsedApplication):
    name = application.sender_name.split()[0] if application.sender_name else "there"
    subject = "Re: Your message"

    text = f"""Hi {name},

This inbox is only for Builder's Residency applications. To apply, please send your resume (PDF), GitHub profile link, and portfolio/project link to this address.

— The Company Team
"""

    html = f"""<p>Hi {name},</p>
<p>This inbox is only for Builder's Residency applications. To apply, please send your resume (PDF), GitHub profile link, and portfolio/project link to this address.</p>
<p>— The Company Team</p>
"""

    await _send(application.sender_email, application.sender_name, subject, text, html)
