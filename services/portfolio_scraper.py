import httpx
from bs4 import BeautifulSoup
from typing import Optional
from models.schemas import PortfolioSignals

TIMEOUT = 8.0
MAX_CONTENT_CHARS = 1500

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CandidateEvaluator/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def scrape_portfolio(url: Optional[str]) -> PortfolioSignals:
    if not url:
        return PortfolioSignals(url="", accessible=False, error="No portfolio URL provided.")

    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers=HEADERS,
        ) as client:
            resp = await client.get(url)

        if resp.status_code != 200:
            return PortfolioSignals(
                url=url,
                accessible=False,
                error=f"Portfolio returned status {resp.status_code}.",
            )

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract title
        title = None
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)[:200]

        # Extract meta description
        description = None
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            description = meta_desc.get("content", "")[:300]

        # Extract visible text
        for tag in soup(["script", "style", "nav", "footer", "head"]):
            tag.decompose()

        raw_text = soup.get_text(separator=" ", strip=True)
        # Collapse whitespace
        import re
        raw_text = re.sub(r"\s+", " ", raw_text).strip()
        content_preview = raw_text[:MAX_CONTENT_CHARS]

        return PortfolioSignals(
            url=url,
            title=title,
            description=description,
            content_preview=content_preview,
            accessible=True,
        )

    except httpx.TimeoutException:
        return PortfolioSignals(
            url=url,
            accessible=False,
            error="Portfolio URL timed out. We noted this and evaluated based on other signals.",
        )
    except Exception as e:
        return PortfolioSignals(
            url=url,
            accessible=False,
            error=f"Could not access portfolio: {str(e)[:100]}",
        )
