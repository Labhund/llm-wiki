# Working Agent Context for Tool Calls

**Status:** Draft Design Idea

**Context:** Platform/agent integration (Claude Code, Hermes, etc.)

---

## Problem

When an agent calls a tool (like `wiki_traverse()`), how much conversation history should be passed as context?

**Trade-offs:**

| More context | Less context |
|--------------|--------------|
| ✅ Agent understands full conversation flow | ❌ Expensive (more tokens) |
| ✅ Can reference earlier decisions | ❌ Slower (longer prompts) |
| ✅ Better continuity | ❌ Risk of context window overflow |
| ❌ Noise (irrelevant turns) | ✅ Focused on recent relevant history |

**Goal:** Provide **right amount** of context — enough for continuity, not so much it's expensive/noisy.

---

## The Platform Question

Different platforms handle tool context differently:

| Platform | Default behavior | Configurable? |
|-----------|------------------|-----------------|
| **Claude Code** | Last N turns (N = config) | Yes (`--context-window`) |
| **Hermes Agent** | Full conversation history | No (all or nothing) |
| **OpenAI Codex** | Variable (platform decision) | No |
| **Custom** | Depends on implementation | Varies |

**The challenge**: Tool doesn't control context — platform does.

---

## Design Space

### Option A: Full Conversation History

**Always pass everything.**

Pros:
- Agent has complete picture
- No missing context
- Consistent behavior

Cons:
- Expensive for long conversations
- Slow (huge prompts)
- Risk of context overflow

**Use case:** Short conversations, or tools that need full context (e.g., "summarize this chat")

---

### Option B: Sliding Window (Last N Turns)

**Pass last N turns only.**

Pros:
- Predictable cost
- Fast (bounded prompt size)
- Focuses on recent context

Cons:
- Loses early context
- May miss important decisions
- Inconsistent with full-history tools

**Use case:** Long conversations, tools that focus on recent state.

---

### Option C: Relevance-Filtered Context

**Pass only turns relevant to tool invocation.**

Pros:
- Focused, minimal noise
- Efficient (no wasted tokens)

Cons:
- Requires LLM to filter (expensive)
- Filter may miss context
- Complex to implement

**Use case:** Long conversations with distinct phases (e.g., setup → work → teardown).

---

### Option D: Summarized History

**Pass summarized version of full history.**

Pros:
- Complete picture (summarized)
- Bounded size
- Retains key decisions

Cons:
- Loss of detail
- Summarization cost
- Summary may miss nuance

**Use case:** Very long conversations (50+ turns), cost-critical.

---

### Option E: Explicit Context Parameter

**Tool declares how much context it needs.**

```python
def wiki_traverse(
    query: str,
    context: Optional[str] = None,  # Optional: explicit context
    context_mode: str = "auto",     # "none", "full", "summary", "auto"
    context_turns: int = 10,         # For "auto" mode
) -> dict:
    """Wiki traversal with configurable context."""
    ...
```

**Modes:**
- `none` — No conversation history passed
- `full` — Pass everything
- `summary` — Pass summarized history
- `auto` — Pass last `context_turns` (default: 10)

**Pros:**
- Tool controls its needs
- Flexible per tool
- Platform-agnostic

**Cons:**
- Tool complexity (deciding what mode to use)
- Requires platform support

---

## Recommended Approach: Auto + Tool Override

**Default: Auto mode (last 10 turns)**

```yaml
# Platform config
tool_context:
  default_mode: "auto"
  default_turns: 10
  allow_tool_override: true
```

**Tool can override:**
```python
# Tool declares its needs
def wiki_traverse(
    query: str,
    context_mode: str = "auto",  # Override default
    context_turns: int = 5,         # Override default
) -> dict:
    ...
```

**Why this works:**
- Most tools are fine with "auto" (10 turns is reasonable)
- Power tools can override for their needs
- Platform has sensible default
- User can configure globally

---

## Implementation: Platform Side

