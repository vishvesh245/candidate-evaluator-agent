import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.responses import JSONResponse

import database
from services.email_parser import parse_inbound_email
from services.resume_parser import parse_resume
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
    """Basic heuristic — does this email mention anything application-related?"""
    keywords = ["apply", "application", "resume", "github", "portfolio", "residency", "candidate"]
    text_lower = body_text.lower()
    return any(kw in text_lower for kw in keywords)


async def process_application(payload: dict):
    application = None
    try:
        application = parse_inbound_email(payload)
        logger.info(f"Processing application from {application.sender_email}")

        # 1. Duplicate check
        if await database.is_duplicate(application.sender_email):
            logger.info(f"Duplicate application from {application.sender_email}")
            await send_duplicate_email(application)
            return

        # 2. Non-application email check
        if not looks_like_application(application.body_text) and not application.resume_attachment:
            logger.info(f"Non-application email from {application.sender_email}")
            await send_non_application_email(application)
            return

        # 3. Missing fields check
        missing = application.get_missing_fields()
        if missing:
            logger.info(f"Incomplete application from {application.sender_email}: missing {missing}")
            await send_incomplete_email(application, missing)
            await database.save_application(
                email=application.sender_email,
                sender_name=application.sender_name,
                status="incomplete",
            )
            return

        # 4. Parse resume
        resume_text, resume_error = parse_resume(application.resume_attachment)
        if resume_error:
            logger.warning(f"Resume parse error for {application.sender_email}: {resume_error}")
            await send_incomplete_email(application, [resume_error])
            return

        logger.info(f"Resume parsed: {len(resume_text)} chars")

        # 5. Extract GitHub + portfolio signals in parallel
        github_signals, portfolio_signals = await asyncio.gather(
            extract_github_signals(application.github_url),
            scrape_portfolio(application.portfolio_url),
        )

        logger.info(
            f"GitHub: {github_signals.public_repos} repos, {github_signals.recent_activity_count} recent events. "
            f"Portfolio: accessible={portfolio_signals.accessible}"
        )

        # 6. Evaluate
        evaluation = await evaluate_candidate(
            candidate_name=application.sender_name,
            resume_text=resume_text,
            github_signals=github_signals,
            portfolio_signals=portfolio_signals,
        )

        logger.info(
            f"Evaluation complete for {application.sender_email}: "
            f"score={evaluation.weighted_total}, decision={evaluation.decision}"
        )

        # 7. Send response and save
        if evaluation.decision == "pass":
            await send_pass_email(application, evaluation)
        else:
            await send_fail_email(application, evaluation)

        await database.save_application(
            email=application.sender_email,
            sender_name=application.sender_name,
            status=evaluation.decision,
            score=evaluation.weighted_total,
            result=evaluation.model_dump(),
        )

        logger.info(f"Done: {application.sender_email} → {evaluation.decision} ({evaluation.weighted_total})")

    except Exception as e:
        logger.exception(f"Unhandled error processing application: {e}")
        # Don't send any email on unexpected errors — better to stay silent than send a broken response


@app.post("/webhook/inbound")
async def inbound_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Postmark inbound webhook endpoint.
    Returns 200 immediately and processes asynchronously.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    background_tasks.add_task(process_application, payload)
    return JSONResponse({"status": "received"})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/applications")
async def list_applications():
    """Admin endpoint to view all processed applications."""
    apps = await database.get_all_applications()
    return {"count": len(apps), "applications": apps}
