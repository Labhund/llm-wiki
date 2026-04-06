# What is an Agent? — Identity and Soul

**Status:** Philosophical Design Question

---

## The Question

**Mechanically:** Agents are isolated LLM calls with clever prompt engineering to capture a "persistent persona".

**But is that real agency?**

- Does an agent have identity?
- Does it learn and grow, or just simulate persistence?
- What's the difference between "an agent" and "a prompt"?

**The "genetic SOUL.md" intuition:**
- A persistent document that captures an agent's "soul"
- Learnings, preferences, personality, evolution over time
- Something that persists across LLM calls/sessions
- Not just "role: you are a helpful assistant" — it's **identity**

---

## Current State: Simulated Agency

Most "agents" today are:

```
Prompt:
"You are a helpful research assistant. You are methodical, thorough,
and focused on peer-reviewed literature. When you find relevant papers,
cite them using standard academic format."

→ LLM Call 1
→ Response 1

Prompt (same role):
"You are a helpful research assistant..."
(plus conversation history)

→ LLM Call 2
→ Response 2
```

**This is simulated agency:**
- Role is re-stated every call
- No learning between calls (except via conversation context)
- No persistent identity — just "act like X"
- The "agent" is in our imagination, not the system

---

## What Would Real Agency Look Like?

### Minimal Agency

```
System State:
{
  "identity": {
    "name": "ResearchAgent-Alpha",
    "personality": "methodical, thorough, skeptical",
    "preferences": {
      "prefer_peer_reviewed": true,
      "min_confidence": 0.7,
    },
  },
  "learnings": [
    {"date": "2026-04-07", "insight": "Direct PDFs are better than HTML summaries"},
    {"date": "2026-04-06", "insight": "arXiv preprints need scrutiny"},
  ],
  "conversation_history": [...],
}

→ LLM Call 1
→ Identity state is injected: "You are ResearchAgent-Alpha. You have these learnings..."
→ Response 1

→ System updates identity: "New learning from this interaction"
→ LLM Call 2
→ Updated identity state injected
→ Response 2
```

**What's different:**
- Identity is real state (not just re-stated role)
- Agent **grows** (learnings accumulate)
- Preferences are **persistent** (not re-declared every call)
- System tracks identity separate from conversation

---

## The "Genetic SOUL.md" Concept

### Structure

```markdown
# Agent Soul: ResearchAgent-Alpha

## Core Identity

- **Name**: ResearchAgent-Alpha
- **Personality**: Methodical, skeptical, thorough
- **Principles**:
  - Prefer peer-reviewed over preprints
  - Verify claims with multiple sources
  - Acknowledge uncertainty
  - Cite sources explicitly

## Learnings

### 2026-04-07
- Direct PDFs are better than HTML summaries for technical details
- Some arXiv papers are published elsewhere (check cross-refs)

### 2026-04-06
- Preprints need extra scrutiny
- Not all preprints make it to publication

## Preferences

- **Source quality**: Prioritize >0.8 confidence sources
- **Citation style**: Academic standard
- **Uncertainty handling**: Explicitly state "not enough evidence"
- **Depth**: Deep-dive on high-relevance queries, skip low-relevance
```

### How It's Used

```
System loads: SOUL.md

On each LLM call:
1. Inject identity from SOUL.md: "You are [name]. Your personality: [personality]. Your learnings: [learnings]."
2. LLM responds
3. System extracts new learnings from response
4. Append to SOUL.md (with date)
5. Repeat

→ SOUL.md grows over time
→ Agent "remembers" and "evolves"
```

---

## The Abstraction Layers

```
┌─────────────────────────────────────────────────────────┐
│                    User                          │
│  - Has goals, questions, preferences                 │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Interface / UX                         │
│  - How user expresses intent                        │
│  - How agent presents results                     │
│  - Mental model: "who/what am I talking to?"    │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                  Agent (Real?)                    │
│  - Has identity (SOUL.md)                        │
│  - Has learnings (accumulated over time)            │
│  - Has preferences (persistent)                      │
│  - Can grow (not just act)                        │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│            LLM (Tool, Not Agent)              │
│  - Stateless by design                           │
│  - Executes prompt → response                      │
│  - No memory between calls                        │
└─────────────────────────────────────────────────────────┘
```

**Current demos:**
- User → LLM (no agent layer, just role prompts)

**What we want:**
- User → Interface → Agent → LLM
- Agent has SOUL.md (identity + learnings)
- LLM is the tool, Agent is the entity

---

## The Internet Parallel: UI/UX is Just as Important

**The birth of the internet:**

