from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class Attachment(BaseModel):
    name: str
    content: str  # base64 encoded
    content_type: str
    content_length: int = 0


class ParsedApplication(BaseModel):
    sender_email: str
    sender_name: str
    subject: str
    body_text: str
    resume_attachment: Optional[Attachment] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    message_id: str = ""

    def get_missing_fields(self) -> list[str]:
        missing = []
        if not self.resume_attachment:
            missing.append("resume (PDF, Word doc, or image of your resume)")
        if not self.github_url:
            missing.append("GitHub profile link")
        return missing


class GitHubSignals(BaseModel):
    username: str
    bio: Optional[str] = None
    public_repos: int = 0
    followers: int = 0
    total_stars: int = 0  # sum of stars across all owned repos
    account_age_days: int = 0
    top_languages: list[str] = []
    recent_activity_count: int = 0  # commits/events in last 90 days
    has_pinned_repos: bool = False
    notable_repos: list[dict] = []  # name, description, stars, language — sorted by stars
    error: Optional[str] = None


class PortfolioSignals(BaseModel):
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    content_preview: Optional[str] = None  # first 1000 chars of text
    accessible: bool = False
    error: Optional[str] = None


class EvaluationScores(BaseModel):
    shipped_products: int  # 0-100
    technical_depth: int
    business_thinking: int
    speed_of_execution: int
    communication_clarity: int


class EvaluationReasoning(BaseModel):
    shipped_products: str
    technical_depth: str
    business_thinking: str
    speed_of_execution: str
    communication_clarity: str


class EvaluationResult(BaseModel):
    scores: EvaluationScores
    reasoning: EvaluationReasoning
    weighted_total: float
    standout_signals: list[str]
    weak_signals: list[str]
    email_summary: str
    fail_reason: Optional[str] = None
    decision: str  # "pass" or "fail"
    technical_ownership: Optional[str] = None  # "direct", "indirect", "mixed", "unclear"
