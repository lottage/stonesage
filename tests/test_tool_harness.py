#!/usr/bin/env python3
"""
Unit tests for StoneSage Universal Tool Harness:
- Core tool execution
- Model tool hallucination alias resolution
- A-MEM atomic fact card generation (< 35 tokens)
- FIFO eviction policy for dynamic tools (MAX_DYNAMIC_TOOLS_IN_AMEM = 20)
- Ingesting tools from URL
- Multi-syntax tool call extraction (Qwen/ChatML, XML, direct call:)
"""

import os
import sys
import json
import unittest
import tempfile
from unittest.mock import patch, MagicMock

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "StoneSage", "backend")))

from stonesage.tool_harness import ToolRegistry, extract_tool_calls_from_text, MAX_DYNAMIC_TOOLS_IN_AMEM


class TestToolHarness(unittest.TestCase):

    def setUp(self):
        self.registry = ToolRegistry(amem_enabled=False, qdrant_enabled=False)

    def test_core_tools_registered(self):
        tools = self.registry.list_tools()
        tool_names = [t["name"] for t in tools]
        self.assertIn("read_file", tool_names)
        self.assertIn("write_file", tool_names)
        self.assertIn("list_directory", tool_names)
        self.assertIn("search_codebase", tool_names)
        self.assertIn("ingest_knowledge", tool_names)
        self.assertIn("execute_command", tool_names)
        self.assertIn("download_model", tool_names)
        self.assertIn("ingest_tool_from_url", tool_names)

        # Ensure all core tools are flagged is_core
        for t in tools:
            if t["name"] in ["read_file", "write_file", "list_directory"]:
                self.assertTrue(t["is_core"])

    def test_built_in_file_tools(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".txt") as tf:
            tf.write("line 1\nline 2\nline 3\n")
            temp_path = tf.name

        try:
            # Test read_file
            read_res = self.registry.execute_tool("read_file", {"path": temp_path})
            self.assertTrue(read_res["ok"])
            self.assertEqual(read_res["total_lines"], 3)
            self.assertIn("line 2", read_res["content"])

            # Test write_file
            write_res = self.registry.execute_tool("write_file", {"path": temp_path, "content": "updated line\n"})
            self.assertTrue(write_res["ok"])

            read_res2 = self.registry.execute_tool("read_file", {"path": temp_path})
            self.assertTrue(read_res2["ok"])
            self.assertEqual(read_res2["content"], "updated line\n")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_alias_resolution_for_hallucinated_mcp_tools(self):
        # 1. chat_mcp__ingest -> ingest_knowledge
        res = self.registry.execute_tool("chat_mcp__ingest", {"text": "hello knowledge"})
        self.assertNotEqual(res.get("error"), "Unknown tool: chat_mcp__ingest")

        # 2. chat_mcp__search -> search_codebase
        res_search = self.registry.execute_tool("chat_mcp__search", {"query": "test"})
        self.assertNotEqual(res_search.get("error"), "Unknown tool: chat_mcp__search")

    def test_amem_card_token_limit(self):
        cards = self.registry.get_amem_tool_cards()
        self.assertTrue(len(cards) > 0)
        for c in cards:
            card_str = c.get("card", "")
            self.assertTrue(len(card_str) > 0)
            token_est = len(card_str.split())
            self.assertLess(token_est, 35, f"Card exceeds 35 token limit: {card_str}")

    def test_fifo_eviction_policy_on_overflow(self):
        # Register 25 dynamic tools to trigger FIFO eviction
        # MAX_DYNAMIC_TOOLS_IN_AMEM is 20
        registered = []
        for i in range(1, 26):
            t_name = f"dynamic_tool_{i:02d}"
            res = self.registry.register_custom_tool(
                name=t_name,
                description=f"Dynamic tool number {i}",
                parameters={"type": "object", "properties": {"x": {"type": "string"}}},
                amem_card=f"A-MEM: dynamic_tool_{i:02d} - performs task {i}"
            )
            self.assertTrue(res["ok"])
            registered.append(t_name)

        # Active dynamic tools in FIFO queue should be capped at MAX_DYNAMIC_TOOLS_IN_AMEM (20)
        self.assertEqual(len(self.registry.dynamic_tools_order), MAX_DYNAMIC_TOOLS_IN_AMEM)

        # Tools 1 through 5 should have been evicted from dynamic_tools_order
        for i in range(1, 6):
            evicted_name = f"dynamic_tool_{i:02d}"
            self.assertNotIn(evicted_name, self.registry.dynamic_tools_order)

        # Tools 6 through 25 should still be present in FIFO order
        for i in range(6, 26):
            kept_name = f"dynamic_tool_{i:02d}"
            self.assertIn(kept_name, self.registry.dynamic_tools_order)

        # Core tools must NEVER be evicted
        core_names = ["read_file", "write_file", "list_directory"]
        all_tool_names = [t["name"] for t in self.registry.list_tools()]
        for c_name in core_names:
            self.assertIn(c_name, all_tool_names)

    def test_url_tool_ingestion_valid_json(self):
        mock_schema = {
            "name": "remote_weather_lookup",
            "description": "Fetch weather forecast for city",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"]
            }
        }
        mock_body = json.dumps(mock_schema).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_body
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = self.registry.ingest_tool_from_url("https://example.com/tools/weather.json")
            self.assertTrue(res["ok"])
            self.assertEqual(res["tool_name"], "remote_weather_lookup")

            # Check that it is listed and in dynamic tools
            tool_entry = self.registry.get_tool("remote_weather_lookup")
            self.assertIsNotNone(tool_entry)
            self.assertEqual(tool_entry["description"], "Fetch weather forecast for city")
            self.assertIn("remote_weather_lookup", self.registry.dynamic_tools_order)

    def test_extract_tool_calls_from_text(self):
        # 1. Qwen / ChatML syntax
        qwen_sample = '<|tool_call>call:read_file{"path": "config.json"}<tool_call|>'
        calls = extract_tool_calls_from_text(qwen_sample)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "read_file")
        self.assertEqual(calls[0][1], {"path": "config.json"})

        # 2. Qwen pseudo-markup syntax: <|"|>value<|"|>
        qwen_mangled = '<|tool_call>call:write_file{"path": <|"|>test.txt<|"|>, "content": <|"|>hello<|"|>}<tool_call|>'
        calls2 = extract_tool_calls_from_text(qwen_mangled)
        self.assertEqual(len(calls2), 1)
        self.assertEqual(calls2[0][0], "write_file")
        self.assertEqual(calls2[0][1].get("path"), "test.txt")

        # 3. XML syntax
        xml_sample = '<tool_call>{"name": "list_directory", "arguments": {"path": "/tmp"}}</tool_call>'
        calls3 = extract_tool_calls_from_text(xml_sample)
        self.assertEqual(len(calls3), 1)
        self.assertEqual(calls3[0][0], "list_directory")
        self.assertEqual(calls3[0][1], {"path": "/tmp"})

        # 4. Direct call: syntax
        direct_sample = 'I will read the file: call:read_file{"path": "main.py"}'
        calls4 = extract_tool_calls_from_text(direct_sample)
        self.assertEqual(len(calls4), 1)
        self.assertEqual(calls4[0][0], "read_file")
        self.assertEqual(calls4[0][1], {"path": "main.py"})


if __name__ == "__main__":
    unittest.main()
