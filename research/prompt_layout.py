"""A development prompt-layout change, never imported by the memory service."""
import copy
import json


def current_task_last(plan):
    if not plan.get("public_inputs_verified") or plan.get("official_data") is not False:
        raise ValueError("Only verified public development inputs are allowed")
    before = plan["messages"]
    common = before["without_memory"]
    if len(common) != 2 or before["with_memory"][:-1] != common:
        raise ValueError("Expected the original shared task/source and one historical-context message")
    visible = json.loads(common[1]["content"])
    problem = visible["public_task"]["problem_statement"]
    final = {"role": "user", "content": (
        "Fix the repository issue below using the current baseline source supplied above. "
        "Return the exact JSON edits requested by the system.\n\n" + problem)}
    changed = copy.deepcopy(plan)
    changed["messages"] = {
        "without_memory": [*copy.deepcopy(common), final],
        "with_memory": [copy.deepcopy(common[0]), copy.deepcopy(before["with_memory"][-1]),
                        copy.deepcopy(common[1]), copy.deepcopy(final)],
    }
    changed["prompt_layout"] = "historical context before current baseline; original issue repeated verbatim last in both arms"
    changed["layout_scope"] = "Public one-shot development harness only; official Answer/Agent prompts and memory-service output are unchanged"
    return changed
