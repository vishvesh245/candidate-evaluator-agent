import json
import anthropic
from models.schemas import (
    EvaluationResult,
    EvaluationScores,
    EvaluationReasoning,
    GitHubSignals,
    PortfolioSignals,
)
from prompts.evaluator_prompt import SYSTEM_PROMPT, build_evaluation_prompt
from config import settings


def _apply_github_score_floors(scores: EvaluationScores, github_signals: GitHubSignals) -> EvaluationScores:
    """
    Enforce minimum scores on technical dimensions based on verifiable GitHub signals.
    These are hard floors — GitHub follower counts and total stars are peer-validated
    and cannot be faked. Claude's text reasoning should not override them.
    """
    followers = github_signals.followers
    total_stars = github_signals.total_stars
    public_repos = github_signals.public_repos

    shipped_floor = scores.shipped_products
    technical_floor = scores.technical_depth

    # Floors require BOTH follower/star signal AND meaningful repo volume.
    # This prevents mascot/org accounts (high followers, few repos) from triggering floors.
    if (followers >= 10_000 and public_repos >= 100) or (total_stars >= 20_000 and public_repos >= 50):
        shipped_floor = max(shipped_floor, 80)
        technical_floor = max(technical_floor, 80)
    elif (followers >= 1_000 and public_repos >= 30) or (total_stars >= 5_000 and public_repos >= 20):
        shipped_floor = max(shipped_floor, 70)
        technical_floor = max(technical_floor, 70)
    elif total_stars >= 1_000 and public_repos >= 15:
        shipped_floor = max(shipped_floor, 60)
        technical_floor = max(technical_floor, 65)

    return EvaluationScores(
        shipped_products=shipped_floor,
        technical_depth=technical_floor,
        business_thinking=scores.business_thinking,
        speed_of_execution=scores.speed_of_execution,
        communication_clarity=scores.communication_clarity,
    )


def compute_weighted_total(scores: EvaluationScores) -> float:
    return round(
        scores.shipped_products * (settings.weight_shipped_products / 100)
        + scores.technical_depth * (settings.weight_technical_depth / 100)
        + scores.business_thinking * (settings.weight_business_thinking / 100)
        + scores.speed_of_execution * (settings.weight_speed_of_execution / 100)
        + scores.communication_clarity * (settings.weight_communication_clarity / 100),
        1,
    )


async def evaluate_candidate(
    candidate_name: str,
    resume_text: str,
    github_signals: GitHubSignals,
    portfolio_signals: PortfolioSignals,
) -> EvaluationResult:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    github_dict = github_signals.model_dump()
    portfolio_dict = portfolio_signals.model_dump()

    prompt = build_evaluation_prompt(
        resume_text=resume_text,
        github_signals=github_dict,
        portfolio_signals=portfolio_dict,
        candidate_name=candidate_name,
    )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if Claude wraps in them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # Extract JSON if Claude added explanation text before/after
    json_start = raw.find("{")
    json_end = raw.rfind("}") + 1
    if json_start != -1 and json_end > json_start:
        raw = raw[json_start:json_end]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned invalid JSON: {e}\nRaw output: {raw[:500]}")

    scores = EvaluationScores(**data["scores"])
    reasoning = EvaluationReasoning(**data["reasoning"])

    # Apply hard score floors based on verifiable GitHub signals.
    # Claude sometimes underscores prolific OSS authors due to name mismatches
    # or a "consumer app" bias. These floors are peer-validated minimums.
    scores = _apply_github_score_floors(scores, github_signals)  # github_signals is the GitHubSignals model

    # Recompute weighted total server-side (don't trust Claude's math)
    weighted_total = compute_weighted_total(scores)
    decision = "pass" if weighted_total >= settings.pass_threshold else "fail"

    return EvaluationResult(
        scores=scores,
        reasoning=reasoning,
        weighted_total=weighted_total,
        standout_signals=data.get("standout_signals", []),
        weak_signals=data.get("weak_signals", []),
        email_summary=data.get("email_summary", ""),
        fail_reason=data.get("fail_reason"),
        decision=decision,
        technical_ownership=data.get("technical_ownership"),
    )
