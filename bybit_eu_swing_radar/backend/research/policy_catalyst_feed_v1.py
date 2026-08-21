"""Point-in-time policy/liquidity catalyst feed primitives.

The layer is deterministic, label-free and context-only. It classifies official
primary-source headlines into a frozen event taxonomy. It never emits trade
direction, never changes strategy scores/gates and never infers causality from a
temporal coincidence with market movement.
"""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin

from research.policy_catalyst_sources_v1 import (
    classify_primary_policy_url,
    enabled_source_registry,
    source_registry,
)

SPEC_VERSION = "policy-catalyst-feed-v1"
MAX_FUTURE_SKEW = timedelta(minutes=5)
DEFAULT_EVENT_LOOKBACK_HOURS = 48

EVENT_CLASSES = (
    "US_CRYPTO_REGULATION",
    "TREASURY_DEBT_MANAGEMENT",
    "FED_LIQUIDITY_MARKET_OPERATION",
    "US_MONETARY_POLICY",
    "DERIVATIVES_REGULATION",
    "TRADE_POLICY",
    "SANCTIONS_FINANCIAL_GEOPOLITICS",
)

_COMMON_KEYWORDS: dict[str, tuple[str, ...]] = {
    "US_CRYPTO_REGULATION": (
        "crypto",
        "digital asset",
        "digital assets",
        "stablecoin",
        "stablecoins",
        "blockchain",
        "tokenization",
        "tokenized",
        "genius act",
        "market structure",
    ),
    "TREASURY_DEBT_MANAGEMENT": (
        "buyback",
        "buybacks",
        "refunding",
        "marketable borrowing",
        "borrowing estimates",
        "treasury borrowing advisory committee",
        "debt management",
        "liquidity support",
    ),
    "FED_LIQUIDITY_MARKET_OPERATION": (
        "standing repo",
        "repo operation",
        "repurchase agreement",
        "discount window",
        "liquidity facility",
        "liquidity facilities",
        "open market operation",
        "open market operations",
        "reserve balances",
        "primary dealer credit",
        "central bank liquidity",
    ),
    "US_MONETARY_POLICY": (
        "fomc",
        "federal funds rate",
        "interest rate decision",
        "monetary policy",
    ),
    "DERIVATIVES_REGULATION": (
        "derivatives",
        "swap",
        "swaps",
        "futures",
        "portfolio margin",
    ),
    "TRADE_POLICY": (
        "tariff",
        "tariffs",
        "reciprocal trade",
        "trade agreement",
        "trade policy",
    ),
    "SANCTIONS_FINANCIAL_GEOPOLITICS": (
        "sanction",
        "sanctions",
        "ofac",
        "terrorist finance",
        "illicit finance",
        "iran",
        "russia",
        "hizballah",
        "hezbollah",
    ),
}

_PROVIDER_ALLOWED_CLASSES: dict[str, frozenset[str]] = {
    "SEC": frozenset({"US_CRYPTO_REGULATION"}),
    "FED": frozenset({"FED_LIQUIDITY_MARKET_OPERATION", "US_MONETARY_POLICY"}),
    "TREASURY": frozenset(
        {"TREASURY_DEBT_MANAGEMENT", "US_CRYPTO_REGULATION", "SANCTIONS_FINANCIAL_GEOPOLITICS"}
    ),
    "CFTC": frozenset({"US_CRYPTO_REGULATION", "DERIVATIVES_REGULATION"}),
    "WHITE_HOUSE": frozenset(
        {"US_CRYPTO_REGULATION", "TRADE_POLICY", "SANCTIONS_FINANCIAL_GEOPOLITICS"}
    ),
    "CONGRESS": frozenset({"US_CRYPTO_REGULATION", "TRADE_POLICY"}),
}


def spec() -> dict[str, Any]:
    registry = source_registry()
    return {
        "version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "hard_gate": False,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "trade_direction": None,
        "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
        "event_classes": list(EVENT_CLASSES),
        "default_event_lookback_hours": DEFAULT_EVENT_LOOKBACK_HOURS,
        "enabled_providers": [row["provider_code"] for row in registry if row.get("enabled")],
        "unavailable_providers": [
            {
                "provider_code": row["provider_code"],
                "reason": row.get("coverage_note") or "NOT_CONFIGURED",
            }
            for row in registry
            if not row.get("enabled")
        ],
        "principles": [
            "only official HTTPS primary-source URLs are accepted",
            "published_at and first_seen_at remain distinct point-in-time timestamps",
            "provider failure is explicit and never interpreted as no policy risk",
            "headline classification is frozen and label-free",
            "temporal proximity to price movement is not causal proof",
            "no score, ranking, eligibility, shortability or execution mutation",
        ],
    }


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def classify_headline(provider_code: str, headline: str) -> list[str]:
    """Return frozen label-free event classes for one official-source headline."""
    code = str(provider_code).upper().strip()
    allowed = _PROVIDER_ALLOWED_CLASSES.get(code, frozenset())
    text = " ".join(str(headline).lower().split())
    matched: list[str] = []
    for event_class in EVENT_CLASSES:
        if event_class not in allowed:
            continue
        if any(keyword in text for keyword in _COMMON_KEYWORDS[event_class]):
            matched.append(event_class)
    return matched


