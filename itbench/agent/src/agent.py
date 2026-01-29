"""
SRE Diagnosis Agent using ReAct (Reason, Act, Observe) framework.

This agent intelligently collects system telemetry data (alerts, metrics, logs, etc.)
and produces structured diagnoses for Kubernetes incidents.
"""

import asyncio
import json
import logging
import os
import traceback
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
import litellm
from litellm import completion

from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, Message, Part, TaskState
from a2a.utils import get_message_text, new_agent_text_message


load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Prompt loading
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_file = PROMPTS_DIR / f"{name}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
    return prompt_file.read_text(encoding="utf-8")


# =============================================================================
# ReAct Framework Classes
# =============================================================================

class ActionType(Enum):
    """Available actions the agent can take."""
    FETCH_ALERTS = "fetch_alerts"
    FETCH_METRICS = "fetch_metrics"
    FETCH_K8S_EVENTS = "fetch_k8s_events"
    FETCH_K8S_OBJECTS = "fetch_k8s_objects"
    FETCH_OTEL_LOGS = "fetch_otel_logs"
    FETCH_OTEL_TRACES = "fetch_otel_traces"
    DIAGNOSE = "diagnose"
    FINISH = "finish"


@dataclass
class Observation:
    """Result of an action."""
    action: ActionType
    success: bool
    data: Any = None
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class ReActStep:
    """A single step in the ReAct loop."""
    step_number: int
    thought: str
    action: ActionType
    action_input: Optional[dict] = None
    observation: Optional[Observation] = None


@dataclass
class AgentState:
    """Current state of the ReAct agent."""
    scenario: str
    collected_data: dict = field(default_factory=dict)
    steps: list[ReActStep] = field(default_factory=list)
    diagnosis: Optional[dict] = None
    is_complete: bool = False
    max_steps: int = 10


# Data type mapping for actions
ACTION_TO_DATA_TYPE = {
    ActionType.FETCH_ALERTS: "alerts",
    ActionType.FETCH_METRICS: "metrics",
    ActionType.FETCH_K8S_EVENTS: "k8s_events",
    ActionType.FETCH_K8S_OBJECTS: "k8s_objects",
    ActionType.FETCH_OTEL_LOGS: "otel_logs",
    ActionType.FETCH_OTEL_TRACES: "otel_traces",
}

# Available data types that can be requested from the evaluator
AVAILABLE_DATA_TYPES = ["alerts", "metrics", "k8s_events", "k8s_objects", "otel_logs", "otel_traces"]


