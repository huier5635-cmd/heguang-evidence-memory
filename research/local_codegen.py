"""Pure public paired-draft preparation and exact snippet validation; no code execution."""
import hashlib
import json
from pathlib import PurePosixPath

from heguang_code.reranker import fit_budget


def prepare(parent, *, memory_characters=24000, memory_records=8):
    if not parent.get("public_inputs_verified") or parent.get("official_data") is not False:
        raise ValueError("Verified public-only parent inputs required")
    common = parent["messages"]["without_memory"]
    with_memory = parent["messages"]["with_memory"]
    if with_memory[:-1] != common or len(common) != 2:
        raise ValueError("The paired arms must have exactly the same public task and base code")
    records = json.loads(with_memory[-1]["content"])["historical_retrieval_records"]
    chosen = fit_budget(records, top_k=memory_records, characters=memory_characters)
    if not chosen:
        raise ValueError("No intact historical record fits the predeclared local pilot budget")
    messages = {"without_memory": common, "with_memory": [*common, {"role": "user", "content": json.dumps({
        "historical_retrieval_records": chosen}, ensure_ascii=False)}]}
    visible = json.loads(common[1]["content"])
    allowed = {row["path"]: row["sha256"] for row in visible["unmodified_base_source_files"]}
    return {"purpose": "Local same-model public development pilot, not a blind score or official agent",
            "instance_id": parent["instance_id"], "base_commit": parent["base_commit"],
            "public_inputs_verified": True, "official_data": False,
            "manual_public_case_already_developed": True,
            "target_gold_solution_read": False, "target_tests_in_model_prompt": False,
            "arm_order": parent["arm_order"], "messages": messages, "allowed_file_sha256": allowed,
            "local_memory_prefix": {"characters": memory_characters, "max_records": memory_records,
                "returned_records": len(chosen), "returned_characters": sum(len(row["content"]) for row in chosen),
                "selection": "Ranked full API records; an explicit smaller context budget for the local model. No text is truncated."},
            "generation": {"temperature": 0, "seed": 1729, "max_tokens": 2048, "response_format": {"type": "json_object"}},
            "server": {"context_tokens": 32768, "threads": 4, "cache_type_k": "q8_0", "cache_type_v": "q8_0"},
            "limits": {"total_seconds": 1200, "rss_mib": 7168, "minimum_available_mib": 1536, "generation_requests": 2},
            "automatic_code_execution": False, "official_submission_allowed": False}


def proposed_files(content, originals):
    """Validate all original snippets before replacing them; never execute model output."""
    value = json.loads(content)
    if not isinstance(value, dict) or not isinstance(value.get("edits"), list) or len(value["edits"]) > 32:
        raise ValueError("Expected one JSON object with at most 32 exact snippet edits")
    changes = {}
    for edit in value["edits"]:
        if not isinstance(edit, dict) or set(edit) != {"path", "old", "new"}:
            raise ValueError("An edit must contain exactly path, old and new")
        path, old, new = edit["path"], edit["old"], edit["new"]
        if not isinstance(path, str) or path not in originals or "\\" in path:
            raise ValueError("Edit is outside the supplied production context")
        parts = PurePosixPath(path)
        if parts.is_absolute() or ".." in parts.parts or "tests" in parts.parts:
            raise ValueError("Invalid production file path")
        if not isinstance(old, str) or not old or not isinstance(new, str) or len(new.encode()) > 100000:
            raise ValueError("Invalid or oversized snippet")
        source = originals[path]
        if source.count(old) != 1:
            raise ValueError("Original snippet is absent or ambiguous")
        begin = source.index(old)
        changes.setdefault(path, []).append((begin, begin + len(old), new))
    output = dict(originals)
    for path, edits in changes.items():
        ordered = sorted(edits)
        if any(ordered[i][1] > ordered[i + 1][0] for i in range(len(ordered) - 1)):
            raise ValueError("Original edit spans overlap")
        for begin, end, new in reversed(ordered):
            output[path] = output[path][:begin] + new + output[path][end:]
    return output
