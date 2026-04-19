# Contributing to Tripwire

Thanks for your interest. Tripwire is a governance library — correctness and auditability matter. Contributions that improve safety, expand integrations, or strengthen the red-team library are especially welcome.

## Before you start

1. **Sign the CLA.** All contributors must sign the [Contributor License Agreement](CLA.md). This keeps the project's dual-license option open and protects you legally.
2. **Open an issue first** for anything beyond a small bugfix. Aligning on scope saves everyone time.
3. **One concern per PR.** Small, focused PRs get reviewed quickly.

## Development setup

```bash
git clone https://github.com/firekicks/tripwire-ai
cd tripwire-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[api,test]"
pytest
```

You should see **130 passing tests**.

## What we look for in PRs

- **Tests.** New features need tests. Bug fixes need a regression test.
- **Type hints.** Public API must be fully typed.
- **No breaking changes** without an issue discussion first.
- **Docs.** User-facing changes update the README and CHANGELOG.
- **Zero warnings.** Except for upstream deprecations we can't control.

## Adding a red-team attack

The high-leverage contribution path. Each attack is a subclass in `src/tripwire_ai/adversary.py`:

1. Implement your attack as an `async` callable that takes a `Tripwire` brain and returns a `ScenarioResult`.
2. Register it in the `ATTACKS` registry.
3. Add a test in `tests/test_adversary.py` that asserts your attack is blocked by the default policy.

Attacks that exercise real-world exploit classes (prompt injection via tool output, tool-chain privilege escalation, multi-turn jailbreaks, etc.) are gold.

## What we won't merge

- Code without tests.
- Features that weaken the security posture (e.g., turning off shadow mode by default, removing audit-log integrity).
- Dependencies on paid services for core functionality.
- Anything without a signed CLA.

## Reporting security issues

Do **not** open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree your contribution is licensed under the project's dual-license (AGPL-3.0 + commercial) per the [CLA](CLA.md).
