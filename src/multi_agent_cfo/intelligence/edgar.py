"""SEC EDGAR client for fetching public company data.

Provides typed access to SEC's free EDGAR API:
- Ticker → CIK lookup via the SEC company tickers file
- Company metadata (name, SIC industry code) via the submissions endpoint

SEC requires all programmatic users to identify themselves via a
User-Agent header (per https://www.sec.gov/os/accessing-edgar-data).
Update USER_AGENT below with your real contact email before any
production use — SEC can block anonymous traffic.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


# SEC asks programmatic users to identify themselves. Update before production.
USER_AGENT = "multi-agent-cfo research@example.com"

# SEC EDGAR endpoints — no API key required, rate limit is 10 req/sec.
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


@dataclass(frozen=True)
class CompanyInfo:
    """Identifying information for a public company.

    Frozen so it can flow through pipelines as a value object.
    """

    ticker: str
    cik: str  # Zero-padded to 10 digits, e.g. '0000909832'
    name: str
    sic: str
    sic_description: str


class EdgarClient:
    """Client for SEC EDGAR public company data.

    Responsibilities:
    - HTTP client lifecycle with timeout and identifying User-Agent
    - Retry with exponential backoff on transient failures
    - Process-local caching of the ticker registry (~1MB, changes infrequently)
    - Typed dataclass returns instead of raw dicts
    """

    def __init__(self, user_agent: str = USER_AGENT, timeout: float = 10.0) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout,
        )

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _get_json(self, url: str) -> dict:
        """Fetch and parse JSON, retrying on transient failures."""
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    @cache
    def _all_tickers(self) -> dict[str, dict]:
        """Fetch and index the SEC ticker → company mapping.

        SEC returns {"0": {"cik_str": ..., "ticker": ..., "title": ...}, ...}.
        We re-index by uppercase ticker for O(1) lookups.
        """
        raw = self._get_json(TICKERS_URL)
        return {entry["ticker"].upper(): entry for entry in raw.values()}

    def lookup_company(self, ticker: str) -> CompanyInfo:
        """Look up a public company by ticker symbol.

        Raises:
            ValueError: If the ticker is not in SEC's registry.
        """
        normalized = ticker.upper()
        ticker_map = self._all_tickers()
        if normalized not in ticker_map:
            raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR registry")

        entry = ticker_map[normalized]
        # SEC stores CIK as integer; URL paths need it zero-padded to 10 digits.
        cik_padded = str(entry["cik_str"]).zfill(10)

        submissions = self._get_json(SUBMISSIONS_URL.format(cik=cik_padded))

        return CompanyInfo(
            ticker=normalized,
            cik=cik_padded,
            name=entry["title"],
            sic=submissions.get("sic", ""),
            sic_description=submissions.get("sicDescription", ""),
        )

    def close(self) -> None:
        """Release HTTP client resources."""
        self._client.close()

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()