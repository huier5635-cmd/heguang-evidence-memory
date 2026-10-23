"""Optional public-case draft generation. Never imported by the memory service."""
import json
from decimal import Decimal, InvalidOperation

import httpx

BASE_URL = "https://openrouter.ai/api/v1"


class FreeOnlyClient:
    def __init__(self, key, *, transport=None):
        if not key:
            raise ValueError("PUBLIC_EVAL_API_KEY is not configured")
        self.key = key
        self.client = httpx.Client(base_url=BASE_URL, transport=transport, timeout=180, trust_env=False,
                                   follow_redirects=False)

    def close(self):
        self.client.close()

    def generate(self, model, messages, *, max_tokens=4096):
        if not model.endswith(":free") or not 1 <= max_tokens <= 8192:
            raise ValueError("Only named :free models and a bounded output are permitted")
        try:
            catalog = self.client.get("/models")
            catalog.raise_for_status()
            entry = next((row for row in catalog.json()["data"] if row["id"] == model), None)
            if entry is None:
                raise ValueError("Requested free model is absent from the live catalog")
            pricing = entry.get("pricing", {})
            if not {"prompt", "completion"} <= pricing.keys():
                raise ValueError("Model pricing is incomplete")
            try:
                free = all(Decimal(str(pricing.get(name, "0"))) == 0 for name in ("prompt", "completion", "request"))
            except InvalidOperation:
                free = False
            if not free:
                raise ValueError("Live catalog does not confirm zero text/request prices")
            if any(row.get("role") not in {"system", "user"} or not isinstance(row.get("content"), str) for row in messages):
                raise ValueError("Public draft generation accepts only text messages")
            if sum(len(row["content"].encode()) for row in messages) > 1024 ** 2:
                raise ValueError("Public-case upload exceeds the research limit")
            payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False,
                       "provider": {"max_price": {"prompt": 0, "completion": 0, "request": 0},
                                    "allow_fallbacks": False, "data_collection": "deny"}}
            if "temperature" in entry.get("supported_parameters", []):
                payload["temperature"] = 0
            response = self.client.post("/chat/completions", headers={"Authorization": "Bearer " + self.key}, json=payload)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body.get("choices"), list) or not body["choices"]:
                raise RuntimeError("Model did not return a draft")
            reported_cost = body.get("usage", {}).get("cost")
            if reported_cost is not None and Decimal(str(reported_cost)) != 0:
                raise RuntimeError("Unexpected nonzero inference cost; stop the experiment")
            return {"response": body, "catalog_pricing": pricing,
                    "catalog_context_length": entry.get("context_length"),
                    "reported_cost_zero": reported_cost is not None and Decimal(str(reported_cost)) == 0,
                    "pricing_and_route_capped_at_zero": True}
        except httpx.HTTPStatusError as error:
            raise RuntimeError(f"Free model request stopped at HTTP {error.response.status_code}; no retry or paid fallback") from None
        except httpx.HTTPError:
            raise RuntimeError("Free model network request failed; no retry or paid fallback") from None


def run_plan(plan, model, key, output):
    if not plan.get("public_inputs_verified") or plan.get("official_data") is not False:
        raise ValueError("A verified public-only input plan is required")
    order = plan["arm_order"]
    if sorted(order) != ["with_memory", "without_memory"]:
        raise ValueError("Exactly two predeclared arms are required")
    client = FreeOnlyClient(key)
    try:
        output.mkdir(parents=True, exist_ok=False)
        for arm in order:
            result = client.generate(model, plan["messages"][arm])
            (output / (arm + ".json")).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            # Never execute or apply external model output automatically.
            if not result["reported_cost_zero"]:
                raise RuntimeError("Cost reporting is incomplete; stop before another request")
    finally:
        client.close()
