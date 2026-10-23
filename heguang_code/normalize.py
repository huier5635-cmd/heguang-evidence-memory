"""Extract original visible trajectory fields. Never execute commands or generate lessons."""
import json

PAIR_COMMAND_LIMIT = 8192

def source_view(message):
    content = message.content or ""
    extra = message.extra
    fields = []
    seen = set()

    def append(label, text):
        if isinstance(text, str) and text.strip() and text not in seen:
            fields.append((label, text))
            seen.add(text)

    output = extra.get("raw_output")
    if isinstance(output, str) and output:
        append("Tool output", output)
    else:
        append("Content", content)
    for action in extra.get("actions", []) if isinstance(extra.get("actions"), list) else []:
        if isinstance(action, dict):
            append("Command", action.get("command"))
    calls = list(message.tool_calls)
    if message.function_call:
        calls.append({"function": message.function_call})
    for call in calls:
        function = call.get("function", {})
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except ValueError:
                parsed = None
        else:
            parsed = arguments
        if isinstance(parsed, dict) and isinstance(parsed.get("command"), str):
            append("Command", parsed["command"])
        elif isinstance(arguments, str):
            append("Tool arguments", arguments)
        elif isinstance(arguments, dict):
            append("Tool arguments", json.dumps(arguments, ensure_ascii=False, sort_keys=True))
    append("Historical final patch", extra.get("submission"))
    if len(fields) == 1 and fields[0] == ("Content", content):
        return content, "content"
    return "\n\n".join(label + ":\n" + text for label, text in fields), "trajectory_fields_v1"


def commands_in(message):
    if message.role != "assistant":
        return []
    commands = []
    actions = message.extra.get("actions", [])
    for action in actions if isinstance(actions, list) else []:
        if isinstance(action, dict) and isinstance(action.get("tool_call_id"), str) and isinstance(action.get("command"), str):
            commands.append((action["tool_call_id"], action["command"]))
    for call in message.tool_calls:
        function = call.get("function", {})
        if not isinstance(function, dict) or not isinstance(call.get("id"), str):
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                continue
        if isinstance(arguments, dict) and isinstance(arguments.get("command"), str):
            commands.append((call["id"], arguments["command"]))
    return commands


def trajectory_views(messages, preceding=None, ambiguous=None):
    """Pair earlier unambiguous identities, optionally seeded from this session.

    Seed dictionaries are updated in place so the caller can commit them atomically.
    Integer parents refer to this batch; string parents are persisted source IDs.
    """
    preceding = {} if preceding is None else preceding
    ambiguous = set() if ambiguous is None else ambiguous
    for position, message in enumerate(messages):
        text, representation = source_view(message)
        paired_position = None
        tool_id = message.model_extra.get("tool_call_id")
        if message.role == "tool" and isinstance(tool_id, str) and tool_id in preceding and tool_id not in ambiguous:
            paired_position, command = preceding[tool_id]
            text = "Command:\n" + command + "\n\n" + text
            representation = "trajectory_step_v1"
        yield text, representation, paired_position
        for tool_id, command in commands_in(message):
            if tool_id and command:
                if len(command) > PAIR_COMMAND_LIMIT:
                    # The original message is still retained in full. Do not
                    # duplicate an arbitrarily large command into many outputs.
                    ambiguous.add(tool_id)
                    command = ""
                if tool_id in preceding and preceding[tool_id] != (position, command):
                    # An identity reused by another turn or conflicting fields
                    # cannot safely identify the parent command.
                    ambiguous.add(tool_id)
                preceding[tool_id] = (position, command)
