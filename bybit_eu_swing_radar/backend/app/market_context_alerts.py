"""Operational market-context warnings for existing live radar responses.

This layer is deliberately descriptive and non-gating. It makes already collected
geopolitical context visible to the live Action and pairs it with the response's
own short-timeframe relative-volume impulse. Macro-liquidity series are exposed
with their native slower frequency and are never represented as proof of an
intraday liquidity injection.
"""
from __future__ import annotations

import inspect
import json
import os
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Mapping, Sequence

import asyncpg
from fastapi import FastAPI

GEO_SPEC_VERSION = "geopolitical-event-shadow-v2"
MACRO_SPEC_VERSION = "btc-macro-cycle-etf-shadow-v1"
POLICY_SPEC_VERSION = "policy-catalyst-feed-v1"
ALERT_VERSION = "market-context-alert-v1"
GEO_MAX_AGE_SECONDS = 3 * 60 * 60
GEO_BASELINE_MIN_SNAPSHOTS = 24
POLICY_MAX_AGE_SECONDS = 30 * 60
POLICY_ACTIVE_FIRST_SEEN_SECONDS = 6 * 60 * 60
CACHE_TTL_SECONDS = 60.0

# These are operational visibility thresholds over an already normalized relative
# volume ratio. They do not change strategy scores, eligibility or execution.
VOLUME_RATIO_ELEVATED = 1.50
VOLUME_RATIO_HIGH = 2.50

TARGET_GET_PATHS = frozenset(
    {
        "/v1/scan",
        "/v1/top-candidates",
        "/v1/market-regime",
        "/v1/setup/{symbol}",
        "/v1/watchlist",
        "/v1/momentum-radar",
        "/v1/day-trade/top-candidates",
        "/v1/day-trade/scan",
        "/v1/day-trade/setup/{symbol}",
        "/v1/day-trade/audit/{symbol}",
        "/v1/day-trade/status",
        "/v1/day-trade/flow/{symbol}",
    }
)

_external_cache: dict[str, Any] | None = None
_external_cache_monotonic = 0.0
_fastapi_get_installed = False
_original_fastapi_get = None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(loaded) if isinstance(loaded, Mapping) else {}
    return {}


def _as_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _geo_metrics(snapshot: Mapping[str, Any] | None) -> dict[str, float | None]:
    context = ((snapshot or {}).get("event_context") or {}).get("material_conflict") or {}
    return {
        "event_count": _number(context.get("event_count")),
        "event_share_pct": _number(context.get("event_share_pct")),
        "sum_num_mentions": _number(context.get("sum_num_mentions")),
        "goldstein_le_minus7_count": _number(context.get("goldstein_le_minus7_count")),
    }


def _percentile_rank(history: Sequence[float], current: float | None) -> float | None:
    if current is None or not history:
        return None
    usable = [float(value) for value in history]
    if not usable:
        return None
    return 100.0 * sum(1 for value in usable if value <= current) / len(usable)


def _extract_geo_timestamp(snapshot: Mapping[str, Any] | None) -> datetime | None:
    source = (snapshot or {}).get("source_file") or {}
    return _as_utc(source.get("source_file_timestamp")) or _as_utc((snapshot or {}).get("captured_at"))


