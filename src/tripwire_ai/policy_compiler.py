"""Natural-language policy compiler.

Converts English policy descriptions into a structured `CompiledPolicy` —
a bundle of TripwireConfig overrides, rate-limit settings, anomaly thresholds,
and named custom predicates that callers can mount on a Tripwire.

Two paths:
  1. **Rule-based parser** (always available, zero deps): regex patterns
     cover the most common operator intents — rate caps, cost caps, loss
     halts, deny-rate alarms, debounce windows.
  2. **LLM fallback** (optional, requires `anthropic`): when the rule-based
     parser does not match, the description is handed to Claude Haiku
     constrained to emit the same JSON schema. Validated before use.

Design intent: an operator can write 'block any caller exceeding 10 calls
per second' in their config file, and Tripwire produces an enforceable
policy without anyone touching Python.

Usage:
    from tripwire_ai.policy_compiler import compile_policy

    policy = compile_policy([
        "Cap each caller at 5 requests per second with bursts of 10",
        "Halt the portfolio when losses exceed 5 percent",
        "Alert when deny rate exceeds 80 percent over a 60 second window",
    ])
    brain = policy.build_brain(persist_dir="/tmp/state")
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from tripwire_ai.anomaly import AnomalyDetector
from tripwire_ai.config import TripwireConfig
from tripwire_ai.rate_limit import TokenBucketLimiter


_NUM = r"(\d+(?:\.\d+)?)"


@dataclass
class PolicyRule:
    source: str
    parser: str
    target: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CompiledPolicy:
    config: TripwireConfig
    rate_limiter: Optional[TokenBucketLimiter]
    anomaly_detector: Optional[AnomalyDetector]
    rules: List[PolicyRule]
    unparsed: List[str]

    def build_brain(self, persist_dir: Optional[Path] = None):
        from tripwire_ai.brain import Tripwire
        return Tripwire(
            cfg=self.config,
            persist_dir=persist_dir,
            rate_limiter=self.rate_limiter,
            anomaly_detector=self.anomaly_detector,
        )

    def explain(self) -> str:
        lines = ["Compiled policy:"]
        for r in self.rules:
            lines.append(f"  - [{r.target}] {r.source!r} -> {r.params}")
        if self.unparsed:
            lines.append("Unparsed (passed to LLM if available, else discarded):")
            for u in self.unparsed:
                lines.append(f"  - {u!r}")
        return "\n".join(lines)


_RATE_RE = re.compile(
    rf"(?:cap|limit|throttle|allow).{{0,40}}?{_NUM}\s*(?:requests?|calls?|reqs?|rps)"
    rf"(?:.{{0,20}}?per\s*second)?(?:.{{0,40}}?bursts?\s*(?:of|=)\s*{_NUM})?",
    re.IGNORECASE,
)

_GLOBAL_RATE_RE = re.compile(
    rf"(?:global|process|system).{{0,30}}?{_NUM}\s*(?:requests?|calls?|rps)",
    re.IGNORECASE,
)

_PORTFOLIO_LOSS_RE = re.compile(
    rf"(?:halt|stop|kill|nuke).{{0,40}}?(?:portfolio|trading|all)?.{{0,40}}?"
    rf"(?:loss(?:es)?|drawdown).{{0,20}}?{_NUM}\s*(?:%|percent)",
    re.IGNORECASE,
)

_BALANCE_FLOOR_RE = re.compile(
    rf"(?:halt|stop|floor).{{0,40}}?(?:balance).{{0,20}}?(?:below|under|<)\s*\$?{_NUM}",
    re.IGNORECASE,
)

_DENY_STORM_RE = re.compile(
    rf"(?:alert|warn|alarm).{{0,40}}?deny\s*rate.{{0,40}}?{_NUM}\s*(?:%|percent)"
    rf"(?:.{{0,40}}?{_NUM}\s*second)?",
    re.IGNORECASE,
)

_RUNAWAY_RE = re.compile(
    rf"(?:detect|alert|flag).{{0,40}}?runaway.{{0,40}}?{_NUM}\s*x.{{0,30}}?baseline",
    re.IGNORECASE,
)

_DEBOUNCE_RE = re.compile(
    rf"(?:debounce|cooldown|stabilize).{{0,40}}?{_NUM}\s*second",
    re.IGNORECASE,
)

_KILL_SAMPLES_RE = re.compile(
    rf"(?:require|need|wait\s*for).{{0,30}}?{_NUM}\s*(?:samples?|trades?|observations?)"
    rf".{{0,30}}?(?:before|prior\s*to).{{0,20}}?(?:kill|halt|disable)",
    re.IGNORECASE,
)


def _parse_rule(text: str) -> Optional[PolicyRule]:
    s = text.strip()
    if m := _GLOBAL_RATE_RE.search(s):
        return PolicyRule(source=text, parser="rule", target="global_rate",
                          params={"global_rate": float(m.group(1))})
    if m := _RATE_RE.search(s):
        params = {"rate_per_key": float(m.group(1))}
        if m.group(2):
            params["burst_per_key"] = float(m.group(2))
        return PolicyRule(source=text, parser="rule", target="rate_per_key", params=params)
    if m := _PORTFOLIO_LOSS_RE.search(s):
        pct = -float(m.group(1)) / 100.0
        return PolicyRule(source=text, parser="rule", target="portfolio_floor_pct",
                          params={"portfolio_floor_pct": pct})
    if m := _BALANCE_FLOOR_RE.search(s):
        return PolicyRule(source=text, parser="rule", target="balance_floor_usd",
                          params={"balance_floor_usd": float(m.group(1))})
    if m := _DENY_STORM_RE.search(s):
        rate = float(m.group(1)) / 100.0
        params = {"deny_storm_rate": rate}
        if m.group(2):
            params["window_s"] = float(m.group(2))
        return PolicyRule(source=text, parser="rule", target="deny_storm", params=params)
    if m := _RUNAWAY_RE.search(s):
        return PolicyRule(source=text, parser="rule", target="runaway",
                          params={"runaway_multiplier": float(m.group(1))})
    if m := _DEBOUNCE_RE.search(s):
        return PolicyRule(source=text, parser="rule", target="debounce_seconds",
                          params={"debounce_seconds": float(m.group(1))})
    if m := _KILL_SAMPLES_RE.search(s):
        return PolicyRule(source=text, parser="rule", target="min_samples_for_kill",
                          params={"min_samples_for_kill": int(float(m.group(1)))})
    return None


def _llm_parse(text: str, model: str = "claude-haiku-4-5-20251001") -> Optional[PolicyRule]:
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    schema = {
        "rate_per_key|burst_per_key|global_rate": "TokenBucketLimiter knobs (calls/sec, bucket size)",
        "portfolio_floor_pct|portfolio_nuclear_pct": "negative float, eg -0.05 for 5% loss",
        "balance_floor_usd": "absolute USD",
        "debounce_seconds|decay_hours": "TripwireConfig timing knobs",
        "min_samples_for_kill|min_samples_for_green": "cold-start sample counts",
        "deny_storm_rate|runaway_multiplier|new_caller_threshold|window_s": "AnomalyDetector knobs",
    }
    prompt = (
        "Convert this operator policy to a JSON object with keys 'target' (one of: "
        "rate_per_key, global_rate, portfolio_floor_pct, balance_floor_usd, deny_storm, "
        "runaway, debounce_seconds, min_samples_for_kill) and 'params' (object of "
        "numeric values matching the target). Schema reference: "
        f"{json.dumps(schema)}. Policy: {text!r}. Return only the JSON object."
    )
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model, max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        raw = raw[raw.find("{") : raw.rfind("}") + 1]
        data = json.loads(raw)
        target = data.get("target")
        params = data.get("params") or {}
        if not isinstance(target, str) or not isinstance(params, dict):
            return None
        return PolicyRule(source=text, parser="llm", target=target, params=params)
    except Exception:
        return None


def compile_policy(
    descriptions: List[str],
    base: Optional[TripwireConfig] = None,
    use_llm_fallback: bool = True,
) -> CompiledPolicy:
    """Compile a list of English policy descriptions to a CompiledPolicy."""
    cfg_kwargs: Dict[str, Any] = {}
    rate_kwargs: Dict[str, Any] = {}
    anom_kwargs: Dict[str, Any] = {}
    rules: List[PolicyRule] = []
    unparsed: List[str] = []

    for desc in descriptions:
        rule = _parse_rule(desc)
        if rule is None and use_llm_fallback:
            rule = _llm_parse(desc)
        if rule is None:
            unparsed.append(desc)
            continue
        rules.append(rule)
        if rule.target in {"rate_per_key", "global_rate"}:
            rate_kwargs.update(rule.params)
        elif rule.target in {"deny_storm", "runaway"}:
            anom_kwargs.update(rule.params)
        else:
            cfg_kwargs.update(rule.params)

    cfg = base or TripwireConfig.from_env()
    if cfg_kwargs:
        cfg = replace(cfg, **{k: v for k, v in cfg_kwargs.items() if hasattr(cfg, k)})

    limiter = TokenBucketLimiter(**rate_kwargs) if rate_kwargs else None
    detector = AnomalyDetector(**anom_kwargs) if anom_kwargs else None

    return CompiledPolicy(
        config=cfg,
        rate_limiter=limiter,
        anomaly_detector=detector,
        rules=rules,
        unparsed=unparsed,
    )
