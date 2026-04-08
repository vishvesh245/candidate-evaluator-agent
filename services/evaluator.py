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

    data = json.loads(raw)

    scores = EvaluationScores(**data["scores"])
    reasoning = EvaluationReasoning(**data["reasoning"])

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
    )
