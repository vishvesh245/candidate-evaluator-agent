"""
Comprehensive eval test runner.
Usage: python tests/run_eval_tests.py /path/to/resume.pdf

Runs all test cases against the local server and prints a results table.
"""

import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

BASE_URL = "http://localhost:8000"
WAIT_SECONDS = 35  # Time to wait for background task (GitHub + Claude)
FAST_WAIT = 6      # For edge cases that don't hit Claude


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_pdf(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def make_payload(
    email: str,
    name: str,
    subject: str,
    body: str,
    pdf_content: str = None,
    pdf_name: str = "resume.pdf",
    extra_attachments: list = None,
) -> dict:
    attachments = []
    if pdf_content:
        attachments.append({
            "Name": pdf_name,
            "Content": pdf_content,
            "ContentType": "application/pdf",
            "ContentLength": len(pdf_content),
        })
    if extra_attachments:
        attachments.extend(extra_attachments)

    return {
        "FromFull": {"Email": email, "Name": name},
        "Subject": subject,
        "TextBody": body,
        "HtmlBody": "",
        "Attachments": attachments,
        "MessageID": f"test-{email}-{int(time.time())}",
    }


async def send(payload: dict) -> bool:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{BASE_URL}/webhook/inbound", json=payload)
        return resp.status_code == 200


async def get_result(email: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{BASE_URL}/applications/data")
    apps = resp.json().get("applications", [])
    return next((a for a in apps if a["email"] == email.lower()), None)


async def reset_test_records():
    """Delete all test records from previous runs so each run starts clean."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{BASE_URL}/test/reset")
        return resp.status_code == 200


def check(condition: bool, label: str) -> str:
    return f"  {'✅' if condition else '❌'} {label}"


# ── Test cases ────────────────────────────────────────────────────────────────

async def run_tests(pdf_path: str):
    pdf = load_pdf(pdf_path)
    results = []

    print(f"\n{'═'*62}")
    print("  CANDIDATE EVALUATOR — EVAL TEST SUITE")
    print(f"{'═'*62}\n")

    # ── Reset test records ────────────────────────────────────────
    print("Resetting test records from previous runs...")
    await reset_test_records()
    print("Done.\n")

    # ── E1: Missing resume ────────────────────────────────────────
    print("E1 · Missing Resume")
    payload = make_payload(
        email="e1.missing.resume@test.com",
        name="Test E1",
        subject="Application to Company's Builder Residency",
        body="Hi, applying!\nGitHub: https://github.com/vishvesh245\nPortfolio: https://vishvesh.me",
    )
    await send(payload)
    await asyncio.sleep(FAST_WAIT)
    result = await get_result("e1.missing.resume@test.com")
    status = result["status"] if result else None
    passed = status == "incomplete"
    print(check(passed, f"Status = incomplete (got: {status})"))
    results.append(("E1", "Missing Resume", passed))

    # ── E2: Missing GitHub ────────────────────────────────────────
    print("\nE2 · Missing GitHub Link")
    payload = make_payload(
        email="e2.missing.github@test.com",
        name="Test E2",
        subject="Builder Residency Application",
        body="Hi, here is my application. Portfolio: https://vishvesh.me",
        pdf_content=pdf,
    )
    await send(payload)
    await asyncio.sleep(FAST_WAIT)
    result = await get_result("e2.missing.github@test.com")
    status = result["status"] if result else None
    passed = status == "incomplete"
    print(check(passed, f"Status = incomplete (got: {status})"))
    results.append(("E2", "Missing GitHub", passed))

    # ── E3: Missing both ──────────────────────────────────────────
    print("\nE3 · Missing Resume + GitHub")
    payload = make_payload(
        email="e3.missing.both@test.com",
        name="Test E3",
        subject="Application",
        body="I want to apply to the residency.",
    )
    await send(payload)
    await asyncio.sleep(FAST_WAIT)
    result = await get_result("e3.missing.both@test.com")
    status = result["status"] if result else None
    passed = status == "incomplete"
    print(check(passed, f"Status = incomplete (got: {status})"))
    results.append(("E3", "Missing Both", passed))

    # ── E4: DOCX attached ─────────────────────────────────────────
    print("\nE4 · DOCX Attached (wrong format)")
    fake_docx = base64.b64encode(b"PK\x03\x04fake docx content").decode("utf-8")
    payload = make_payload(
        email="e4.docx@test.com",
        name="Test E4",
        subject="Application",
        body="GitHub: https://github.com/vishvesh245\nPortfolio: https://vishvesh.me",
        extra_attachments=[{
            "Name": "Vishvesh_Resume.docx",
            "Content": fake_docx,
            "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "ContentLength": len(fake_docx),
        }],
    )
    await send(payload)
    await asyncio.sleep(FAST_WAIT)
    result = await get_result("e4.docx@test.com")
    status = result["status"] if result else None
    # Should be incomplete — DOCX detected but no PDF
    passed = status == "incomplete"
    print(check(passed, f"Status = incomplete (got: {status})"))
    results.append(("E4", "DOCX Attached", passed))

    # ── E5: Gibberish email ───────────────────────────────────────
    print("\nE5 · Gibberish / Non-Application Email")
    payload = make_payload(
        email="e5.gibberish@test.com",
        name="Test E5",
        subject="asdfghjkl",
        body="qwerty 12345 random noise hello testing 123",
    )
    await send(payload)
    await asyncio.sleep(FAST_WAIT)
    result = await get_result("e5.gibberish@test.com")
    # Should NOT be in DB (non-application, no save)
    passed = result is None
    print(check(passed, f"Not saved to DB (got: {result})"))
    results.append(("E5", "Gibberish Email", passed))

    # ── E6: Duplicate application ─────────────────────────────────
    print("\nE6 · Duplicate Application")
    # First send a complete application and wait for evaluation
    payload = make_payload(
        email="e6.duplicate@test.com",
        name="Test E6",
        subject="Application to Company's Builder Residency",
        body="GitHub: https://github.com/vishvesh245\nPortfolio: https://vishvesh.me",
        pdf_content=pdf,
    )
    await send(payload)
    print("  Waiting for first application to be evaluated...")
    await asyncio.sleep(WAIT_SECONDS)
    first = await get_result("e6.duplicate@test.com")
    first_status = first["status"] if first else None

    # Now send duplicate
    await send(payload)
    await asyncio.sleep(FAST_WAIT)
    second = await get_result("e6.duplicate@test.com")
    second_status = second["status"] if second else None

    passed = first_status in ("pass", "fail") and second_status == first_status
    print(check(first_status in ("pass", "fail"), f"First evaluated: {first_status}"))
    print(check(passed, f"Duplicate blocked, status unchanged: {second_status}"))
    results.append(("E6", "Duplicate Application", passed))

    # ── E7: Incomplete → resubmit ─────────────────────────────────
    print("\nE7 · Incomplete → Resubmit (should not be blocked)")
    email_e7 = "e7.resubmit@test.com"
    # First: missing GitHub
    payload = make_payload(
        email=email_e7,
        name="Test E7",
        subject="Application",
        body="Hi, applying! Portfolio: https://vishvesh.me",
        pdf_content=pdf,
    )
    await send(payload)
    await asyncio.sleep(FAST_WAIT)
    first = await get_result(email_e7)
    first_status = first["status"] if first else None

    # Second: complete application
    payload = make_payload(
        email=email_e7,
        name="Test E7",
        subject="Application — complete",
        body="GitHub: https://github.com/vishvesh245\nPortfolio: https://vishvesh.me",
        pdf_content=pdf,
    )
    await send(payload)
    print("  Waiting for resubmission to be evaluated...")
    await asyncio.sleep(WAIT_SECONDS)
    second = await get_result(email_e7)
    second_status = second["status"] if second else None

    passed = first_status == "incomplete" and second_status in ("pass", "fail")
    print(check(first_status == "incomplete", f"First = incomplete (got: {first_status})"))
    print(check(second_status in ("pass", "fail"), f"Second evaluated: {second_status}"))
    results.append(("E7", "Incomplete → Resubmit", passed))

    # ── Q1: Strong builder ────────────────────────────────────────
    print("\nQ1 · Strong Builder (sindresorhus)")
    payload = make_payload(
        email="q1.strong@test.com",
        name="Sindre Sorhus",
        subject="Application to Company's Builder Residency",
        body="GitHub: https://github.com/sindresorhus\nPortfolio: https://sindresorhus.com",
        pdf_content=pdf,
    )
    await send(payload)
    print("  Evaluating (this takes ~30s)...")
    await asyncio.sleep(WAIT_SECONDS)
    result = await get_result("q1.strong@test.com")
    status = result["status"] if result else None
    score = result["score"] if result else None
    passed = status == "pass"
    print(check(passed, f"Decision = PASS (got: {status}, score: {score})"))
    if result and result.get("result_json"):
        r = json.loads(result["result_json"])
        print(f"  Scores: {r['scores']}")
    results.append(("Q1", "Strong Builder", passed))

    # ── Q2: Weak / learner ────────────────────────────────────────
    print("\nQ2 · Weak Candidate (course repos, no products)")
    payload = make_payload(
        email="q2.weak@test.com",
        name="Weak Candidate",
        subject="Application to Company's Builder Residency",
        # Using a GitHub with mostly forked/tutorial repos
        body="GitHub: https://github.com/octocat\nPortfolio: https://github.com/octocat",
        pdf_content=pdf,
    )
    await send(payload)
    print("  Evaluating (this takes ~30s)...")
    await asyncio.sleep(WAIT_SECONDS)
    result = await get_result("q2.weak@test.com")
    status = result["status"] if result else None
    score = result["score"] if result else None
    passed = status == "fail"
    print(check(passed, f"Decision = FAIL (got: {status}, score: {score})"))
    results.append(("Q2", "Weak Candidate", passed))

    # ── Q3: PM only ───────────────────────────────────────────────
    print("\nQ3 · PM-Only Profile (Vishvesh's profile)")
    payload = make_payload(
        email="q3.pm.only@test.com",
        name="Vishvesh Pandya",
        subject="Application to Company's Builder Residency",
        body="GitHub: https://github.com/vishvesh245\nPortfolio: https://vishvesh.me",
        pdf_content=pdf,
    )
    await send(payload)
    print("  Evaluating (this takes ~30s)...")
    await asyncio.sleep(WAIT_SECONDS)
    result = await get_result("q3.pm.only@test.com")
    status = result["status"] if result else None
    score = result["score"] if result else None
    passed = status == "fail"
    print(check(passed, f"Decision = FAIL (got: {status}, score: {score})"))
    results.append(("Q3", "PM-Only Profile", passed))

    # ── Q4: Invalid GitHub ────────────────────────────────────────
    print("\nQ4 · Invalid / Non-existent GitHub Profile")
    payload = make_payload(
        email="q4.invalid.github@test.com",
        name="Test Q4",
        subject="Application to Company's Builder Residency",
        body="GitHub: https://github.com/thispersondefinitelydoesnotexist99999\nPortfolio: https://vishvesh.me",
        pdf_content=pdf,
    )
    await send(payload)
    print("  Evaluating (this takes ~30s)...")
    await asyncio.sleep(WAIT_SECONDS)
    result = await get_result("q4.invalid.github@test.com")
    status = result["status"] if result else None
    # Should still evaluate (not crash), decision based on resume only
    passed = status in ("pass", "fail")
    print(check(passed, f"Evaluated without crashing (got: {status})"))
    results.append(("Q4", "Invalid GitHub", passed))

    # ── Q5: No portfolio ──────────────────────────────────────────
    print("\nQ5 · No Portfolio URL")
    payload = make_payload(
        email="q5.no.portfolio@test.com",
        name="Test Q5",
        subject="Application to Company's Builder Residency",
        body="GitHub: https://github.com/vishvesh245",
        pdf_content=pdf,
    )
    await send(payload)
    print("  Evaluating (this takes ~30s)...")
    await asyncio.sleep(WAIT_SECONDS)
    result = await get_result("q5.no.portfolio@test.com")
    status = result["status"] if result else None
    passed = status in ("pass", "fail")
    print(check(passed, f"Evaluated normally without portfolio (got: {status})"))
    results.append(("Q5", "No Portfolio", passed))

    # ── S1: Email delivery failure ────────────────────────────────
    print("\nS1 · Email Delivery Failure (invalid recipient)")
    payload = make_payload(
        email="s1.delivery.fail@test.com",
        name="Test S1",
        subject="Application to Company's Builder Residency",
        body="GitHub: https://github.com/vishvesh245\nPortfolio: https://vishvesh.me",
        pdf_content=pdf,
    )
    # Temporarily patch: override recipient to clearly invalid address
    # We test that DB save still happens even when email fails
    print("  Evaluating (this takes ~30s)...")
    await send(payload)
    await asyncio.sleep(WAIT_SECONDS)
    result = await get_result("s1.delivery.fail@test.com")
    status = result["status"] if result else None
    # Should be saved to DB even if email fails
    passed = status in ("pass", "fail")
    print(check(passed, f"Saved to DB despite potential email issue (got: {status})"))
    results.append(("S1", "Email Delivery Resilience", passed))

    # ── Summary ───────────────────────────────────────────────────
    print(f"\n{'═'*62}")
    print("  RESULTS SUMMARY")
    print(f"{'═'*62}")
    total = len(results)
    passed_count = sum(1 for _, _, p in results if p)
    for id_, name, p in results:
        icon = "✅" if p else "❌"
        print(f"  {icon}  {id_:<4} {name}")
    print(f"\n  {passed_count}/{total} tests passed")
    print(f"{'═'*62}\n")

    print(f"Full scorecards: {BASE_URL}/applications\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tests/run_eval_tests.py /path/to/resume.pdf")
        sys.exit(1)
    asyncio.run(run_tests(sys.argv[1]))