def normalize_event(
    *,
    provider_code: str,
    headline: str,
    url: str,
    published_at: datetime | str | None,
    captured_at: datetime,
) -> dict[str, Any] | None:
    source = classify_primary_policy_url(url)
    if source is None or source.get("provider_code") != provider_code:
        return None
    classes = classify_headline(provider_code, headline)
    if not classes:
        return None
    captured = _as_utc(captured_at)
    published = _as_utc(published_at)
    if captured is None:
        raise ValueError("captured_at is required")
    if published is not None and published > captured + MAX_FUTURE_SKEW:
        return None
    canonical = str(url).split("#", 1)[0]
    event_id = hashlib.sha256(f"{provider_code}|{canonical}".encode("utf-8")).hexdigest()
    return {
        "event_id": event_id,
        "provider": source["provider"],
        "provider_code": provider_code,
        "authority_tier": source["authority_tier"],
        "source_family": source["source_family"],
        "headline": " ".join(str(headline).split()),
        "url": canonical,
        "published_at": published.isoformat() if published else None,
        "observed_at": captured.isoformat(),
        "event_classes": classes,
        "primary_event_class": classes[0],
        "trade_direction": None,
        "context_only": True,
        "hard_gate": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
    }


def _text(node: ET.Element, names: Iterable[str]) -> str | None:
    lowered = {name.lower() for name in names}
    for child in list(node):
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in lowered and child.text and child.text.strip():
            return child.text.strip()
    return None


def parse_rss_or_atom(xml_text: str, *, provider_code: str, captured_at: datetime) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    events: list[dict[str, Any]] = []
    for node in nodes:
        headline = _text(node, {"title"})
        url = _text(node, {"link"})
        if not url:
            for child in list(node):
                if child.tag.rsplit("}", 1)[-1].lower() == "link" and child.attrib.get("href"):
                    url = child.attrib["href"]
                    break
        published = _text(node, {"pubDate", "published", "updated", "date"})
        if not headline or not url:
            continue
        normalized = normalize_event(
            provider_code=provider_code,
            headline=headline,
            url=url,
            published_at=published,
            captured_at=captured_at,
        )
        if normalized is not None:
            events.append(normalized)
    return events


class _OfficialIndexParser(HTMLParser):
    def __init__(self, base_url: str, allowed_prefixes: list[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.allowed_prefixes = tuple(prefix.rstrip("/") for prefix in allowed_prefixes)
        self._href: str | None = None
        self._parts: list[str] = []
        self.items: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        absolute = urljoin(self.base_url, href)
        classified = classify_primary_policy_url(absolute)
        if classified is None:
            return
        path = re.sub(r"^https?://[^/]+", "", absolute).rstrip("/")
        if not any(path == prefix or path.startswith(prefix + "/") for prefix in self.allowed_prefixes):
            return
        self._href = absolute
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        headline = " ".join("".join(self._parts).split())
        if headline:
            self.items.append((headline, self._href))
        self._href = None
        self._parts = []


def parse_official_html_index(
    html_text: str,
    *,
    provider_code: str,
    base_url: str,
    allowed_prefixes: list[str],
) -> list[dict[str, str]]:
    parser = _OfficialIndexParser(base_url, allowed_prefixes)
    parser.feed(html_text)
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for headline, url in parser.items:
        if url in seen or not classify_headline(provider_code, headline):
            continue
        seen.add(url)
        rows.append({"headline": headline, "url": url})
    return rows


def extract_published_at_from_html(html_text: str) -> datetime | None:
    text = html.unescape(html_text)
    patterns = (
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'<time[^>]+datetime=["\']([^"\']+)["\']',
        r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|date|publish-date)["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:article:published_time|date|publish-date)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            parsed = _as_utc(match.group(1))
            if parsed is not None:
                return parsed
    plain = re.sub(r"<[^>]+>", " ", text)
    match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+([0-3]?\d),\s+(20\d{2})\b",
        plain,
        flags=re.IGNORECASE,
    )
    if match:
        try:
            return datetime.strptime(match.group(0), "%B %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def build_snapshot(
    events: Iterable[Mapping[str, Any]],
    *,
    source_results: Iterable[Mapping[str, Any]],
    captured_at: datetime,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    now = _as_utc(captured_at)
    if now is None:
        raise ValueError("captured_at is required")
    deduped: dict[str, dict[str, Any]] = {}
    for raw in events:
        row = dict(raw)
        event_id = str(row.get("event_id") or "")
        if event_id:
            deduped[event_id] = row
    ordered = sorted(
        deduped.values(),
        key=lambda row: (row.get("published_at") or "", row.get("provider_code") or "", row.get("event_id") or ""),
        reverse=True,
    )
    source_rows = [dict(row) for row in source_results]
    failed = [row for row in source_rows if row.get("status") != "OK"]
    data_quality = "COMPLETE" if source_rows and not failed else "PARTIAL" if ordered else "DEGRADED"
    return {
        "spec": spec(),
        "spec_version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "hard_gate": False,
        "live_strategy_mutated": False,
        "captured_at": now.isoformat(),
        "source_commit_sha": source_commit_sha,
        "data_quality": data_quality,
        "coverage": {
            "enabled_source_count": len(enabled_source_registry()),
            "attempted_source_count": len(source_rows),
            "ok_source_count": sum(1 for row in source_rows if row.get("status") == "OK"),
            "failed_source_count": len(failed),
            "event_count": len(ordered),
            "failed_sources": [row.get("provider_code") for row in failed],
        },
        "source_results": source_rows,
        "events": ordered,
        "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
        "notes": [
            "Policy events are primary-source context, not trade signals.",
            "published_at is provider time; first_seen_at is assigned only by persistence.",
            "A provider failure remains visible and is never treated as zero event risk.",
        ],
    }
