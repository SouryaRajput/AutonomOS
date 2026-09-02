"""
Message Sanitizer & Channel Separation Adapter.

Maintains a strict architectural boundary between:
1. USER-FACING CONVERSATION CHANNEL (natural-language assistant prose, progress, results)
2. INTERNAL EXECUTION CHANNEL (tool calls, tool arguments, raw JSON payloads, worker lifecycle events)
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional


TOOL_ACTION_KEYS = {
    "action",
    "tool",
    "tool_id",
    "tool_name",
    "function",
    "function_call",
    "tool_calls",
    "command",
    "crawler_action",
    "research_action",
}

KNOWN_TOOL_NAMES = {
    "list_directory",
    "read_file",
    "write_file",
    "create_file",
    "delete_file",
    "file_exists",
    "stat_file",
    "run_command",
    "exec",
    "web_search",
    "web_fetch",
    "crawler_allocation",
    "spawn_crawler",
    "execute_crawler_task",
    "evidence_evaluation",
    "plan_update",
    "create_task",
    "assign_task",
    "renegotiate_plan",
}


def is_internal_tool_payload(obj: Any) -> bool:
    """
    Determine whether a Python object represents an internal tool call / execution payload.
    """
    if isinstance(obj, dict):
        # 1. Direct tool keys check
        keys_lower = {str(k).lower() for k in obj.keys()}
        if keys_lower & TOOL_ACTION_KEYS:
            action_val = str(obj.get("action") or obj.get("tool") or obj.get("tool_id") or obj.get("name") or obj.get("command") or "").lower()
            if action_val in KNOWN_TOOL_NAMES:
                return True
            if "arguments" in keys_lower or "parameters" in keys_lower or "path" in keys_lower:
                return True
            if "tool" in keys_lower or "tool_id" in keys_lower or "tool_calls" in keys_lower:
                return True

        # 2. OpenAI / Anthropic format
        if obj.get("type") == "function" and "function" in obj:
            return True
        if "function_call" in obj:
            return True
        if "tool_calls" in obj and isinstance(obj["tool_calls"], list):
            return True

    return False


def is_json_tool_string(text: str) -> tuple[bool, Optional[dict[str, Any]]]:
    """
    Check if a string or substring parses to an internal tool execution dictionary.
    """
    trimmed = text.strip()
    if not (trimmed.startswith("{") and trimmed.endswith("}")):
        return False, None

    try:
        data = json.loads(trimmed)
        if isinstance(data, dict) and is_internal_tool_payload(data):
            return True, data
    except Exception:
        pass

    return False, None


def extract_user_facing_narrative(
    raw_text: str,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Separate raw text into:
    1. Clean user-facing narrative (string for conversation transcript)
    2. Extracted internal tool calls (list of dicts for internal execution channel)

    Preserves natural language prose, progress updates, explanations, and legitimate markdown code fences.
    Strips raw JSON tool calls, action blocks, and internal function invocation fragments.
    """
    if not raw_text:
        return "", []

    text = raw_text.strip()
    extracted_tools: list[dict[str, Any]] = []

    # 1. First, check and extract code fences that contain internal tool JSON
    def replace_code_fence(match: re.Match) -> str:
        fence_content = match.group(1).strip()
        is_tool, payload = is_json_tool_string(fence_content)
        if is_tool and payload:
            extracted_tools.append(payload)
            return ""  # Remove internal tool code block from user narrative
        # Otherwise, preserve normal code block
        return match.group(0)

    # Match ```(json|tool|tool_call)? ... ```
    text = re.sub(r"```(?:json|tool|tool_call|function_call)?\s*([\s\S]*?)\s*```", replace_code_fence, text, flags=re.IGNORECASE)

    # 2. Extract embedded standalone raw JSON objects { ... }
    # Use balanced brace scanning to correctly extract complete JSON objects
    cleaned_segments: list[str] = []
    idx = 0
    length = len(text)

    while idx < length:
        # Find start of JSON object '{'
        start_brace = text.find("{", idx)
        if start_brace == -1:
            # Append remaining text
            cleaned_segments.append(text[idx:])
            break

        # Append preceding text narrative
        cleaned_segments.append(text[idx:start_brace])

        # Scan for matching closing brace '}'
        brace_depth = 0
        end_brace = -1
        in_string = False
        escape = False

        for i in range(start_brace, length):
            char = text[i]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if not in_string:
                if char == "{":
                    brace_depth += 1
                elif char == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        end_brace = i
                        break

        if end_brace != -1:
            candidate_json = text[start_brace : end_brace + 1]
            is_tool, payload = is_json_tool_string(candidate_json)
            if is_tool and payload:
                extracted_tools.append(payload)
                idx = end_brace + 1
                continue
            else:
                # Not a tool call, preserve original text
                cleaned_segments.append(candidate_json)
                idx = end_brace + 1
                continue
        else:
            # Unbalanced brace, append rest
            cleaned_segments.append(text[start_brace:])
            break

    result_narrative = "".join(cleaned_segments)

    # 3. Strip robotic / internal metadata lines if present
    result_narrative = re.sub(r"✓ Project understood\n?", "", result_narrative, flags=re.IGNORECASE)
    result_narrative = re.sub(r"✓ Project map created[^\n]*\n?", "", result_narrative, flags=re.IGNORECASE)
    result_narrative = re.sub(r"✓ Work plan prepared[^\n]*\n?", "", result_narrative, flags=re.IGNORECASE)
    result_narrative = re.sub(r"⏸ Workers inactive[^\n]*\n?", "", result_narrative, flags=re.IGNORECASE)
    result_narrative = re.sub(r"Decision:\s*\"?[^\n\"]*\"?\n?", "", result_narrative, flags=re.IGNORECASE)
    result_narrative = re.sub(r"Reason:\s*\"?[^\n\"]*\"?\n?", "", result_narrative, flags=re.IGNORECASE)
    result_narrative = re.sub(r"Planned TASK-\d+[^\n]*\n?", "", result_narrative, flags=re.IGNORECASE)
    result_narrative = re.sub(r"Context selected:\s*(\n\s*[-*]\s*`?[^\n]+`?)+", "", result_narrative, flags=re.IGNORECASE)

    # Clean up excess whitespace
    result_narrative = re.sub(r"\n{3,}", "\n\n", result_narrative).strip()

    return result_narrative, extracted_tools