def build_geopolitical_context(
    latest: Mapping[str, Any] | None,
    history: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    latest_timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Classify event-intensity context using prior-only prospective snapshots."""
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not latest:
        return {
            "state": "UNAVAILABLE",
            "data_quality": "UNAVAILABLE",
            "mandatory_warning": False,
            "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
            "note": "No persisted geopolitical-event-v2 snapshot is available.",
        }

    source_time = latest_timestamp or _extract_geo_timestamp(latest)
    source_age = (
        max((current_time - source_time).total_seconds(), 0.0)
        if source_time is not None
        else None
    )
    metrics = _geo_metrics(latest)
    top_countries = (
        (((latest.get("event_context") or {}).get("material_conflict") or {}).get("top_action_countries"))
        or []
    )

    prior_rows: list[Mapping[str, Any]] = []
    if source_time is not None:
        lower_bound = source_time - timedelta(hours=24)
        for row in history:
            ts = _extract_geo_timestamp(row)
            if ts is None or ts >= source_time or ts < lower_bound:
                continue
            prior_rows.append(row)
    else:
        prior_rows = list(history)

    baseline_metrics = [_geo_metrics(row) for row in prior_rows]
    percentiles: dict[str, float | None] = {}
    for name, current in metrics.items():
        values = [
            float(row[name])
            for row in baseline_metrics
            if row.get(name) is not None
        ]
        percentiles[name] = _percentile_rank(values, current)

    baseline_count = len(prior_rows)
    if source_age is None or source_age > GEO_MAX_AGE_SECONDS:
        state = "STALE"
    elif baseline_count < GEO_BASELINE_MIN_SNAPSHOTS:
        state = "BASELINE_BUILDING"
    else:
        high_dimensions = sum(
            1 for value in percentiles.values() if value is not None and value >= 95.0
        )
        elevated_dimensions = sum(
            1 for value in percentiles.values() if value is not None and value >= 90.0
        )
        if high_dimensions >= 2:
            state = "HIGH"
        elif high_dimensions >= 1 or elevated_dimensions >= 2:
            state = "ELEVATED"
        else:
            state = "NORMAL"

    mandatory = state in {"HIGH", "ELEVATED"}
    note = {
        "HIGH": "Geopolitical event intensity is in the extreme tail of the prior-only 24h prospective baseline.",
        "ELEVATED": "Geopolitical event intensity is elevated versus the prior-only prospective baseline.",
        "NORMAL": "No elevated geopolitical event-intensity anomaly is detected versus the available prior baseline.",
        "BASELINE_BUILDING": "The prospective geopolitical baseline is still building; raw context is visible but anomaly classification is not yet reliable.",
        "STALE": "The latest geopolitical snapshot is stale; external-catalyst attribution is incomplete.",
    }[state]

    return {
        "state": state,
        "data_quality": latest.get("data_quality") or "UNKNOWN",
        "source": "GDELT 2.0 Event Database static stream",
        "source_timestamp": source_time.isoformat() if source_time else None,
        "source_age_seconds": round(source_age, 3) if source_age is not None else None,
        "baseline_prior_snapshots": baseline_count,
        "baseline_window_hours": 24,
        "baseline_min_snapshots": GEO_BASELINE_MIN_SNAPSHOTS,
        "metrics": metrics,
        "prior_baseline_percentiles": percentiles,
        "top_action_countries": top_countries[:5],
        "mandatory_warning": mandatory,
        "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
        "note": note,
    }


def build_macro_liquidity_context(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not snapshot:
        return {
            "state": "UNAVAILABLE",
            "intraday_causality_supported": False,
            "note": "No persisted BTC macro/cycle/ETF snapshot is available.",
        }
    macro = snapshot.get("macro") or {}
    etf = snapshot.get("etf") or {}
    return {
        "state": "AVAILABLE",
        "captured_at": snapshot.get("captured_at"),
        "fed_total_assets": macro.get("fed_total_assets"),
        "overnight_reverse_repo": macro.get("overnight_reverse_repo"),
        "btc_etf": {
            "latest_date": etf.get("latest_date") if isinstance(etf, Mapping) else None,
            "latest_daily_flow_usd": etf.get("latest_daily_flow_usd") if isinstance(etf, Mapping) else None,
            "flow_5d_usd": etf.get("flow_5d_usd") if isinstance(etf, Mapping) else None,
        },
        "intraday_causality_supported": False,
        "note": (
            "Fed balance-sheet data are weekly and reverse-repo/ETF flow data are daily; "
            "they provide macro context but cannot by themselves prove an intraday liquidity injection."
        ),
    }


def build_policy_catalyst_context(
    latest_capture: Mapping[str, Any] | None,
    recent_events: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    latest_captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Expose fresh primary-source policy events without inferring trade direction."""
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not latest_capture:
        return {
            "state": "UNAVAILABLE",
            "data_quality": "UNAVAILABLE",
            "source_age_seconds": None,
            "recent_events": [],
            "mandatory_warning": False,
            "context_only": True,
            "hard_gate": False,
            "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
            "note": "No persisted primary-source policy catalyst capture is available.",
        }

    captured_at = latest_captured_at or _as_utc(latest_capture.get("captured_at"))
    age_seconds = (
        max((current_time - captured_at).total_seconds(), 0.0)
        if captured_at is not None
        else None
    )
    active_after = current_time - timedelta(seconds=POLICY_ACTIVE_FIRST_SEEN_SECONDS)
    visible_events: list[dict[str, Any]] = []
    for raw in recent_events:
        row = dict(raw)
        first_seen = _as_utc(row.get("first_seen_at"))
        published_at = _as_utc(row.get("published_at"))
        if first_seen is None or first_seen < active_after or first_seen > current_time + timedelta(minutes=5):
            continue
        # Bootstrap guard: old provider content discovered by the first capture must
        # not become an ACTIVE catalyst merely because first_seen_at is new.
        if published_at is not None and published_at < active_after:
            continue
        visible_events.append(
            {
                "provider": row.get("provider"),
                "provider_code": row.get("provider_code"),
                "authority_tier": row.get("authority_tier"),
                "headline": row.get("headline"),
                "url": row.get("url") or row.get("source_url"),
                "primary_event_class": row.get("primary_event_class"),
                "event_classes": row.get("event_classes") or [],
                "published_at": row.get("published_at"),
                "first_seen_at": first_seen.isoformat(),
            }
        )
    visible_events.sort(key=lambda row: str(row.get("first_seen_at") or ""), reverse=True)
    if age_seconds is None or age_seconds > POLICY_MAX_AGE_SECONDS:
        state = "STALE"
    elif visible_events:
        state = "ACTIVE"
    else:
        state = "NORMAL"
    return {
        "state": state,
        "data_quality": latest_capture.get("data_quality") or "UNKNOWN",
        "captured_at": captured_at.isoformat() if captured_at else None,
        "source_age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "active_window_hours": POLICY_ACTIVE_FIRST_SEEN_SECONDS // 3600,
        "recent_events": visible_events[:5],
        "mandatory_warning": state == "ACTIVE",
        "context_only": True,
        "hard_gate": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
        "note": {
            "ACTIVE": "Fresh high-authority policy/liquidity catalyst context is available; temporal coincidence is not causal proof.",
            "NORMAL": "Primary policy sources are fresh and no newly first-seen relevant catalyst is active in the configured window.",
            "STALE": "Primary policy source capture is stale; real-time policy attribution is incomplete.",
        }[state],
    }


def _walk_metrics(value: Any, collected: dict[str, list[float]]) -> None:
    normalized = _jsonable(value)
    if isinstance(normalized, Mapping):
        for key, item in normalized.items():
            if key in collected:
                number = _number(item)
                if number is not None:
                    collected[key].append(number)
            elif key != "market_context_alerts":
                _walk_metrics(item, collected)
    elif isinstance(normalized, (list, tuple)):
        for item in normalized:
            _walk_metrics(item, collected)


def build_market_impulse_context(market_payload: Any) -> dict[str, Any]:
    collected = {
        "volume_ratio_5m": [],
        "volume_ratio_15m": [],
        "return_15m_pct": [],
        "return_1h_pct": [],
    }
    _walk_metrics(market_payload, collected)
    volume_values = collected["volume_ratio_5m"] + collected["volume_ratio_15m"]
    max_volume_ratio = max(volume_values) if volume_values else None
    max_abs_return_15m = (
        max(abs(value) for value in collected["return_15m_pct"])
        if collected["return_15m_pct"]
        else None
    )
    if max_volume_ratio is None:
        state = "UNKNOWN"
    elif max_volume_ratio >= VOLUME_RATIO_HIGH:
        state = "HIGH"
    elif max_volume_ratio >= VOLUME_RATIO_ELEVATED:
        state = "ELEVATED"
    else:
        state = "NORMAL"
    return {
        "state": state,
        "max_relative_volume_ratio_5m_15m": max_volume_ratio,
        "max_abs_return_15m_pct": max_abs_return_15m,
        "thresholds": {
            "elevated_relative_volume_ratio": VOLUME_RATIO_ELEVATED,
            "high_relative_volume_ratio": VOLUME_RATIO_HIGH,
        },
        "interpretation": (
            "Relative spot-volume impulse only. This is not proof of central-bank, stablecoin, "
            "ETF or other macro liquidity injection."
        ),
    }


async def _load_external_context_uncached() -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return {
            "geopolitical": build_geopolitical_context(None, []),
            "macro_liquidity": build_macro_liquidity_context(None),
            "policy_catalyst": build_policy_catalyst_context(None, []),
            "external_context_error": "DATABASE_URL_NOT_CONFIGURED",
        }

    connection = None
    geo_rows = []
    macro_payload = None
    policy_capture_payload = None
    policy_capture_at = None
    policy_events: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        connection = await asyncpg.connect(database_url, timeout=3)
        try:
            geo_rows = await connection.fetch(
                """
                SELECT source_file_timestamp,payload
                FROM research_geopolitical_event_v2_snapshots
                WHERE spec_version=$1
                ORDER BY source_file_timestamp DESC
                LIMIT 97
                """,
                GEO_SPEC_VERSION,
            )
        except asyncpg.UndefinedTableError:
            errors.append("GEOPOLITICAL_TABLE_UNAVAILABLE")
        try:
            macro_row = await connection.fetchrow(
                """
                SELECT payload
                FROM research_btc_macro_cycle_etf_snapshots
                WHERE spec_version=$1
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                MACRO_SPEC_VERSION,
            )
            if macro_row is not None:
                macro_payload = _decode_json(macro_row["payload"])
        except asyncpg.UndefinedTableError:
            errors.append("MACRO_LIQUIDITY_TABLE_UNAVAILABLE")
        try:
            policy_capture = await connection.fetchrow(
                """
                SELECT captured_at,payload
                FROM research_policy_catalyst_captures
                WHERE spec_version=$1
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                POLICY_SPEC_VERSION,
            )
            if policy_capture is not None:
                policy_capture_at = _as_utc(policy_capture["captured_at"])
                policy_capture_payload = _decode_json(policy_capture["payload"])
            rows = await connection.fetch(
                """
                SELECT provider_code,primary_event_class,published_at,first_seen_at,headline,source_url,payload
                FROM research_policy_catalyst_events
                WHERE spec_version=$1
                  AND first_seen_at >= NOW() - INTERVAL '24 hours'
                ORDER BY first_seen_at DESC
                LIMIT 25
                """,
                POLICY_SPEC_VERSION,
            )
            for row in rows:
                payload = _decode_json(row["payload"])
                payload.update(
                    {
                        "provider_code": row["provider_code"],
                        "primary_event_class": row["primary_event_class"],
                        "published_at": _as_utc(row["published_at"]).isoformat() if _as_utc(row["published_at"]) else None,
                        "first_seen_at": _as_utc(row["first_seen_at"]).isoformat() if _as_utc(row["first_seen_at"]) else None,
                        "headline": row["headline"],
                        "source_url": row["source_url"],
                    }
                )
                policy_events.append(payload)
        except asyncpg.UndefinedTableError:
            errors.append("POLICY_CATALYST_TABLE_UNAVAILABLE")
    except Exception as exc:
        errors.append(f"DATABASE_CONTEXT_ERROR:{type(exc).__name__}")
    finally:
        if connection is not None:
            await connection.close()

    latest_payload = None
    latest_timestamp = None
    history: list[dict[str, Any]] = []
    if geo_rows:
        latest_payload = _decode_json(geo_rows[0]["payload"])
        latest_timestamp = _as_utc(geo_rows[0]["source_file_timestamp"])
        for row in geo_rows[1:]:
            payload = _decode_json(row["payload"])
            payload.setdefault("source_file", {})
            payload["source_file"].setdefault(
                "source_file_timestamp",
                _as_utc(row["source_file_timestamp"]).isoformat()
                if _as_utc(row["source_file_timestamp"])
                else None,
            )
            history.append(payload)

    return {
        "geopolitical": build_geopolitical_context(
            latest_payload,
            history,
            latest_timestamp=latest_timestamp,
        ),
        "macro_liquidity": build_macro_liquidity_context(macro_payload),
        "policy_catalyst": build_policy_catalyst_context(
            policy_capture_payload,
            policy_events,
            latest_captured_at=policy_capture_at,
        ),
        "external_context_error": ";".join(errors) if errors else None,
    }


async def _external_context() -> dict[str, Any]:
    global _external_cache, _external_cache_monotonic
    now_monotonic = time.monotonic()
    if (
        _external_cache is not None
        and now_monotonic - _external_cache_monotonic < CACHE_TTL_SECONDS
    ):
        return deepcopy(_external_cache)
    loaded = await _load_external_context_uncached()
    _external_cache = loaded
    _external_cache_monotonic = now_monotonic
    return deepcopy(loaded)


def _overall_state(geo_state: str, impulse_state: str) -> str:
    if "HIGH" in {geo_state, impulse_state}:
        return "HIGH"
    if "ELEVATED" in {geo_state, impulse_state}:
        return "ELEVATED"
    if impulse_state == "UNKNOWN" and geo_state in {"UNAVAILABLE", "STALE"}:
        return "UNKNOWN"
    return "NORMAL"


def _headline(geo: Mapping[str, Any], impulse: Mapping[str, Any], overall: str) -> str:
    geo_state = str(geo.get("state") or "UNAVAILABLE")
    impulse_state = str(impulse.get("state") or "UNKNOWN")
    if overall in {"HIGH", "ELEVATED"} and geo_state in {"HIGH", "ELEVATED"} and impulse_state in {"HIGH", "ELEVATED"}:
        return "Piaci volumenimpulzus és emelkedett geopolitikai eseményintenzitás egyszerre látható; külső katalizátor lehetséges, okság nem bizonyított."
    if impulse_state in {"HIGH", "ELEVATED"} and geo_state in {"STALE", "UNAVAILABLE", "BASELINE_BUILDING"}:
        return "Piaci volumenimpulzus látható, de a geopolitikai attribúció nem teljes; ezt külön jelezni kell, nem szabad magyarázat nélkül hagyni."
    if impulse_state in {"HIGH", "ELEVATED"}:
        return "Emelkedett rövidtávú spot volumenimpulzus látható; ez önmagában nem bizonyít makro-likviditás injekciót."
    if geo_state in {"HIGH", "ELEVATED"}:
        return "Emelkedett geopolitikai eseményintenzitás látható; lehetséges piaci katalizátor, de az okság nincs bizonyítva."
    return "Nincs emelt market-context figyelmeztetés az elérhető adatok alapján."


async def get_market_context_alerts(market_payload: Any) -> dict[str, Any]:
    external = await _external_context()
    geo = external["geopolitical"]
    macro = external["macro_liquidity"]
    policy = external["policy_catalyst"]
    impulse = build_market_impulse_context(market_payload)
    overall = _overall_state(str(geo.get("state")), str(impulse.get("state")))
    impulse_state = str(impulse.get("state") or "UNKNOWN")
    policy_state = str(policy.get("state") or "UNAVAILABLE")
    policy_gap_during_impulse = (
        impulse_state in {"HIGH", "ELEVATED"}
        and policy_state in {"UNAVAILABLE", "STALE"}
    )
    mandatory = (
        overall in {"HIGH", "ELEVATED"}
        or policy_state == "ACTIVE"
        or policy_gap_during_impulse
    )
    if policy_state == "ACTIVE" and impulse_state in {"HIGH", "ELEVATED"}:
        headline = "Friss elsődleges policy/liquidity katalizátor és rövidtávú volumenimpulzus időben egybeesik; okság nincs bizonyítva."
    elif policy_state == "ACTIVE":
        headline = "Friss elsődleges policy/liquidity katalizátor látható; ez kontextus, nem önálló trade-jel."
    elif policy_gap_during_impulse:
        headline = "Piaci volumenimpulzus látható, de a valós idejű policy/liquidity attribúció nem teljes és friss forráscapture nélkül nem ellenőrizhető."
    else:
        headline = _headline(geo, impulse, overall)
    return {
        "version": ALERT_VERSION,
        "context_only": True,
        "hard_gate": False,
        "score_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "warning_level": overall,
        "mandatory_user_warning": mandatory,
        "headline": headline,
        "market_impulse": impulse,
        "geopolitical": geo,
        "macro_liquidity": macro,
        "policy_catalyst": policy,
        "causal_attribution": "UNCONFIRMED_UNLESS_INDEPENDENTLY_CORROBORATED",
        "external_context_error": external.get("external_context_error"),
        "reporting_policy": {
            "must_surface_elevated_or_high": True,
            "must_not_be_suppressed_by_trade_score": True,
            "if_large_move_and_external_context_missing": "explicitly report attribution as not checkable",
            "must_surface_fresh_primary_policy_events": True,
            "must_surface_policy_gap_during_elevated_impulse": True,
            "language_rule": "distinguish observed volume/flow impulse from claimed macro liquidity injection",
        },
    }


async def enrich_market_response(result: Any) -> Any:
    if result is None:
        return None
    normalized = _jsonable(result)
    if not isinstance(normalized, Mapping):
        return result
    payload = dict(normalized)
    payload["market_context_alerts"] = await get_market_context_alerts(payload)
    return payload


def install_market_context_route_enrichment() -> None:
    """Wrap selected existing GET routes before app.main registers them.

    This keeps the public Action operations unchanged while ensuring their normal
    responses contain the warning layer. The wrapper is fail-soft and never
    changes core strategy calculations or cached records.
    """
    global _fastapi_get_installed, _original_fastapi_get
    if _fastapi_get_installed:
        return
    original_get = FastAPI.get
    _original_fastapi_get = original_get

    @wraps(original_get)
    def patched_get(self: FastAPI, path: str, *args: Any, **kwargs: Any):
        register = original_get(self, path, *args, **kwargs)
        if path not in TARGET_GET_PATHS:
            return register

        def decorator(endpoint):
            @wraps(endpoint)
            async def enriched_endpoint(*endpoint_args: Any, **endpoint_kwargs: Any):
                value = endpoint(*endpoint_args, **endpoint_kwargs)
                if inspect.isawaitable(value):
                    value = await value
                try:
                    return await enrich_market_response(value)
                except Exception as exc:
                    normalized = _jsonable(value)
                    if not isinstance(normalized, Mapping):
                        return value
                    fallback = dict(normalized)
                    fallback["market_context_alerts"] = {
                        "version": ALERT_VERSION,
                        "context_only": True,
                        "hard_gate": False,
                        "warning_level": "UNAVAILABLE",
                        "mandatory_user_warning": False,
                        "external_context_error": f"ENRICHMENT_ERROR:{type(exc).__name__}",
                        "reporting_policy": {
                            "if_large_move_and_external_context_missing": "explicitly report attribution as not checkable"
                        },
                    }
                    return fallback

            return register(enriched_endpoint)

        return decorator

    FastAPI.get = patched_get
    _fastapi_get_installed = True
