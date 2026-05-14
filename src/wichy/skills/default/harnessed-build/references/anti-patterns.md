# Agentic Coding Anti-Patterns

Condensed research for why unharnessed agentic coding produces repeated bugs.

## 1. Invisible State

**Symptom:** Agent "forgets" constraints from prior sessions.

**Why it happens:** Reliance on chat history means the model creates a compressed mental map that loses detail. Constraints discovered in round 1 live only in the transcript, not in structured external state.

**Fix:** Lock invariants in a written spec (`SPEC.md`) before coding. Agents reference the spec, not their memory.

## 2. Monolithic Mega-Prompt

**Symptom:** Single large prompt produces code that varies unpredictably and misses edge cases.

**Why it happens:** The model optimizes for intent when overloaded with instructions, compressing procedural guidance. Steps near the end rely on lossy memory.

**Fix:** Decompose into stages. Each stage gets a focused prompt, specific files, and a deterministic gate.

## 3. All-or-Nothing Autonomy

**Symptom:** Either uncontrolled edits across the entire codebase, or excessive confirmation-seeking that still misses bugs.

**Why it happens:** No calibrated delegation. Too much autonomy → harmful sequences. Too little → slow chatbot.

**Fix:** Action budgets. Agent proposes plan, user locks it. Agent executes stages independently, gates prevent uncontrolled edits.

## 4. No Verification Loop

**Symptom:** Bugs found only during review, not during creation.

**Why it happens:** Single-pass generation is "stochastic reasoning" — plausible but not necessarily correct. No separate critique pass.

**Fix:** Generate-evaluate-revise. After implementation, agent self-critiques against locked spec. Each invariant is traced to an enforcing line of code.

## 5. Chasing Research Instead of Fixing Architecture

**Symptom:** Adding complex coordination (multi-agent, Swarm) on top of broken fundamentals.

**Why it happens:** Teams adopt exotic topologies instead of fixing state management.

**Fix:** Fix foundations first. Structured spec + deterministic gates + verification loops. Only then consider multi-agent patterns.

---

## Sources

- [AI Agent Anti‑Patterns: Architectural Pitfalls](https://achan2013.medium.com/ai-agent-anti-patterns-part-1-architectural-pitfalls-that-break-enterprise-ai-agents-before-they-32d211dded43)
- [Agentic Patterns Developers Should Steal](https://karun.me/blog/2026/03/19/agentic-patterns-developers-should-steal/)
- [Why You Should Use an Agentic Harness](https://www.mindstudio.ai/blog/qwen-3-6-plus-agentic-harness-vs-chat-mode)
- [AI Agent Workflow Patterns](https://www.acceli.com/blog/ai-agent-workflow-patterns)
