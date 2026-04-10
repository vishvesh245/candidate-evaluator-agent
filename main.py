import asyncio
import json
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse

import database
from services.email_parser import parse_inbound_email
from services.resume_parser import parse_resume, fetch_cloud_resume
from services.github_extractor import extract_github_signals
from services.portfolio_scraper import scrape_portfolio
from services.evaluator import evaluate_candidate
from services.email_sender import (
    send_pass_email,
    send_fail_email,
    send_incomplete_email,
    send_duplicate_email,
    send_non_application_email,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    logger.info("Database initialized.")
    yield


app = FastAPI(title="Candidate Evaluator", lifespan=lifespan)


def looks_like_application(body_text: str) -> bool:
    keywords = ["apply", "application", "resume", "github", "portfolio", "residency", "candidate"]
    return any(kw in body_text.lower() for kw in keywords)


async def process_application(payload: dict):
    try:
        received_at = datetime.utcnow().isoformat()
        application = parse_inbound_email(payload)
        logger.info(f"── Inbound email from {application.sender_email} | Subject: {application.subject!r}")

        # 1. Duplicate check
        if await database.is_duplicate(application.sender_email):
            logger.info(f"── DUPLICATE: {application.sender_email} already evaluated")
            await send_duplicate_email(application)
            return

        # 2. Non-application email check
        if not looks_like_application(application.body_text) and not application.resume_attachment:
            logger.info(f"── NON-APPLICATION email from {application.sender_email}, ignoring")
            await send_non_application_email(application)
            return

        # 3. Try to fetch resume from cloud link if no attachment
        if not application.resume_attachment and application.cloud_resume_url:
            logger.info(f"── Attempting to fetch cloud resume: {application.cloud_resume_url}")
            fetched = await fetch_cloud_resume(application.cloud_resume_url)
            if fetched:
                logger.info(f"── Cloud resume fetched successfully ({fetched.content_length} bytes)")
                application = application.model_copy(update={"resume_attachment": fetched, "cloud_resume_url": None})
            else:
                logger.info(f"── Cloud resume not accessible (private/gated)")

        # 4. Missing fields check (after cloud fetch attempt)
        missing = application.get_missing_fields()
        if missing:
            logger.info(f"── INCOMPLETE from {application.sender_email} | Missing: {missing}")
            await send_incomplete_email(application, missing)
            await database.save_application(
                email=application.sender_email,
                sender_name=application.sender_name,
                status="incomplete",
                subject=application.subject,
                body_text=application.body_text,
                github_url=application.github_url,
                portfolio_url=application.portfolio_url,
                has_resume=application.resume_attachment is not None,
                received_at=received_at,
            )
            return

        # 4. Parse resume
        resume_text, resume_error = await parse_resume(application.resume_attachment)
        if resume_error:
            logger.warning(f"── RESUME ERROR for {application.sender_email}: {resume_error}")
            await send_incomplete_email(application, [resume_error])
            await database.save_application(
                email=application.sender_email,
                sender_name=application.sender_name,
                status="incomplete",
                subject=application.subject,
                body_text=application.body_text,
                github_url=application.github_url,
                portfolio_url=application.portfolio_url,
                has_resume=True,
                received_at=received_at,
            )
            return

        logger.info(f"── Resume parsed: {len(resume_text)} chars")

        # 5. Extract signals in parallel
        github_signals, portfolio_signals = await asyncio.gather(
            extract_github_signals(application.github_url),
            scrape_portfolio(application.portfolio_url),
        )
        logger.info(
            f"── GitHub @{github_signals.username}: {github_signals.public_repos} repos, "
            f"{github_signals.recent_activity_count} events/90d, "
            f"languages: {github_signals.top_languages[:3]}"
        )
        logger.info(
            f"── Portfolio: accessible={portfolio_signals.accessible} | {application.portfolio_url}"
        )

        # 6. Evaluate
        logger.info(f"── Running Claude evaluation for {application.sender_email}...")
        try:
            evaluation = await evaluate_candidate(
                candidate_name=application.sender_name,
                resume_text=resume_text,
                github_signals=github_signals,
                portfolio_signals=portfolio_signals,
            )
        except Exception as eval_err:
            logger.error(f"── EVALUATION FAILED for {application.sender_email}: {eval_err}")
            await send_incomplete_email(application, ["We ran into a technical issue processing your application. Please re-submit and we'll try again."])
            await database.save_application(
                email=application.sender_email,
                sender_name=application.sender_name,
                status="incomplete",
                subject=application.subject,
                body_text=application.body_text,
                github_url=application.github_url,
                portfolio_url=application.portfolio_url,
                has_resume=True,
                received_at=received_at,
            )
            return

        s = evaluation.scores
        logger.info(
            f"── Scores | shipped={s.shipped_products} technical={s.technical_depth} "
            f"business={s.business_thinking} speed={s.speed_of_execution} clarity={s.communication_clarity}"
        )
        logger.info(
            f"── Weighted total: {evaluation.weighted_total} → {evaluation.decision.upper()}"
        )

        # 7. Save to DB first — decouple from email delivery
        await database.save_application(
            email=application.sender_email,
            sender_name=application.sender_name,
            status=evaluation.decision,
            score=evaluation.weighted_total,
            result=evaluation.model_dump(),
            subject=application.subject,
            body_text=application.body_text,
            github_url=application.github_url,
            portfolio_url=application.portfolio_url,
            has_resume=True,
            received_at=received_at,
        )
        logger.info(f"── Saved to DB: {application.sender_email} → {evaluation.decision.upper()} ({evaluation.weighted_total}/100)")

        # 8. Send response email — log failure but don't crash
        try:
            if evaluation.decision == "pass":
                await send_pass_email(application, evaluation)
            else:
                await send_fail_email(application, evaluation)
            logger.info(f"── DONE: Email sent to {application.sender_email} ✓")
        except Exception as email_err:
            logger.error(f"── EMAIL DELIVERY FAILED for {application.sender_email}: {email_err} — evaluation saved, email not sent")

    except Exception as e:
        logger.exception(f"Unhandled error processing application: {e}")


@app.post("/webhook/inbound")
async def inbound_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    background_tasks.add_task(process_application, payload)
    return JSONResponse({"status": "received"})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/test/reset")
async def test_reset():
    """Delete all test email records so the eval suite starts clean each run."""
    await database.delete_test_records()
    return {"status": "reset"}


@app.get("/applications/data")
async def list_applications_json():
    apps = await database.get_all_applications()
    return {"count": len(apps), "applications": apps}


@app.get("/applications", response_class=HTMLResponse)
async def list_applications():
    apps = await database.get_all_applications()
    cards_html = ""

    for app_row in apps:
        result = json.loads(app_row["result_json"]) if app_row.get("result_json") else None
        status = app_row["status"]
        badge_class = "badge-pass" if status == "pass" else ("badge-fail" if status == "fail" else "badge-incomplete")
        badge_label = status.upper()
        score_display = f"{app_row['score']:.1f}" if app_row.get("score") else "—"
        created_at = app_row.get("created_at", "")[:16].replace("T", " · ")

        # Response time
        response_time_display = ""
        received_at_str = app_row.get("received_at")
        created_at_str = app_row.get("created_at")
        if received_at_str and created_at_str:
            try:
                from datetime import timezone
                t1 = datetime.fromisoformat(received_at_str)
                t2 = datetime.fromisoformat(created_at_str)
                delta_seconds = int((t2 - t1).total_seconds())
                if delta_seconds < 60:
                    response_time_display = f"⚡ {delta_seconds}s response"
                else:
                    response_time_display = f"⚡ {delta_seconds // 60}m {delta_seconds % 60}s response"
            except Exception:
                pass

        # Dimension bars
        bars_html = ""
        if result and result.get("scores"):
            sc = result["scores"]
            dims = [
                ("Shipped Products", sc.get("shipped_products", 0), 30),
                ("Technical Depth", sc.get("technical_depth", 0), 25),
                ("Business Thinking", sc.get("business_thinking", 0), 20),
                ("Speed of Execution", sc.get("speed_of_execution", 0), 15),
                ("Communication", sc.get("communication_clarity", 0), 10),
            ]
            for label, val, weight in dims:
                bars_html += f"""
                <div class="bar-row">
                  <span class="bar-label">{label} ({weight}%)</span>
                  <div class="bar-track"><div class="bar-fill" style="width:{val}%"></div></div>
                  <span class="bar-score">{val}</span>
                </div>"""

        # Signals
        signals_html = ""
        if result:
            standout = result.get("standout_signals", [])
            weak = result.get("weak_signals", [])
            standout_items = "".join(f'<div class="signal-item"><span class="dot dot-green"></span>{s}</div>' for s in standout)
            weak_items = "".join(f'<div class="signal-item"><span class="dot dot-red"></span>{s}</div>' for s in weak)
            signals_html = f"""
            <div class="divider"></div>
            <div class="signals">
              <div class="signal-group"><div class="section-label">Standout</div>{standout_items}</div>
              <div class="signal-group"><div class="section-label">Weak Signals</div>{weak_items}</div>
            </div>"""

        # Technical ownership badge
        ownership_html = ""
        if result and result.get("technical_ownership"):
            ow = result["technical_ownership"]
            ow_styles = {
                "direct":   ("pill-green",  "⚙ Direct builder"),
                "indirect": ("pill-yellow", "📋 No direct build evidence"),
                "mixed":    ("pill-blue",   "⚡ Mixed ownership"),
                "unclear":  ("pill-gray",   "? Ownership unclear"),
            }
            ow_cls, ow_label = ow_styles.get(ow, ("pill-gray", ow))
            ownership_html = f'<div class="divider"></div><div class="section-label">Builder Type</div><span class="pill {ow_cls}">{ow_label}</span>'

        # Fail reason
        fail_reason_html = ""
        if result and result.get("fail_reason"):
            fail_reason_html = f"""
            <div class="divider"></div>
            <div class="section-label">Rejection Reason</div>
            <div class="fail-reason"><strong>Why rejected</strong>{result['fail_reason']}</div>"""

        # Email summary
        summary_html = ""
        if result and result.get("email_summary"):
            summary_html = f"""
            <div class="divider"></div>
            <div class="section-label">Email Sent</div>
            <blockquote>{result['email_summary']}</blockquote>"""

        # Inbound email section
        has_resume = bool(app_row.get("has_resume"))
        github_url = app_row.get("github_url") or ""
        portfolio_url = app_row.get("portfolio_url") or ""
        body_text = (app_row.get("body_text") or "").replace("<", "&lt;").replace(">", "&gt;")
        subject = app_row.get("subject") or "—"

        resume_pill = '<span class="pill pill-green">✓ Resume PDF</span>' if has_resume else '<span class="pill pill-red">✗ Resume PDF</span>'
        github_pill = f'<span class="pill pill-green">✓ GitHub</span>' if github_url else '<span class="pill pill-red">✗ GitHub</span>'
        portfolio_pill = f'<span class="pill pill-green">✓ Portfolio</span>' if portfolio_url else '<span class="pill pill-red">✗ Portfolio</span>'

        inbound_html = f"""
        <div class="divider"></div>
        <div class="inbound-toggle" onclick="toggleInbound(this)">
          <span class="chevron">▶</span> Inbound Email
        </div>
        <div class="inbound-content">
          <div class="inbound-row"><span class="inbound-key">From</span><span class="inbound-val">{app_row['sender_name']} &lt;{app_row['email']}&gt;</span></div>
          <div class="inbound-row"><span class="inbound-key">Subject</span><span class="inbound-val">{subject}</span></div>
          <div class="inbound-row"><span class="inbound-key">Detected</span><span class="inbound-val">{resume_pill}{github_pill}{portfolio_pill}</span></div>
          <div class="inbound-row"><span class="inbound-key">Body</span></div>
          <div class="inbound-body">{body_text or "—"}</div>
        </div>"""

        cards_html += f"""
        <div class="card">
          <div class="card-header">
            <div class="candidate-info">
              <h2>{app_row['sender_name']}</h2>
              <p>{app_row['email']}</p>
            </div>
            <div class="right-meta">
              <span class="badge {badge_class}">{badge_label}</span>
              <div class="score">{score_display} <span>/ 100</span></div>
              <div class="timestamp">{created_at}</div>
              {"<div class='response-time'>" + response_time_display + "</div>" if response_time_display else ""}
            </div>
          </div>
          {"<div class='section-label'>Dimension Scores</div>" + bars_html if bars_html else ""}
          {signals_html}
          {ownership_html}
          {fail_reason_html}
          {summary_html}
          {inbound_html}
        </div>"""

    if not cards_html:
        cards_html = '<div style="text-align:center;padding:60px;color:#9CA3AF;font-size:14px;">No applications yet.</div>'

    total = len(apps)
    passed = sum(1 for a in apps if a["status"] == "pass")
    failed = sum(1 for a in apps if a["status"] == "fail")
    incomplete = sum(1 for a in apps if a["status"] == "incomplete")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Candidate Evaluator — Admin</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Inter', sans-serif; background: #F7F8FA; color: #111827; }}
  header {{ background: #0F1117; padding: 20px 32px; display: flex; align-items: center; justify-content: space-between; }}
  header h1 {{ color: #fff; font-size: 16px; font-weight: 600; letter-spacing: -0.2px; }}
  .header-meta {{ display: flex; gap: 16px; }}
  .stat {{ font-size: 12px; color: #6B7280; }}
  .stat span {{ font-weight: 600; color: #9CA3AF; }}
  .container {{ max-width: 800px; margin: 32px auto; padding: 0 20px; }}
  .card {{ background: #fff; border-radius: 12px; border: 1px solid #E5E7EB; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
  .card-header {{ display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 20px; }}
  .candidate-info h2 {{ font-size: 15px; font-weight: 600; color: #111827; }}
  .candidate-info p {{ font-size: 13px; color: #6B7280; margin-top: 2px; }}
  .right-meta {{ text-align: right; }}
  .badge {{ display: inline-block; padding: 4px 12px; border-radius: 6px; font-size: 12px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; }}
  .badge-pass {{ background: #DCFCE7; color: #16A34A; }}
  .badge-fail {{ background: #FEE2E2; color: #DC2626; }}
  .badge-incomplete {{ background: #FEF3C7; color: #D97706; }}
  .score {{ font-size: 26px; font-weight: 700; color: #111827; margin-top: 6px; }}
  .score span {{ font-size: 14px; font-weight: 400; color: #9CA3AF; }}
  .timestamp {{ font-size: 12px; color: #9CA3AF; margin-top: 4px; }}
  .response-time {{ font-size: 11px; color: #6366F1; font-weight: 600; margin-top: 3px; }}
  .divider {{ height: 1px; background: #F3F4F6; margin: 18px 0; }}
  .section-label {{ font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.6px; color: #9CA3AF; margin-bottom: 12px; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 9px; }}
  .bar-label {{ font-size: 12px; color: #6B7280; width: 160px; flex-shrink: 0; }}
  .bar-track {{ flex: 1; background: #F3F4F6; border-radius: 4px; height: 6px; }}
  .bar-fill {{ height: 6px; border-radius: 4px; background: #6366F1; }}
  .bar-score {{ font-size: 12px; font-weight: 600; color: #374151; width: 28px; text-align: right; flex-shrink: 0; }}
  .signals {{ display: flex; gap: 24px; margin-top: 4px; }}
  .signal-group {{ flex: 1; }}
  .signal-item {{ display: flex; align-items: flex-start; gap: 7px; font-size: 13px; color: #374151; margin-bottom: 6px; line-height: 1.4; }}
  .dot {{ width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; margin-top: 4px; }}
  .dot-green {{ background: #22C55E; }}
  .dot-red {{ background: #EF4444; }}
  blockquote {{ background: #F9FAFB; border-left: 3px solid #E5E7EB; padding: 10px 14px; border-radius: 0 6px 6px 0; font-size: 13px; color: #6B7280; line-height: 1.6; margin-top: 4px; font-style: italic; }}
  .fail-reason {{ background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 8px; padding: 10px 14px; font-size: 13px; color: #92400E; line-height: 1.5; margin-top: 4px; }}
  .fail-reason strong {{ display: block; margin-bottom: 3px; color: #78350F; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .inbound-toggle {{ display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.6px; color: #9CA3AF; user-select: none; }}
  .inbound-toggle:hover {{ color: #6B7280; }}
  .inbound-toggle .chevron {{ transition: transform 0.2s; }}
  .inbound-toggle.open .chevron {{ transform: rotate(90deg); }}
  .inbound-content {{ display: none; margin-top: 12px; background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 14px 16px; }}
  .inbound-content.visible {{ display: block; }}
  .inbound-row {{ display: flex; gap: 10px; margin-bottom: 8px; font-size: 13px; }}
  .inbound-key {{ color: #9CA3AF; width: 80px; flex-shrink: 0; font-size: 12px; }}
  .inbound-val {{ color: #374151; line-height: 1.5; }}
  .inbound-body {{ color: #6B7280; font-size: 12px; line-height: 1.6; white-space: pre-wrap; }}
  .pill {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; margin-right: 4px; }}
  .pill-green {{ background: #DCFCE7; color: #16A34A; }}
  .pill-red {{ background: #FEE2E2; color: #DC2626; }}
  .pill-yellow {{ background: #FEF3C7; color: #D97706; }}
  .pill-blue {{ background: #DBEAFE; color: #2563EB; }}
  .pill-gray {{ background: #F3F4F6; color: #6B7280; }}
</style>
</head>
<body>
<header>
  <h1>Candidate Evaluator — Admin</h1>
  <div class="header-meta">
    <div class="stat"><span>{total}</span> total</div>
    <div class="stat"><span style="color:#4ADE80">{passed}</span> passed</div>
    <div class="stat"><span style="color:#F87171">{failed}</span> failed</div>
    <div class="stat"><span style="color:#FCD34D">{incomplete}</span> incomplete</div>
  </div>
</header>
<div class="container">
  {cards_html}
</div>
<script>
  function toggleInbound(el) {{
    el.classList.toggle('open');
    el.nextElementSibling.classList.toggle('visible');
  }}
</script>
</body>
</html>"""

    return HTMLResponse(content=html)