```python
class ToolInvoker:
    def __init__(
        self,
        conversation_history: List[dict],
        config: dict
    ):
        self.history = conversation_history
        self.config = config

    def call_tool(
        self,
        tool: callable,
        tool_name: str,
        args: dict
    ) -> dict:
        """Invoke tool with appropriate context."""

        # Get context for this tool
        tool_context = self._get_context(tool_name, tool.__doc__)

        # Inject context into args if tool accepts it
        if "context" in tool.__code__.co_varnames:
            args["context"] = tool_context

        # Call tool
        return tool(**args)

    def _get_context(self, tool_name: str, tool_doc: str) -> str:
        """Get appropriate context for tool."""
        # Parse tool doc for context hints
        needs_full = "needs full history" in tool_doc.lower()
        needs_none = "no history needed" in tool_doc.lower()

        # Tool override
        if needs_full:
            return self._format_full_history()
        if needs_none:
            return ""

        # Default: auto mode
        default_turns = self.config.get("default_turns", 10)
        return self._format_recent_history(default_turns)

    def _format_full_history(self) -> str:
        """Format full conversation history."""
        return "\n\n".join(
            f"{t['role']}: {t['content']}"
            for t in self.history
        )

    def _format_recent_history(self, turns: int) -> str:
        """Format recent N turns."""
        recent = self.history[-turns:]
        return "\n\n".join(
            f"{t['role']}: {t['content']}"
            for t in recent
        )
```

---

## Implementation: Tool Side

```python
def wiki_traverse(
    query: str,
    context: Optional[str] = None,  # Platform passes this
    context_mode: str = "auto",     # Optional override
) -> dict:
    """
    Traverse wiki to answer a query.

    Args:
        query: The user's question
        context: Conversation history (passed by platform)
        context_mode: Override for context handling ("none", "full", "auto")

    Returns:
        Answer with citations and traversal path.
    """
    # Process context based on mode
    if context_mode == "none":
        working_context = None
    elif context_mode == "full":
        working_context = context
    else:  # auto
        # Parse last N turns from context
        if context:
            working_context = context[-2000:]  # Truncate to ~2000 tokens
        else:
            working_context = None

    # Traversal uses working_context if available
    ...
```

---

## Context Content Format

**Structure matters for agent understanding:**

```
CONVERSATION HISTORY (last 10 turns):

Turn 1:
  User: "What is k-means?"
  Assistant: "K-means is an unsupervised clustering algorithm..."

Turn 2:
  User: "How do we choose k?"
  Assistant: "Elbow method, silhouette analysis..."

Turn 3:
  User: "What about sRNA embeddings?"
  Assistant: "sRNA embeddings use k-means for validation..."

CURRENT TURN:
  User: "How do we validate those embeddings?"

---
```

**Key elements:**
- Turn numbers (for reference)
- Role labels (User/Assistant)
- Clear separation between turns
- "CURRENT TURN" marker (agent knows where it is)

---

## Open Questions

1. **What's the right default for auto mode?** 5 turns? 10 turns? 20 turns?
2. **Should tool be able to request MORE context dynamically?** "I need 20 turns, not 10"
3. **How to handle context truncation?** Cut mid-turn? Cut at turn boundary?
4. **Should we include tool call history?** Previous tool outputs as context?
5. **Can we learn context needs per tool?** Some tools always need full history, some never do.
6. **What about cross-tool context?** If tool A was called, tool B knows about it?

---

## Related Ideas

- [[Pre-Seeding with Vector/Keyword Lookup]] — Tool needs conversation context to inform pre-seeding
- [[Attention-Aware Prompt Ordering]] — Context ordering affects attention within tool's own prompt
- [[Programmatic Context Tuning]] — Tool-side context management (platform-side is different)

---

## Notes

This is fundamentally a **platform/agent integration** question, not just wiki traversal.

The right approach balances:
- Agent needs (continuity, understanding of conversation flow)
- Platform constraints (cost, speed, context limits)
- Tool flexibility (some tools need more/less context)

My recommendation: **Auto mode with tool override**. Most tools are fine with "last 10 turns", but power tools should be able to declare their needs.

The key insight is that **context is expensive** — every token passed to a tool costs money and latency. Platforms should default to minimal context, let tools opt in to more if needed.
