# Origin: How a broken trading bot became Rein-AI

I'm not a traditional founder. I spent ten years at sea as a merchant mariner before I wrote a line of code.

Along the way, I built **The FreeGame Podcast** to 13K+ followers and 2M+ views across platforms. Audience-building turned out to be the unexpected second skill of going independent.

In 2024 I started teaching myself Python. By early 2025 I was building **Predbot**, an autonomous agent that would trade BTC prediction markets on Kalshi. Let Claude read signals. Score confidence. Execute through the Kalshi API with RSA-signed auth. A WebSocket dashboard to watch it run.

It worked. Technically.

What it didn't do was make money.

What it *did* do was teach me something I didn't expect. Every week I'd find the bot doing something I hadn't imagined. Placing trades on dead-book markets with no counterparty. Holding positions past expiration because the exit manager returned a 400 and kept retrying. Chasing stale signals after the model had drifted. Each time I'd patch it: a filter, a timeout, a sanity check.

After a few months I realized I wasn't writing a trading bot anymore. I was writing the guardrails *around* the trading bot. The governance layer had become the product.

Then I looked at what's actually available for autonomous agents and found: almost nothing. Content-level guardrails, sure: dozens of libraries that check whether an LLM output is toxic or off-policy. But nothing at the *action layer* that said: before you actually execute this trade, this tool call, this email, this API hit, let me check.

That gap is Rein-AI.

It's the governance layer I needed but didn't have. A drop-in runtime governor that wraps every action an autonomous agent tries to take. Natural-language policies. Adversarial red-team testing. Regime-sliced scoring so the system catches behavior drift the moment it starts, not after it's already cost you.

Predbot's failure gave me twelve months of evidence about what an autonomous agent actually does when no one's watching. Every bug became a test case. Every runaway trade became a policy rule. Rein-AI ships with **135 tests passing, 1.7µs gate latency, and twelve documented runaway trades caught in production in week one**. All from the bot that didn't work.

I'm releasing it AGPL-3.0 because this layer should be something everyone can audit. There's a Pro tier for teams who need the hard parts (orchestration, safety primitives, NDA-gated extensions) without rebuilding them. But the core, the thing I wish I'd had a year ago, is free.

If you're building an autonomous agent and you've had that feeling of "I have no idea what this thing is actually going to do when I turn it on." That's the feeling Rein-AI was built to answer.

Launching April 30, 2026.

— **John N.W. Hampton Jr**
Former merchant mariner. Self-taught. Shipping.