class Agent:
    """SRE Diagnosis Agent using ReAct framework."""

    def __init__(self):
        self.model = os.getenv("MODEL")
        self.provider = os.getenv("PROVIDER")
        self.base_url = os.getenv("URL")
        self.api_key = os.getenv("API_KEY")
        self._pending_data_request: Optional[str] = None
        self._state: Optional[AgentState] = None
        self._awaiting_response = False

    async def run(self, message: Message, updater: TaskUpdater) -> None:
        """Process incoming message and return response."""
        input_text = get_message_text(message)
        logger.info(f"Agent received: {input_text[:200]}...")

        try:
            msg = json.loads(input_text)
        except json.JSONDecodeError:
            # Not JSON - might be a simple text command
            await updater.add_artifact(
                parts=[Part(root=DataPart(data={"error": "Invalid JSON input"}))],
                name="Response",
            )
            return

        msg_type = msg.get("type")

        if msg_type == "scenario":
            # New scenario - initialize state and start ReAct loop
            scenario = msg.get("scenario", "unknown")
            available_types = msg.get("available_data_types", AVAILABLE_DATA_TYPES)
            
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(f"Starting diagnosis for {scenario}...")
            )
            
            self._state = AgentState(scenario=scenario)
            
            # Start ReAct loop - first step is always to reason
            response = await self._react_step(updater)
            
            await updater.add_artifact(
                parts=[Part(root=DataPart(data=response))],
                name="Response",
            )

        elif msg_type == "data_response":
            # Received data from evaluator
            data_type = msg.get("data_type")
            content = msg.get("content", {})
            
            if self._state and data_type:
                self._state.collected_data[data_type] = content
                logger.info(f"Stored {data_type} data: {len(str(content))} chars")
            
            # Continue ReAct loop
            response = await self._react_step(updater)
            
            await updater.add_artifact(
                parts=[Part(root=DataPart(data=response))],
                name="Response",
            )

        elif msg_type == "acknowledged":
            # Diagnosis was acknowledged, check for next action
            next_action = msg.get("next_action", {})
            if next_action.get("type") == "done":
                # All scenarios complete
                await updater.add_artifact(
                    parts=[Part(root=DataPart(data={"type": "done", "message": "All scenarios processed"}))],
                    name="Response",
                )
            else:
                # Process next scenario
                await updater.add_artifact(
                    parts=[Part(root=DataPart(data={"type": "ready", "message": "Ready for next scenario"}))],
                    name="Response",
                )

        else:
            await updater.add_artifact(
                parts=[Part(root=DataPart(data={"error": f"Unknown message type: {msg_type}"}))],
                name="Response",
            )

    async def _react_step(self, updater: TaskUpdater) -> dict:
        """Execute one step of the ReAct loop."""
        if not self._state:
            return {"error": "No active scenario state"}

        state = self._state
        step_num = len(state.steps) + 1

        if state.is_complete or step_num > state.max_steps:
            # Return final diagnosis
            if state.diagnosis:
                return state.diagnosis
            return {
                "entities": [],
                "propagations": [],
                "alerts_explained": []
            }

        logger.info(f"\n--- ReAct Step {step_num}/{state.max_steps} ---")

        # REASON: Decide what to do
        await updater.update_status(
            TaskState.working,
            new_agent_text_message(f"Step {step_num}: Reasoning...")
        )
        
        thought, action, action_input = await self._reason(state)
        logger.info(f"Thought: {thought[:100]}...")
        logger.info(f"Action: {action.value}")

        step = ReActStep(
            step_number=step_num,
            thought=thought,
            action=action,
            action_input=action_input
        )

        # ACT: Execute the action
        if action == ActionType.DIAGNOSE:
            await updater.update_status(
                TaskState.working,
                new_agent_text_message("Analyzing collected data...")
            )
            diagnosis = await self._diagnose(json.dumps(state.collected_data))
            state.diagnosis = diagnosis
            state.is_complete = True
            step.observation = Observation(action=action, success=True, data=diagnosis)
            state.steps.append(step)
            
            # Return the diagnosis
            return {
                "entities": diagnosis.get("entities", []),
                "propagations": diagnosis.get("propagations", []),
                "alerts_explained": diagnosis.get("alerts_explained", [])
            }

        elif action == ActionType.FINISH:
            state.is_complete = True
            step.observation = Observation(action=action, success=True, data={"status": "complete"})
            state.steps.append(step)
            
            if state.diagnosis:
                return state.diagnosis
            return {
                "entities": [],
                "propagations": [],
                "alerts_explained": []
            }

        elif action in ACTION_TO_DATA_TYPE:
            # Request data from evaluator
            data_type = ACTION_TO_DATA_TYPE[action]
            
            if data_type in state.collected_data:
                # Already have this data
                step.observation = Observation(
                    action=action,
                    success=True,
                    data=state.collected_data[data_type],
                    error="Data already collected"
                )
                state.steps.append(step)
                # Continue to next step
                return await self._react_step(updater)
            
            # Request data from evaluator
            state.steps.append(step)
            return {
                "type": "data_request",
                "data_type": data_type,
                "scenario": state.scenario
            }

        return {"error": f"Unknown action: {action.value}"}

    async def _reason(self, state: AgentState) -> tuple[str, ActionType, Optional[dict]]:
        """REASON phase: Use LLM to decide what action to take next."""
        collected_types = list(state.collected_data.keys())
        missing_types = [dt for dt in AVAILABLE_DATA_TYPES if dt not in collected_types]

        # Summarize previous steps
        step_summaries = []
        for step in state.steps:
            obs_summary = ""
            if step.observation:
                if step.observation.success:
                    if step.action in ACTION_TO_DATA_TYPE:
                        data = step.observation.data
                        if isinstance(data, dict):
                            obs_summary = f"Retrieved {len(data)} items"
                        elif isinstance(data, list):
                            obs_summary = f"Retrieved {len(data)} records"
                        else:
                            obs_summary = "Retrieved data successfully"
                    else:
                        obs_summary = "Action completed successfully"
                else:
                    obs_summary = f"Failed: {step.observation.error}"
            step_summaries.append(
                f"Step {step.step_number}: {step.action.value} | {obs_summary}"
            )

        # Create alert summary if we have alerts
        alert_summary = ""
        if "alerts" in state.collected_data:
            alerts = state.collected_data["alerts"]
            if isinstance(alerts, dict):
                all_alerts = []
                for v in alerts.values():
                    if isinstance(v, list):
                        all_alerts.extend(v)
                alerts = all_alerts
            
            if alerts:
                alert_names = [a.get("labels", {}).get("alertname", "unknown") for a in alerts[:20]]
                alert_summary = f"\nAlert summary ({len(alerts)} total): {', '.join(set(alert_names))}"

        # Load and format prompt
        prompt_template = load_prompt("reason")
        prompt = prompt_template.format(
            scenario=state.scenario,
            collected_types=collected_types if collected_types else "None yet",
            missing_types=missing_types,
            steps_taken=len(state.steps),
            max_steps=state.max_steps,
            alert_summary=alert_summary,
            step_summaries=chr(10).join(step_summaries) if step_summaries else "No previous steps"
        )

        try:
            logger.info(f"Sending reason request to LLM...")
            
            response = completion(
                base_url=self.base_url,
                api_key=self.api_key,
                model=f"{self.provider}/{self.model}" if self.provider else self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                timeout=60,
            )

            content = response.choices[0].message.content.strip()
            
            # Clean markdown fences
            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            result = json.loads(content)
            
            thought = result.get("thought", "No reasoning provided")
            action_str = result.get("action", "fetch_alerts")
            action_input = result.get("action_input", {})

            action_map = {
                "fetch_alerts": ActionType.FETCH_ALERTS,
                "fetch_metrics": ActionType.FETCH_METRICS,
                "fetch_k8s_events": ActionType.FETCH_K8S_EVENTS,
                "fetch_k8s_objects": ActionType.FETCH_K8S_OBJECTS,
                "fetch_otel_logs": ActionType.FETCH_OTEL_LOGS,
                "fetch_otel_traces": ActionType.FETCH_OTEL_TRACES,
                "diagnose": ActionType.DIAGNOSE,
                "finish": ActionType.FINISH,
            }

            action = action_map.get(action_str, ActionType.FETCH_ALERTS)
            return thought, action, action_input

        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing failed: {e}")
            # Fallback logic
            if not state.collected_data:
                return "Starting by fetching alerts (JSON parse failed)", ActionType.FETCH_ALERTS, {}
            elif "alerts" in state.collected_data and len(state.collected_data) < 3:
                return "Need more data (JSON parse failed)", ActionType.FETCH_K8S_EVENTS, {}
            else:
                return "Proceeding to diagnose (JSON parse failed)", ActionType.DIAGNOSE, {}
                
        except Exception as e:
            logger.error(f"Error in reasoning: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            # Fallback logic
            if not state.collected_data:
                return "Starting by fetching alerts", ActionType.FETCH_ALERTS, {}
            elif "alerts" in state.collected_data and len(state.collected_data) < 3:
                return "Need more data to diagnose", ActionType.FETCH_K8S_EVENTS, {}
            else:
                return "Have enough data, proceeding to diagnose", ActionType.DIAGNOSE, {}

    async def _diagnose(self, data_str: str) -> dict:
        """Analyze collected data and produce diagnosis."""
        MAX_DATA_CHARS = 100000
        if len(data_str) > MAX_DATA_CHARS:
            logger.warning(f"Data is {len(data_str)} chars, truncating to {MAX_DATA_CHARS}")
            data_str = data_str[:MAX_DATA_CHARS] + "\n\n...[DATA TRUNCATED]..."

        logger.info(f"Sending {len(data_str)} characters to LLM for diagnosis")

        prompt_template = load_prompt("diagnose")
        prompt = prompt_template.format(data_str=data_str)

        try:
            model_context_limit = 128000
            estimated_prompt_tokens = len(prompt) // 4
            available_tokens = model_context_limit - estimated_prompt_tokens - 1000
            max_tokens = min(4096, max(100, available_tokens))

            if available_tokens < 100:
                logger.error(f"Prompt too large: {estimated_prompt_tokens} tokens")
                return {
                    "entities": [],
                    "propagations": [],
                    "alerts_explained": [],
                    "metadata": {"error": "Prompt exceeds context window"}
                }

            content = ""
            for attempt in range(3):
                try:
                    logger.info(f"Diagnosis attempt {attempt+1}/3...")
                    
                    response = completion(
                        base_url=self.base_url,
                        api_key=self.api_key,
                        model=f"{self.provider}/{self.model}" if self.provider else self.model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        timeout=120,
                        num_retries=3,
                    )

                    content = response.choices[0].message.content
                    if content and content.strip():
                        break
                    logger.warning(f"Empty response (attempt {attempt+1}/3)")
                except Exception as e:
                    logger.error(f"LLM error (attempt {attempt+1}/3): {e}")
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2)

            if not content or not content.strip():
                raise ValueError("LLM returned empty response")

            # Clean response
            content = content.strip()
            
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip() if end != -1 else content[start:].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end].strip() if end != -1 else content[start:].strip()

            if not content.startswith("{") and not content.startswith("["):
                brace_idx = content.find("{")
                bracket_idx = content.find("[")
                if brace_idx != -1 and (bracket_idx == -1 or brace_idx < bracket_idx):
                    content = content[brace_idx:]
                elif bracket_idx != -1:
                    content = content[bracket_idx:]

            if content.startswith("{"):
                depth = 0
                for i, c in enumerate(content):
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            content = content[:i+1]
                            break

            result = json.loads(content)
            
            if "metadata" not in result:
                result["metadata"] = {}
            result["metadata"]["model"] = self.model
            result["metadata"]["timestamp"] = datetime.now(UTC).isoformat()

            return result

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failed: {e}")
            return {
                "entities": [],
                "propagations": [],
                "alerts_explained": [],
                "metadata": {"error": f"JSON parse failed: {e}"}
            }
        except Exception as e:
            logger.error(f"Diagnosis error: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            return {
                "entities": [],
                "propagations": [],
                "alerts_explained": [],
                "metadata": {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            }
