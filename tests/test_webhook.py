"""
Local test script — simulates Postmark inbound webhook payloads.
Run with: python tests/test_webhook.py

Requires the server to be running locally:
  uvicorn main:app --reload --port 8000
"""

import asyncio
import json
import base64
import httpx
import sys
from pathlib import Path

BASE_URL = "http://localhost:8000"
SAMPLE_DIR = Path(__file__).parent / "sample_payloads"


def load_payload(filename: str) -> dict:
    with open(SAMPLE_DIR / filename) as f:
        return json.load(f)


def attach_pdf(payload: dict, pdf_path: str) -> dict:
    """Attach a real PDF file to a payload for testing."""
    with open(pdf_path, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")
    payload["Attachments"] = [
        {
            "Name": "resume.pdf",
            "Content": content,
            "ContentType": "application/pdf",
            "ContentLength": len(content),
        }
    ]
    return payload


async def send_webhook(payload: dict, label: str):
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"From: {payload['FromFull']['Email']}")
    print(f"Subject: {payload['Subject']}")

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/webhook/inbound", json=payload)
        print(f"Response: {resp.status_code} — {resp.json()}")

    # Wait for background task to complete (GitHub + Claude can take ~20s)
    await asyncio.sleep(25)

    # Check applications list
    async with httpx.AsyncClient(timeout=15.0) as client:
        apps_resp = await client.get(f"{BASE_URL}/applications")
    apps = apps_resp.json().get("applications", [])
    email = payload["FromFull"]["Email"].lower()
    match = next((a for a in apps if a["email"] == email), None)
    if match:
        print(f"Result: status={match['status']}, score={match.get('score')}")
    else:
        print("Result: not saved to DB (incomplete/duplicate/non-application)")


async def run_tests(pdf_path: str = None):
    print(f"\nConnecting to {BASE_URL}...")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{BASE_URL}/health")
            assert resp.status_code == 200, "Server not healthy"
            print("Server is up.")
        except Exception:
            print("ERROR: Server not running. Start with: uvicorn main:app --reload --port 8000")
            sys.exit(1)

    # Test 1: Missing resume (no PDF attached)
    payload = load_payload("missing_resume.json")
    await send_webhook(payload, "Missing Resume")

    # Test 2: Missing GitHub link
    payload = load_payload("missing_github.json")
    if pdf_path:
        payload = attach_pdf(payload, pdf_path)
    await send_webhook(payload, "Missing GitHub Link")

    # Test 3: Gibberish / non-application email
    payload = load_payload("gibberish.json")
    await send_webhook(payload, "Gibberish Email")

    # Test 4: Full application (needs real PDF)
    if pdf_path:
        payload = load_payload("strong_candidate.json")
        payload = attach_pdf(payload, pdf_path)
        await send_webhook(payload, "Strong Candidate (Full Application)")

        # Test 5: Duplicate application
        await asyncio.sleep(1)
        payload = load_payload("duplicate_application.json")
        payload = attach_pdf(payload, pdf_path)
        await send_webhook(payload, "Duplicate Application")
    else:
        print("\nSkipping full application tests — no PDF path provided.")
        print("Usage: python tests/test_webhook.py /path/to/resume.pdf")

    print(f"\n{'='*60}")
    print("All tests complete. Check your email inbox for responses.")


if __name__ == "__main__":
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_tests(pdf_path))
