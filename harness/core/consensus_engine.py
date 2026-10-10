"""
Multi-Stream Cross-Examination & Consensus Engine.
Orchestrates multi-agent peer debate and synthesis across homelab nodes:
1. Stage 1: Parallel Independent Generation
2. Stage 2: Bilateral Peer Review & Cross-Critique
3. Stage 3: Weighted Founder Adjudication & Synthesis
"""

import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable

from .llama_client import LlamaClient, StreamChunk
from .context_fabric import context_fabric
from .founders import get_founder, FOUNDERS
from ..config import fleet_config

logger = logging.getLogger("Harness.ConsensusEngine")


@dataclass
class ConsensusPlan:
    task_prompt: str
    lead_founder_id: str
    agents: List[Dict[str, Any]]
    domain: str = "software"


@dataclass
class ConsensusResult:
    task_prompt: str
    lead_founder_id: str
    initial_solutions: Dict[str, str] = field(default_factory=dict)
    peer_critiques: Dict[str, Dict[str, str]] = field(default_factory=dict)
    final_synthesis: str = ""
    elapsed_seconds: float = 0.0
    status: str = "completed"
    error: Optional[str] = None


class ConsensusEngine:
    """
    Executes the 3-stage consensus pipeline across distributed homelab nodes.
    Supports live progress callbacks for streaming terminal UI.
    """

    def __init__(self):
        pass

    def build_plan(
        self,
        prompt: str,
        agent_ids: Optional[List[str]] = None,
        domain: str = "software"
    ) -> ConsensusPlan:
        """Constructs an executable multi-agent consensus plan."""
        if not agent_ids:
            agent_ids = ["aevum", "sentinel"]

        founder = get_founder(domain) or FOUNDERS["aevum"]
        agent_configs = []

        # Map available fleet nodes to agents
        nodes = list(fleet_config.nodes.keys())
        for idx, aid in enumerate(agent_ids):
            node_key = nodes[idx % len(nodes)]
            node_obj = fleet_config.nodes[node_key]
            agent_configs.append({
                "agent_id": aid,
                "node_id": node_key,
                "endpoint_url": node_obj.base_url,
                "model_name": "local-model"
            })

        return ConsensusPlan(
            task_prompt=prompt,
            lead_founder_id=founder.id,
            agents=agent_configs,
            domain=domain
        )

    async def execute_consensus(
        self,
        plan: ConsensusPlan,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ) -> ConsensusResult:
        """
        Executes the 3-stage consensus pipeline asynchronously.
        """
        start_time = time.time()
        result = ConsensusResult(
            task_prompt=plan.task_prompt,
            lead_founder_id=plan.lead_founder_id
        )

        def report(stage: str, data: Dict[str, Any]):
            if progress_callback:
                try:
                    progress_callback(stage, data)
                except Exception:
                    pass

        # -------------------------------------------------------------
        # STAGE 1: Parallel Independent Generation
        # -------------------------------------------------------------
        report("stage_start", {"stage": 1, "title": "Parallel Independent Generation"})

        async def _generate_agent_solution(ag: Dict[str, Any]) -> tuple[str, str]:
            aid = ag["agent_id"]
            client = LlamaClient(base_url=ag["endpoint_url"])
            system_prompt = context_fabric.compile_dynamic_turn(
                user_query=plan.task_prompt,
                agent_id=aid
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": plan.task_prompt}
            ]
            content_accum = []
            try:
                async for chunk in client.chat_stream(messages, request_id=f"sol_{aid}", max_tokens=1024):
                    if chunk.chunk_type == "output":
                        content_accum.append(chunk.content)
                    elif chunk.chunk_type == "error":
                        logger.error(f"Error in solution stream for {aid}: {chunk.content}")
                        content_accum.append(f"\n[Model Error: {chunk.content}]\n")
                        break
            except Exception as e:
                logger.warning(f"Error generating solution for {aid}: {e}")
                return aid, f"(Failed to generate: {e})"

            return aid, "".join(content_accum).strip()

        gen_tasks = [_generate_agent_solution(ag) for ag in plan.agents]
        solutions = await asyncio.gather(*gen_tasks)
        for aid, sol in solutions:
            result.initial_solutions[aid] = sol
            report("solution_ready", {"agent_id": aid, "solution_snippet": sol[:120]})

        # -------------------------------------------------------------
        # STAGE 2: Bilateral Peer Review & Cross-Critique
        # -------------------------------------------------------------
        report("stage_start", {"stage": 2, "title": "Bilateral Peer Review & Cross-Critique"})

        critique_tasks = []
        for ag in plan.agents:
            reviewer_id = ag["agent_id"]
            client = LlamaClient(base_url=ag["endpoint_url"])

            # Review all other agents' solutions
            for peer_id, peer_sol in result.initial_solutions.items():
                if peer_id == reviewer_id:
                    continue

                async def _critique(rev=reviewer_id, peer=peer_id, sol_text=peer_sol, cl=client):
                    critique_prompt = (
                        f"Target Challenge: {plan.task_prompt}\n\n"
                        f"Proposed Solution from Peer Agent '{peer}':\n{sol_text}\n\n"
                        f"Directive: Critically analyze this solution. Identify potential bugs, concurrency hazards, "
                        f"edge-case failures, and architectural trade-offs. Provide concise, constructive critique."
                    )
                    sys_prompt = context_fabric.compile_dynamic_turn(user_query=critique_prompt, agent_id=rev)
                    messages = [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": critique_prompt}
                    ]
                    accum = []
                    try:
                        async for chunk in cl.chat_stream(messages, request_id=f"crit_{rev}_{peer}", max_tokens=512):
                            if chunk.chunk_type == "output":
                                accum.append(chunk.content)
                            elif chunk.chunk_type == "error":
                                logger.error(f"Error in critique stream for {rev}->{peer}: {chunk.content}")
                                accum.append(f"\n[Model Error: {chunk.content}]\n")
                                break
                    except Exception as e:
                        return rev, peer, f"(Critique failed: {e})"
                    return rev, peer, "".join(accum).strip()

                critique_tasks.append(_critique())

        critique_results = await asyncio.gather(*critique_tasks)
        for rev, peer, crit_text in critique_results:
            result.peer_critiques.setdefault(rev, {})[peer] = crit_text
            report("critique_ready", {"reviewer": rev, "peer": peer, "critique_snippet": crit_text[:120]})

        # -------------------------------------------------------------
        # STAGE 3: Weighted Founder Adjudication & Synthesis
        # -------------------------------------------------------------
        report("stage_start", {"stage": 3, "title": f"Founder Adjudication & Synthesis ({plan.lead_founder_id.title()})"})

        founder = get_founder(plan.lead_founder_id) or FOUNDERS["aevum"]
        # Use primary coordinator node for Founder adjudication
        lead_node = fleet_config.nodes.get("node1_primary")
        lead_url = lead_node.base_url if lead_node else plan.agents[0]["endpoint_url"]
        lead_client = LlamaClient(base_url=lead_url)

        dossier_lines = [
            f"=== MULTI-AGENT CONSENSUS DOSSIER ===",
            f"Core Problem: {plan.task_prompt}\n",
            "--- PROPOSED SOLUTIONS ---"
        ]
        for aid, sol in result.initial_solutions.items():
            dossier_lines.append(f"\n[Agent {aid}]:\n{sol}")

        dossier_lines.append("\n--- PEER CRITIQUES ---")
        for rev, peers in result.peer_critiques.items():
            for peer, crit in peers.items():
                dossier_lines.append(f"\n[{rev} critiquing {peer}]:\n{crit}")

        dossier_lines.append(
            f"\n--- DIRECTIVE FOR FOUNDER {founder.name.upper()} ---:\n"
            f"Review the solutions and peer critiques against your core pillar invariants:\n"
            f"{chr(10).join('• ' + inv for inv in founder.invariants)}\n\n"
            f"Synthesize the single definitive, production-ready, mathematically rigorous consensus solution. "
            f"Address all identified flaws and preserve all valid insights."
        )

        full_dossier = "\n".join(dossier_lines)
        sys_prompt = context_fabric.compile_dynamic_turn(user_query=full_dossier, agent_id=founder.id)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": full_dossier}
        ]

        synth_accum = []
        try:
            async for chunk in lead_client.chat_stream(messages, request_id="founder_synth", max_tokens=2048):
                if chunk.chunk_type == "output":
                    synth_accum.append(chunk.content)
                    report("synthesis_stream", {"token": chunk.content})
                elif chunk.chunk_type == "error":
                    logger.error(f"Founder synthesis model error: {chunk.content}")
                    synth_accum.append(f"\n[Model Error: {chunk.content}]\n")
                    result.error = chunk.content
                    break
        except Exception as e:
            logger.error(f"Founder synthesis failed: {e}")
            result.error = str(e)
            result.final_synthesis = f"(Synthesis failed: {e})"
            result.status = "error"
            result.elapsed_seconds = round(time.time() - start_time, 2)
            return result

        result.final_synthesis = "".join(synth_accum).strip()
        result.elapsed_seconds = round(time.time() - start_time, 2)
        report("consensus_complete", {"elapsed_s": result.elapsed_seconds})
        return result


# Global consensus engine
consensus_engine = ConsensusEngine()
