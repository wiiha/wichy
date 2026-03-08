# Memory Integration for LLM Agents: How to Expose Memory to the Harness

*How do you weave a memory system into an agent's reasoning loop? This document summarizes research on integration patterns, triggers, and orchestration.*

---

## Problem Statement

Having a memory store (vector DB, file system, graph) is only half the battle. The real challenge is **orchestration**: when should the agent read/write memory, and how should memory content be exposed to the LLM during inference?

This distinguishes a static RAG system from a dynamic, agentic memory.

---

## 1. Retrieval Triggers: When to Access Memory

### Heuristic Triggers
- **Session start**: Always retrieve top-K memories relevant to user ID or initial query.
- **Per-turn retrieval**: Retrieve before *every* LLM call (baseline RAG). Simple but can over-inject.
- **Explicit tool call**: Agent decides to call `search_memory(query)` when needed. Gives control but requires learned behavior.
- **Event-driven**: Certain events (user correction, task completion) automatically trigger memory writes or refreshes.

### Learned Triggers
- **Memory-R1 & UMA**: Use reinforcement learning to decide which memories to write/update/delete.
- **ActMem**: Integrates retrieval with causal reasoning; triggers when current situation may conflict with past states.
- **Retrieval gating**: Learn a classifier that predicts whether retrieval will help for a given query, reducing over-injection.

---

## 2. Interface to LLM: How Memory Content is Exposed

### A) Tool/Function Calling
Memory is an explicit tool: `search_memory(query: str) -> list`.

- **Agent decides** when and what to retrieve.
- Can iterate: refine query, re-retrieve until satisfied.
- **Pros**: Control, interpretability.
- **Cons**: Extra tokens and steps; agent must learn to use it effectively.

*Example*: CrewAI + Mem0; Memory-R1’s Answer Agent.

### B) Pre-prompt Injection (RAG-style)
The harness automatically retrieves relevant memories and appends them to the system prompt/context *before* each LLM call.

- Agent is unaware retrieval happened; memory appears as background knowledge.
- **Pros**: Simple; no special agent training; compatible with any LLM.
- **Cons**: Agent cannot correct retrieval; risk of over-injection if too many memories added.

*Example*: Google ADK + Milvus; many simple RAG agents.

### C) Hybrid: Retrieval as Thought Process
Memories are retrieved and then *reasoned over* by the LLM before being used.

- Often seen in ReAct-style: "I need to recall X → search_memory → I found Y → now I’ll answer."
- Blends tool use with reflection; the retrieval result becomes part of the reasoning trace.

---

## 3. Write Strategies: What to Store

### Write Formats
- **Raw chunks**: Store conversation/user–assistant exchange verbatim. No LLM processing at write time.
- **LLM summarization**: Summarize old conversations into concise facts (MemGPT-style).
- **Fact extraction**: Use LLM to extract entities, preferences, events (Mem0-style).
- **Structured graphs**: Build a causal/semantic graph; nodes = events, edges = relations (ActMem).

### Write Triggers
- **Event-driven**: After each user message or tool result.
- **Batch**: Daily or after N messages.
- **Learned**: RL policy decides to write or discard (Memory-R1).

---

## 4. Orchestration Frameworks from Literature

### Generative Agents (Stanford/Google)
- **Memory Stream**: single stream of experiences (episodic).
- **Retrieval scoring**: `score = recency * importance * relevance`.
- **Reflection**: periodic pass that creates higher-level semantic memories from episodes.

### CrewAI + Mem0
- CrewAI built-in memory has types: short-term, long-term, entity, contextual.
- Mem0 replaces as external provider: user-scoped, persistent, intelligent extraction.
- Access via tool calls; supports multi-user isolation.

### Memory-R1
- Two agents: **Memory Manager** (add/update/delete/no-op) and **Answer Agent** (select relevant memories).
- Both fine-tuned with outcome-driven RL (PPO/GRPO).
- Demonstrates that memory operations can be learned end-to-end.

### ActMem
- Transforms dialogue history into a structured causal and semantic graph.
- Uses counterfactual reasoning and commonsense completion.
- Enables detection of conflicts between past states and current intentions.

---

## 5. Key Research Findings

