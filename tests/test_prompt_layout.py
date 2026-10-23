import copy
import json

import pytest

from research.prompt_layout import current_task_last


def test_layout_keeps_source_task_and_treatment_identical_without_mutating_frozen_input():
    system = {"role": "system", "content": "Produce exact edits."}
    source = {"role": "user", "content": json.dumps({"public_task": {"problem_statement": "Repair parse_packet()."},
        "unmodified_base_source_files": [{"path": "src/parser.py", "source": "original source"}]})}
    memory = {"role": "user", "content": "historical evidence, unchanged"}
    plan = {"public_inputs_verified": True, "official_data": False,
            "messages": {"without_memory": [system, source], "with_memory": [system, source, memory]}}
    frozen = copy.deepcopy(plan)
    result = current_task_last(plan)
    no = result["messages"]["without_memory"]
    yes = result["messages"]["with_memory"]
    assert plan == frozen
    assert no == [yes[0], *yes[2:]]
    assert no[1] == source and yes[1] == memory
    assert no[-1]["content"].endswith("Repair parse_packet().")


def test_layout_rejects_unmatched_baselines():
    with pytest.raises(ValueError):
        current_task_last({"public_inputs_verified": True, "official_data": False,
            "messages": {"without_memory": [{}, {}], "with_memory": [{}, {"different": "source"}, {}]}})
