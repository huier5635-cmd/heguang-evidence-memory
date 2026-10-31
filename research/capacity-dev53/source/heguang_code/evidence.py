"""Expose stored evidence kinds and replace provably covered command-only spans."""
import json

from .selection import source_record


def paired_observations(rows):
    """Keep the better original rank when a tool observation contains its command.

    A pairing ID, session, role, zero offset and exact text containment must all
    agree. Ordinary similar passages and unrelated sessions are never merged.
    Every returned body is an intact input body from one original source.
    """
    if len(rows) > 100 or sum(len(r['content']) for r in rows) > 512000:
        raise RuntimeError('Evidence candidate limit exceeded')
    parsed = [source_record(row) for row in rows]
    replacement = {}
    for rank, (parent, command) in enumerate(parsed):
        if parent['role'] != 'assistant' or parent['characters'][0] != 0 or not command.startswith('Command:\n'):
            continue
        for other, (tool, observation) in enumerate(parsed):
            if (tool['role'] == 'tool' and tool.get('paired_source_id') == parent['source_id']
                    and tool['session_id'] == parent['session_id'] and tool['characters'][0] == 0
                    and observation.startswith(command + '\n\n')):
                replacement[rank] = other
                break
    # Both the original tool row and a covered command row resolve to that tool.
    # Preserve the best contributor's rank/score without joining source bodies.
    best_scores = {}
    for rank, row in enumerate(rows):
        selected = replacement.get(rank, rank)
        best_scores[selected] = max(best_scores.get(selected, row['score']), row['score'])
    emitted, result = set(), []
    for rank, row in enumerate(rows):
        selected = replacement.get(rank, rank)
        if selected in emitted:
            continue
        emitted.add(selected)
        chosen = rows[selected]
        result.append({**chosen, 'score': best_scores[selected]})
    return result


def typed_evidence(rows):
    """Add provenance metadata, without presenting stored code as current code."""
    output = []
    for row in rows:
        metadata, body = source_record(row)
        kind = {'tool': 'stored_tool_observation', 'assistant': 'stored_assistant_message',
                'user': 'stored_user_message'}.get(metadata['role'], 'stored_message')
        metadata = {**metadata, 'evidence_kind': kind, 'current_task_verified': False}
        output.append({**row, 'content': '[source ' + json.dumps(metadata, ensure_ascii=False,
                       separators=(',', ':')) + ']\n' + body})
    return output