### Retrieval Quality Dominates Write Sophistication
- **Yuan et al. 2026 (Diagnosing Retrieval vs. Utilization)**: On LoCoMo, varying retrieval method (cosine, BM25, hybrid) changed accuracy by **20 points** (57.1%–77.2%). Varying write strategy (raw vs summarized vs extracted) changed it by only **3–8 points**.
- **Implication**: Investing in better retrieval (hybrid search, reranking) pays off more than fancy write-time summarization.
- Surprising: **Raw chunked storage** (zero LLM calls at write time) matched or outperformed expensive lossy alternatives.

### Over-Injection is a Major Failure Mode
- With loose similarity thresholds, nearly every retrieved memory gets injected, flooding context with irrelevant info.
- Irrelevant context is not neutral—it actively dilutes attention and hurts performance.
- **Solutions**: limit count (top-K), apply a relevance score threshold, or use a learned gating network.

### Memory Interference
- Injected memories can cause the agent to ignore important in-context information (the current conversation).
- Need to balance context allocation: how many tokens go to memory vs. the current dialogue?

### Non-Determinism
- Memory introduces hidden state: two identical prompts can produce different responses based on historical state.
- This complicates debugging, reproducibility, and evaluation.
- Production systems need versioning, memory snapshots, and the ability to disable memory for baseline testing.

---

## 6. Practical Integration Checklist

| Decision              | Options                                                        | Trade-offs                                                                           |
| --------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| **Retrieval trigger** | Every turn / explicit tool / learned gate                      | Every turn = simpler but noisy; explicit = parsimonious but requires agent skill     |
| **Write format**      | Raw chunks / summaries / facts / graphs                        | Raw = high recall, low density; summaries = denser but may lose details              |
| **Retrieval method**  | Dense (embeddings) / sparse (BM25) / hybrid                    | Dense = semantic; sparse = exact keywords; hybrid often best                         |
| **Injection method**  | Direct append / summarized injection / separate context window | Direct = simple; summary reduces noise; separate window preserves context separation |
| **User isolation**    | None / per-user ID in memory entries                           | Essential for multi-user; otherwise cross-talk                                       |
| **Evaluation**        | Accuracy / latency / cost / memory coherence                   | Need benchmarks that test long-horizon consistency                                   |

---

## 7. Suggested Starting Point (Simple + Extensible)

### Storage
- **Raw chunked memories**: store each user+assistant turn (or complete agent step if using tools) as a chunk.
- Include: `timestamp`, `user_input`, `agent_response`, `embedding` (of user + assistant text).

### Retrieval
- **Hybrid search**: combine dense (embedding cosine) and sparse (BM25) scores.
- **Top-K = 5** with **similarity threshold > 0.7** (adjust empirically).
- Retrieve at the **start of each turn** (automatic pre-prompt injection).

### Write
- **After every user message**, append the last user+assistant exchange to memory.
- Throttle to **every K messages** (e.g., every 3rd turn) to avoid spam.
- No LLM processing at write time (keep raw).

### Orchestration
- No learned components initially; use heuristics.
- The harness automatically injects retrieved memories into the system prompt before each LLM call.
- Memory appears in a dedicated section: `[Relevant past conversations] ... [/]`.

### Future Enhancements
- Add a learned gating model to reduce over-injection.
- Introduce a “memory consolidation” job that periodically summarizes old raw chunks into semantic facts (self-reflection).
- Move to structured memory extraction (Mem0-style) or graph memory (ActMem) if needed.
- Experiment with explicit tool-based retrieval to give agent control.

---

## 8. Open Questions

- How to handle very long tool outputs? Store raw? Summarize? Store separately and reference?
- When should memories be *forgotten*? Decay policies based on age or salience.
- How to evaluate memory quality beyond task accuracy? Coherence, contradiction detection, user preference consistency.
- Can we learn retrieval thresholds end-to-end via RL?

---

## References

- Yuan et al. 2026: “Diagnosing Retrieval vs. Utilization Bottlenecks in LLM Agent Memory”
- Hu et al. 2026: “ActMem: Bridging the Gap Between Memory Retrieval and Reasoning”
- Park et al. 2023: “Generative Agents” (memory stream + reflection)
- Mem0: “CrewAI + Mem0: Production-Ready Memory for AI Agents”
- Various blog posts on architecture (Muthu’s notes, Dev.to “What I Learned Adding Memory…”)

---

*Created 2026-03-06 based on literature review and discussion with W.*