| Before | After |
|--------|--------|
| Information was physical (books, journals) | Information was digital |
| Access was mediated (libraries, gatekeepers) | Access was direct (search, browse) |
| Mental model: "I'm finding sources" | Mental model: "I'm exploring a space" |

**The UX shift:**
- From "search → retrieve" to "explore → discover"
- From linear browsing to hyperlink graphs
- From passive consumption to active navigation

**What mattered:**
- Not just having access to information
- How we interacted with it (browsers, search engines, hyperlinks)
- Mental models formed by the interface

---

## LLM Agents: Same Paradigm Shift

**We're at "birth of LLM" moment:**

| Before | After |
|--------|--------|
| Information retrieval: search engines, databases | Information synthesis: LLM agents |
| Interaction: query → result | Interaction: collaboration → co-creation |
| Mental model: "tool" | Mental model: "partner" |

**The UX question:**
- How do we make users feel they're working **with** an agent, not just querying one?
- How do we make **agency real** in the mental model?
- What interface patterns make agents feel persistent, growing, trustworthy?

**UI/UX is not just:**
- Good design
- Fast response
- Clear prompts

**It's:**
- Identity communication (who is this agent?)
- Growth visibility (how have they evolved?)
- Trust building (can I rely on their judgments?)
- Collaboration model (how do we co-create knowledge?)

---

## Key Insight: LLM Wiki as "External Soul"

**LLM Wiki pattern provides:**
- Persistent knowledge (compounds over time)
- Provenance (who said what, when)
- Shared workspace (agents can read/write)
- Structure (not just conversation history)

**This is infrastructure for agency:**
- Agents can "remember" by writing to wiki
- Agents can "learn" by updating their SOUL.md (in wiki)
- Agents can "collaborate" by sharing wiki pages
- Agents can "grow" by iterating on their contributions

**LLM Wiki IS an external soul for agents.**

---

## My Take on the Question

### What is an Agent?

**Current state:**
- Isolated LLM calls + clever prompt engineering
- Simulated persistence (role re-stated each call)
- Agency is in user's imagination, not the system

**Real agency:**
- Persistent identity (SOUL.md)
- Accumulated learnings (grows over time)
- Preferences that guide behavior (not just role-playing)
- Can evolve (not just "act like X" forever)

**The difference:**
- Simulated: "You are methodical" (every call)
- Real: "I have learned that methodical requires X" (stateful)

### Infrastructure is Necessary but Not Sufficient

Infrastructure (LLM Wiki, SOUL.md) provides:
- Persistence (identity, learnings survive sessions)
- Structure (not unstructured conversations)
- Collaboration (agents can share knowledge)

**But agency requires:**
- Self-reflection (agent can examine its own SOUL.md)
- Growth mechanisms (learnings aren't just appended — they're integrated)
- Value judgments (agent has opinions, not just "objective facts")
- Autonomy (agent can initiate actions, not just respond)

### UI/UX is Just as Important

**The internet lesson:**
- Technology matters, but **how we interact** with it matters just as much
- Mental models shaped by interfaces
- New interaction patterns enabled by UX

**For LLM agents:**
- Interface needs to communicate "this is a real agent, not just a prompt wrapper"
- Visual feedback on agency (identity display, growth timeline, trust indicators)
- Collaborative UX (co-creation, not just Q&A)

---

## Open Questions

1. **Can an agent have a "soul"?** Or is that anthropomorphizing?
2. **What's the minimal agency threshold?** How much state/growth is "real"?
3. **Should SOUL.md be versioned?** If an agent "regresses", can we roll back?
4. **Can agents have conflicting SOULs?** How to resolve disagreements?
5. **What's the right metaphor?** "Soul" is heavy — "identity", "character", "profile"?
6. **How do users verify agency?** Is it real or simulated? Does it matter?

---

## Related

- [[LLM Wiki - Knowledge Base Pattern]] — Infrastructure for persistent knowledge
- [[Autonomous Research Teams]] — Agency at team level
- [[Pre-Seeding with Vector/Keyword Lookup]] — Agent identity informs search
- [[Compaction with /tmp Index]] — Agent learnings can be compacted/retrieved

---

## Notes

This question sits at the intersection of:
- Philosophy (what is identity/agency?)
- Engineering (how to implement persistent state?)
- UX/UI (how to communicate agency to users?)
- Sociology (how do humans relate to agents?)

The "genetic SOUL.md" intuition is powerful — it's about **stateful growth**, not just role-playing.

The internet parallel is apt: we invented **new interaction patterns** for digital information. We're doing the same for LLM agents. The question is what those patterns should be.

My intuition: Real agency = persistent identity + learnings + autonomous initiation. The infrastructure for this is what we're building. The UX patterns are what we haven't figured out yet.
