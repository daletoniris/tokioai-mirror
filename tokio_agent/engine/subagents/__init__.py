"""
Subagent System — Parallel worker orchestration for TokioAI.

Inspired by Claude Code's AgentTool + Coordinator pattern. The main agent
can spawn autonomous workers that research, implement, and verify tasks
independently. Workers run their own Think→Act→Observe loops with
restricted tool access.

Architecture:
  - SubAgent: Independent worker with its own LLM loop
  - SubAgentManager: Tracks running/completed workers, collects results
  - Coordinator prompt: When the agent needs to orchestrate complex tasks
"""

from .worker import SubAgent, SubAgentResult
from .manager import SubAgentManager

__all__ = ["SubAgent", "SubAgentResult", "SubAgentManager"]
