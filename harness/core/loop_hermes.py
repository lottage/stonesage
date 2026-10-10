"""
Hermes-3 XML Function Calling Protocol & Structured Reasoning Loop.
Parses <tools>, <tool_call>, and <tool_response> tags while isolating
<thought> and <think> reasoning traces for clean execution.
"""

import re
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("Harness.HermesLoop")

class HermesLoop:
    TOOL_CALL_REGEX = re.compile(r"<tool_call>\s*({.*?})\s*</tool_call>", re.DOTALL)
    THINK_REGEX = re.compile(r"<(?:think|thought)>(.*?)</(?:think|thought)>", re.DOTALL)

    @classmethod
    def format_system_prompt_with_tools(cls, system_prompt: str, tools: List[Dict[str, Any]]) -> str:
        """Injects tools into the prompt adhering to the Hermes-3 XML format."""
        if not tools:
            return system_prompt

        tools_xml = "<tools>\n"
        for t in tools:
            tools_xml += json.dumps(t, indent=2) + "\n"
        tools_xml += "</tools>"

        instructions = (
            "\n\nYou have access to the following tools. To invoke a tool, you MUST wrap a valid JSON payload "
            "inside <tool_call> and </tool_call> tags:\n"
            "<tool_call>\n{\"name\": \"function_name\", \"arguments\": {\"arg1\": \"val1\"}}\n</tool_call>\n"
            "Wrap your internal reasoning inside <think> and </think> before generating the final response or tool call.\n"
        )
        return f"{system_prompt}\n{instructions}\n{tools_xml}"

    @classmethod
    def extract_thoughts_and_content(cls, raw_text: str) -> Tuple[str, str]:
        """Separates internal reasoning (<think>...</think>) from user-facing text."""
        thoughts = []
        for match in cls.THINK_REGEX.finditer(raw_text):
            thoughts.append(match.group(1).strip())
        cleaned_text = cls.THINK_REGEX.sub("", raw_text).strip()
        thought_summary = "\n\n".join(thoughts)
        return thought_summary, cleaned_text

    @classmethod
    def extract_tool_calls(cls, text: str) -> List[Dict[str, Any]]:
        """Extracts JSON tool calls enclosed within <tool_call> tags."""
        calls = []
        for match in cls.TOOL_CALL_REGEX.finditer(text):
            raw_json = match.group(1).strip()
            try:
                payload = json.loads(raw_json)
                if "name" in payload:
                    calls.append(payload)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse tool call JSON: {raw_json[:100]}... Error: {e}")
        return calls

    @classmethod
    def format_tool_response(cls, tool_name: str, result: Any) -> str:
        """Formats the result of a tool execution back to the model."""
        res_str = result if isinstance(result, str) else json.dumps(result, indent=2)
        return f"<tool_response>\n{{\"name\": \"{tool_name}\", \"content\": {json.dumps(res_str)}}}\n</tool_response>"
