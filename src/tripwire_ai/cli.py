"""Tripwire command-line interface.

Subcommands:
    tripwire compile  --policy POLICY.{yaml,txt}      # show what a policy expands to
    tripwire redteam  --policy POLICY.{yaml,txt}      # red-team an English policy
    tripwire attacks                                  # list registered attacks

Policy file formats:
    .yaml / .yml  — top-level list under key 'policy:' OR a top-level YAML list
    .txt / .md    — one English rule per non-empty, non-'#' line

Exit codes:
    0   — success (redteam: catch_rate == 1.0)
    1   — usage / file error
    2   — redteam ran but catch_rate < threshold (default 1.0; --min-catch-rate to relax)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import List


def _load_policy(path: Path) -> List[str]:
    text = path.read_text()
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError:
            print("error: install pyyaml or use a .txt policy file", file=sys.stderr)
            sys.exit(1)
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            data = data.get("policy") or data.get("rules") or []
        if not isinstance(data, list):
            print("error: yaml policy must be a list (or {policy: [...]})", file=sys.stderr)
            sys.exit(1)
        return [str(x).strip() for x in data if str(x).strip()]
    return [
        ln.strip() for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _cmd_compile(args: argparse.Namespace) -> int:
    from tripwire_ai import compile_policy
    rules = _load_policy(Path(args.policy))
    policy = compile_policy(rules, use_llm_fallback=args.llm_fallback)
    if args.json:
        print(json.dumps({
            "config": {k: v for k, v in policy.config.__dict__.items()},
            "rate_limiter": (policy.rate_limiter.__dict__ if policy.rate_limiter else None),
            "anomaly_detector": ({k: v for k, v in policy.anomaly_detector.__dict__.items()
                                  if not k.startswith("_")}
                                 if policy.anomaly_detector else None),
            "rules": [r.__dict__ for r in policy.rules],
            "unparsed": policy.unparsed,
        }, indent=2, default=str))
    else:
        print(policy.explain())
    return 0


async def _run_redteam(args: argparse.Namespace) -> int:
    from tripwire_ai import compile_policy, list_attacks, run_red_team
    from tripwire_ai.brain import Tripwire

    rules = _load_policy(Path(args.policy))
    policy = compile_policy(rules, use_llm_fallback=args.llm_fallback)

    with tempfile.TemporaryDirectory() as td:
        brain = Tripwire(
            cfg=replace(policy.config, shadow_mode=False),
            persist_dir=Path(td),
            rate_limiter=policy.rate_limiter,
            anomaly_detector=policy.anomaly_detector,
        )
        # Tighten detector counts so synthetic attacks fire deterministically
        if brain._anomaly:
            brain._anomaly.deny_storm_min_count = min(
                brain._anomaly.deny_storm_min_count, 10)
            brain._anomaly.new_caller_threshold = min(
                brain._anomaly.new_caller_threshold, 20)

        attacks = args.attacks.split(",") if args.attacks else None
        report = await run_red_team(brain, attacks=attacks)

        if args.json:
            print(json.dumps(report.to_dict(), indent=2, default=str))
        else:
            print(report.render())
            print()
            print(f"Catch rate: {report.catch_rate:.0%}")

        return 0 if report.catch_rate >= args.min_catch_rate else 2


def _cmd_redteam(args: argparse.Namespace) -> int:
    return asyncio.run(_run_redteam(args))


def _cmd_attacks(args: argparse.Namespace) -> int:
    from tripwire_ai import list_attacks
    for n in list_attacks():
        print(n)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tripwire", description="Tripwire CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("compile", help="compile a policy file and print the result")
    pc.add_argument("--policy", required=True, help="path to policy.yaml or policy.txt")
    pc.add_argument("--json", action="store_true", help="emit JSON instead of human text")
    pc.add_argument("--llm-fallback", action="store_true",
                    help="hand unparsed lines to Claude Haiku (requires ANTHROPIC_API_KEY)")
    pc.set_defaults(func=_cmd_compile)

    pr = sub.add_parser("redteam", help="run adversarial simulator against a policy")
    pr.add_argument("--policy", required=True)
    pr.add_argument("--attacks", default=None,
                    help="comma-separated attacks (default: all). See `tripwire attacks`")
    pr.add_argument("--min-catch-rate", type=float, default=1.0,
                    help="exit non-zero if catch rate falls below this (default 1.0)")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--llm-fallback", action="store_true")
    pr.set_defaults(func=_cmd_redteam)

    pa = sub.add_parser("attacks", help="list registered attack scenarios")
    pa.set_defaults(func=_cmd_attacks)

    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
