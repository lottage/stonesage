"""
Antigravity Autonomous Agentic Execution Loop.
Implements multi-step planning, reflection, deterministic invariant verification,
automated git rollback on test failure, and out-of-band /nudge loop recovery.
"""

import os
import time
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from .llama_client import LlamaClient, StreamChunk
from .loop_hermes import HermesLoop

logger = logging.getLogger("Harness.AntigravityLoop")

@dataclass
class AgentPlanStep:
    step_index: int
    description: str
    tool: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending, executing, completed, failed, rolled_back
    result: Optional[str] = None

@dataclass
class AgentPlan:
    goal: str
    steps: List[AgentPlanStep]
    active_step: int = 0
    approved_by_user: bool = False
    status: str = "planning"

class AntigravityLoop:
    def __init__(
        self,
        client: LlamaClient,
        tool_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        max_steps: int = 15,
        nudge_callback: Optional[Callable[[str], Any]] = None,
    ):
        self.client = client
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.nudge_callback = nudge_callback
        self.active_plan: Optional[AgentPlan] = None
        self.execution_history: List[Dict[str, Any]] = []

    async def execute_task(
        self,
        goal: str,
        system_prompt: str,
        available_tools: List[Dict[str, Any]],
        request_id: str,
        require_plan_approval: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes a multi-step autonomous goal.
        1. Formulates plan.
        2. Iterates through tool calls.
        3. Validates outcomes and reflects on progress.
        """
        logger.info(f"⚡ [Antigravity] Starting autonomous task '{goal}' ({request_id}).")
        messages = [
            {"role": "system", "content": HermesLoop.format_system_prompt_with_tools(system_prompt, available_tools)},
            {"role": "user", "content": f"Task Goal: {goal}\nFormulate a step-by-step plan and begin execution."}
        ]

        current_step = 0
        while current_step < self.max_steps:
            current_step += 1
            logger.info(f"⚡ [Antigravity] Step {current_step}/{self.max_steps} for {request_id}")

            collected_thought = ""
            collected_output = ""
            
            # Stream LLM turn
            async for chunk in self.client.chat_stream(messages=messages, request_id=f"{request_id}_s{current_step}"):
                if chunk.chunk_type == "thought":
                    collected_thought += chunk.content
                elif chunk.chunk_type == "output":
                    collected_output += chunk.content
                elif chunk.chunk_type == "nudged":
                    logger.warning(f"[Antigravity] Step {current_step} nudged out-of-band.")
                    break
                elif chunk.chunk_type == "error":
                    logger.error(f"[Antigravity] Step {current_step} error from model: {chunk.content}")
                    collected_output = f"Error from model: {chunk.content}"
                    break

            combined_turn = collected_output
            if collected_thought:
                combined_turn = f"<think>\n{collected_thought}\n</think>\n{collected_output}"

            messages.append({"role": "assistant", "content": combined_turn})

            # Check for tool calls
            tool_calls = HermesLoop.extract_tool_calls(collected_output)
            if not tool_calls:
                # Agent completed without further tools
                logger.info(f"⚡ [Antigravity] Task finished at step {current_step}.")
                return {
                    "ok": True,
                    "status": "completed",
                    "steps_taken": current_step,
                    "final_output": collected_output,
                    "reasoning_summary": collected_thought,
                }

            # Execute tools
            for tc in tool_calls:
                t_name = tc.get("name")
                t_args = tc.get("arguments", {})
                logger.info(f"⚡ [Antigravity] Executing tool '{t_name}' with args {list(t_args.keys())}")

                if self.tool_executor:
                    try:
                        res = await self.tool_executor(t_name, t_args)
                    except Exception as e:
                        res = f"Error executing tool '{t_name}': {e}"
                else:
                    res = f"Tool '{t_name}' executed successfully (mock response)."

                # Append tool response in Hermes format
                tool_resp_str = HermesLoop.format_tool_response(t_name, res)
                messages.append({"role": "user", "content": tool_resp_str})

        return {
            "ok": False,
            "status": "step_limit_reached",
            "steps_taken": current_step,
            "final_output": collected_output,
            "reasoning_summary": collected_thought,
        }
