import copy
from dataclasses import dataclass, field
from .strings import count_tokens, remove_thinking_blocks


SUMMARIZE_PROMPT = """Summarize the following conversation messages into a concise paragraph.
Preserve key facts, decisions, tool results, and any information that may be needed for the ongoing conversation.
Only output the summary, no other text.

Messages:
{messages}"""


@dataclass
class TrimResult:
    """Result of a context trim operation."""
    history: list = field(default_factory=list)
    original_tokens: int = 0
    trimmed_tokens: int = 0
    suggested_tokens: int = 0
    max_tokens: int = 0


class ContextManager:
    """Token-aware context manager that trims chat history to fit within a token budget.

    Applies a pipeline of techniques: tool output truncation, similarity-based filtering,
    budget-based selection, and optional LLM summarization of dropped messages.
    """

    TOOL_OUTPUT_MAX_CHARS = 500
    RECENT_WINDOW = 4
    TOKEN_OVERHEAD_PER_MSG = 4

    def __init__(
        self,
        max_tokens: int,
        suggested_tokens: int,
        embedding_handler=None,
        llm_handler=None,
        summarization_enabled: bool = False,
    ):
        self.max_tokens = max_tokens
        self.suggested_tokens = suggested_tokens
        self.embedding_handler = embedding_handler
        self.llm_handler = llm_handler
        self.summarization_enabled = summarization_enabled

    def trim(
        self,
        history: list[dict[str, str]],
        prompts_token_count: int,
        current_message: str,
    ) -> TrimResult:
        """Trim history to fit within the token budget.

        Args:
            history: Chat history in Newelle format.
            prompts_token_count: Token count already reserved for system prompts.
            current_message: The current user message (used for similarity scoring).

        Returns:
            TrimResult with trimmed history and token statistics.
        """
        if not history:
            return TrimResult(
                history=history,
                suggested_tokens=self.suggested_tokens,
                max_tokens=self.max_tokens,
            )

        history = copy.deepcopy(history)
        n = len(history)

        recent_start = max(0, n - self.RECENT_WINDOW)

        # Phase 1: truncate old tool outputs
        for i in range(recent_start):
            if history[i].get("User") == "Console":
                history[i] = self._truncate_tool_output(history[i])

        # Phase 2: count tokens per message
        msg_tokens = [
            count_tokens(m.get("Message", "")) + self.TOKEN_OVERHEAD_PER_MSG
            for m in history
        ]
        original_tokens = sum(msg_tokens) + prompts_token_count

        if original_tokens <= self.suggested_tokens:
            return TrimResult(
                history=history,
                original_tokens=original_tokens,
                trimmed_tokens=original_tokens,
                suggested_tokens=self.suggested_tokens,
                max_tokens=self.max_tokens,
            )

        # Phase 3: compute similarity scores for older messages
        recent_cost = sum(msg_tokens[recent_start:])
        older_indices = list(range(recent_start))

        if self.embedding_handler is not None and current_message and older_indices:
            scores = self._compute_similarities(history, older_indices, current_message)
        else:
            scores = {i: i / max(recent_start, 1) for i in older_indices}

        # Phase 4: fill budget with highest-scoring older messages
        available = self.suggested_tokens - prompts_token_count - recent_cost
        sorted_older = sorted(older_indices, key=lambda i: scores.get(i, 0), reverse=True)

        keep_set = set(range(recent_start, n))
        dropped_indices = []

        for i in sorted_older:
            if available >= msg_tokens[i]:
                keep_set.add(i)
                available -= msg_tokens[i]
            else:
                dropped_indices.append(i)

        # Phase 5: enforce hard max_tokens limit
        current_total = sum(msg_tokens[i] for i in keep_set) + prompts_token_count
        if current_total > self.max_tokens:
            kept_older_sorted = sorted(
                [i for i in keep_set if i < recent_start],
                key=lambda i: scores.get(i, 0),
            )
            while current_total > self.max_tokens and kept_older_sorted:
                remove_idx = kept_older_sorted.pop(0)
                keep_set.discard(remove_idx)
                current_total -= msg_tokens[remove_idx]
                dropped_indices.append(remove_idx)

        # Phase 6: optional summarization of dropped messages
        result_history = []
        if dropped_indices:
            dropped_indices.sort()
            dropped_messages = [history[i] for i in dropped_indices]
            if self.summarization_enabled and self.llm_handler is not None:
                summary = self._summarize_dropped(dropped_messages)
                if summary:
                    result_history.append({
                        "User": "User",
                        "Message": f"[Previous conversation summary]\n{summary}",
                    })

        for i in sorted(keep_set):
            result_history.append(history[i])

        trimmed_tokens = sum(
            count_tokens(m.get("Message", "")) + self.TOKEN_OVERHEAD_PER_MSG
            for m in result_history
        ) + prompts_token_count

        return TrimResult(
            history=result_history,
            original_tokens=original_tokens,
            trimmed_tokens=trimmed_tokens,
            suggested_tokens=self.suggested_tokens,
            max_tokens=self.max_tokens,
        )

    def _truncate_tool_output(self, message: dict) -> dict:
        """Truncate a Console message's content to TOOL_OUTPUT_MAX_CHARS."""
        text = message.get("Message", "")
        if len(text) <= self.TOOL_OUTPUT_MAX_CHARS:
            return message

        lines = text.split("\n")

        # Keep the first line (tool header) intact
        header = lines[0] if lines else ""
        body = "\n".join(lines[1:]) if len(lines) > 1 else ""

        if len(body) <= self.TOOL_OUTPUT_MAX_CHARS - len(header):
            return message

        body_half = (self.TOOL_OUTPUT_MAX_CHARS - len(header) - 30) // 2
        truncated = header + "\n" + body[:body_half] + "\n... truncated ...\n" + body[-body_half:]

        message = message.copy()
        message["Message"] = truncated
        return message

    def _compute_similarities(
        self,
        history: list[dict],
        indices: list[int],
        query: str,
    ) -> dict[int, float]:
        """Compute cosine similarity between older messages and the current query."""
        try:
            texts = [history[i].get("Message", "") for i in indices]
            all_texts = [query] + texts
            embeddings = self.embedding_handler.get_embedding(all_texts)

            query_emb = embeddings[0]
            similarities = {}

            for j, idx in enumerate(indices):
                msg_emb = embeddings[j + 1]
                dot = float(sum(a * b for a, b in zip(query_emb, msg_emb)))
                norm_q = float(sum(a * a for a in query_emb)) ** 0.5
                norm_m = float(sum(a * a for a in msg_emb)) ** 0.5
                if norm_q > 0 and norm_m > 0:
                    similarities[idx] = dot / (norm_q * norm_m)
                else:
                    similarities[idx] = 0.0

            return similarities
        except Exception:
            return {i: i / max(len(indices), 1) for i in indices}

    def _summarize_dropped(self, messages: list[dict]) -> str:
        """Use the LLM to summarize dropped messages."""
        try:
            formatted = []
            for msg in messages:
                role = msg.get("User", "User")
                content = msg.get("Message", "")
                if len(content) > 300:
                    content = content[:300] + "..."
                formatted.append(f"{role}: {content}")

            messages_text = "\n".join(formatted)
            if not messages_text.strip():
                return ""

            prompt = SUMMARIZE_PROMPT.format(messages=messages_text)
            summary = self.llm_handler.generate_text(prompt)
            summary = remove_thinking_blocks(summary).strip()
            return summary
        except Exception:
            return ""
