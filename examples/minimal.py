"""Minimal Rein example — the shortest runnable demo.

Run:
    pip install -e .
    python3 examples/minimal.py
"""
import asyncio

from rein_ai import Rein, ReinConfig


async def main() -> None:
    brain = Rein(cfg=ReinConfig.from_env())
    await brain.start()
    try:
        decision = brain.gate(source="agent", series="send_email")
        print(f"allowed={decision.allowed} reason={decision.reason}")
    finally:
        await brain.shutdown()
    print("shutdown clean")


if __name__ == "__main__":
    asyncio.run(main())
