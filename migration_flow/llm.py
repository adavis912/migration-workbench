"""Thin, thread-safe Claude client shared by the agents: structured JSON output, call budget, token usage."""
import json, threading

PRICE_PER_MTOK = {"input": 3.0, "output": 15.0}   # assumption for cost estimates; check current pricing


class Claude:
    def __init__(self, api_key, model, max_calls=80):
        import anthropic
        self.client, self.model, self.max_calls = anthropic.Anthropic(api_key=api_key, max_retries=4), model, max_calls
        self.calls, self.input_tokens, self.output_tokens = 0, 0, 0
        self._lock = threading.Lock()

    def _reserve(self):
        with self._lock:
            if self.calls >= self.max_calls: raise RuntimeError(f"Claude call budget ({self.max_calls}) exhausted")
            self.calls += 1

    def _account(self, msg):
        u = getattr(msg, "usage", None)
        if u:
            with self._lock:
                self.input_tokens += getattr(u, "input_tokens", 0) or 0
                self.output_tokens += getattr(u, "output_tokens", 0) or 0

    def json(self, system, user, schema, name, max_tokens=16000):
        """Schema-constrained JSON (output_config json_schema). Retries once; doubles max_tokens if cut off."""
        last = None
        for _ in range(2):
            self._reserve()
            try:
                # Streaming: the SDK refuses long non-streaming requests (large max_tokens).
                with self.client.messages.stream(
                        model=self.model, max_tokens=max_tokens, system=system,
                        messages=[{"role": "user", "content": user}],
                        output_config={"format": {"type": "json_schema", "schema": schema}}) as stream:
                    msg = stream.get_final_message()
                self._account(msg)
                text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
                if getattr(msg, "stop_reason", None) == "max_tokens":
                    max_tokens = min(max_tokens * 2, 64000)
                    raise RuntimeError(f"response cut off at max_tokens ({len(text)} chars)")
                return json.loads(text)
            except Exception as e:
                last = e
        raise RuntimeError(f"structured output ({name}) failed: {last}")

    def text(self, system, user, max_tokens=6000):
        self._reserve()
        with self.client.messages.stream(model=self.model, max_tokens=max_tokens, system=system,
                                         messages=[{"role": "user", "content": user}]) as stream:
            msg = stream.get_final_message()
        self._account(msg)
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()

    def usage(self):
        cost = self.input_tokens / 1e6 * PRICE_PER_MTOK["input"] + self.output_tokens / 1e6 * PRICE_PER_MTOK["output"]
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "estimated_cost_usd": round(cost, 2)}
