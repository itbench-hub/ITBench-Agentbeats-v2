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
        # Track chunks for multi-part data responses
        self._chunk_buffer: dict[str, dict] = {}  # data_type -> {chunks: dict, total: int}

    async def run(self, message: Message, updater: TaskUpdater) -> None:
        """Process incoming message and return response."""
        input_text = get_message_text(message)
        print("\n" + "="*80)
        print("🤖 AGENT: Received message")
        print("="*80)
        logger.info(f"Agent received: {input_text[:200]}...")
        print(f"Message preview: {input_text[:150]}...")

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
        print(f"📨 Message type: {msg_type}")

        if msg_type == "scenario":
            # New scenario - initialize state and start ReAct loop
            scenario = msg.get("scenario", "unknown")
            available_types = msg.get("available_data_types", AVAILABLE_DATA_TYPES)
            
            print(f"\n🎯 NEW SCENARIO: {scenario}")
            print(f"Available data types: {available_types}")
            
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(f"Starting diagnosis for {scenario}...")
            )
            
            self._state = AgentState(scenario=scenario)
            print(f"✅ Initialized agent state for {scenario}")
            
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
            chunk_info = msg.get("chunk_info")
            
            print(f"\n📦 RECEIVED DATA: {data_type}")
            print(f"   Size: {len(str(content))} characters")
            
            # Handle chunked data
            if chunk_info and chunk_info.get("is_chunked"):
                chunk_num = chunk_info.get("chunk_number", 1)
                total_chunks = chunk_info.get("total_chunks", 1)
                
                print(f"   📦 Chunk {chunk_num}/{total_chunks}")
                
                # Initialize buffer for this data type if needed
                if data_type not in self._chunk_buffer:
                    self._chunk_buffer[data_type] = {"chunks": {}, "total": total_chunks}
                
                # Store this chunk
                self._chunk_buffer[data_type]["chunks"][chunk_num] = content
                
                # Check if we have all chunks
                if len(self._chunk_buffer[data_type]["chunks"]) == total_chunks:
                    print(f"   ✅ All {total_chunks} chunks received, merging...")
                    
                    # Merge all chunks in order
                    merged_content = {}
                    for i in range(1, total_chunks + 1):
                        chunk_data = self._chunk_buffer[data_type]["chunks"][i]
                        merged_content.update(chunk_data)
                    
                    # Clear buffer
                    del self._chunk_buffer[data_type]
                    
                    # Store merged data
                    if self._state:
                        self._state.collected_data[data_type] = merged_content
                        logger.info(f"Stored merged {data_type} data: {len(str(merged_content))} chars")
                        print(f"   ✅ Stored merged data. Total data types collected: {len(self._state.collected_data)}")
                        print(f"   Collected types: {list(self._state.collected_data.keys())}")
                    
                    # Continue ReAct loop
                    response = await self._react_step(updater)
                    
                    await updater.add_artifact(
                        parts=[Part(root=DataPart(data=response))],
                        name="Response",
                    )
                    return
                else:
                    # Waiting for more chunks - acknowledge receipt
                    print(f"   ⏳ Waiting for {total_chunks - len(self._chunk_buffer[data_type]['chunks'])} more chunk(s)...")
                    await updater.add_artifact(
                        parts=[Part(root=DataPart(data={
                            "type": "chunk_ack",
                            "data_type": data_type,
                            "chunk_number": chunk_num,
                            "total_chunks": total_chunks
                        }))],
                        name="Response",
                    )
                    return
            
            # Non-chunked data - process normally
            if self._state and data_type:
                self._state.collected_data[data_type] = content
                logger.info(f"Stored {data_type} data: {len(str(content))} chars")
                print(f"   ✅ Stored in state. Total data types collected: {len(self._state.collected_data)}")
                print(f"   Collected types: {list(self._state.collected_data.keys())}")
            
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
            print("❌ ERROR: No active scenario state")
            return {"error": "No active scenario state"}

        state = self._state
        step_num = len(state.steps) + 1

        # Check if we've already completed
        if state.is_complete:
            print(f"\n🏁 ReAct loop complete (is_complete={state.is_complete})")
            if state.diagnosis:
                print("   Returning diagnosis")
                return state.diagnosis
            print("   No diagnosis available, returning empty structure")
            return {
                "entities": [],
                "propagations": [],
                "alerts_explained": []
            }
        
        # If we've reached max steps, force a diagnosis with whatever data we have
        if step_num > state.max_steps:
            print(f"\n🏁 Maximum steps reached ({step_num}/{state.max_steps})")
            print(f"   🔍 Forcing diagnosis with collected data...")
            print(f"   Data types available: {list(state.collected_data.keys())}")
            
            if state.collected_data:
                await updater.update_status(
                    TaskState.working,
                    new_agent_text_message("Maximum steps reached. Analyzing collected data...")
                )
                diagnosis = await self._diagnose(json.dumps(state.collected_data))
                state.diagnosis = diagnosis
                state.is_complete = True
                
                print(f"   ✅ Forced diagnosis complete!")
                print(f"      Entities: {len(diagnosis.get('entities', []))}") 
                print(f"      Propagations: {len(diagnosis.get('propagations', []))}") 
                print(f"      Alerts explained: {len(diagnosis.get('alerts_explained', []))}")
                
                return {
                    "entities": diagnosis.get("entities", []),
                    "propagations": diagnosis.get("propagations", []),
                    "alerts_explained": diagnosis.get("alerts_explained", [])
                }
            else:
                print("   ⚠️  No data collected, returning empty structure")
                return {
                    "entities": [],
                    "propagations": [],
                    "alerts_explained": []
                }

        print(f"\n{'='*80}")
        print(f"🔄 REACT STEP {step_num}/{state.max_steps}")
        print(f"{'='*80}")
        logger.info(f"\n--- ReAct Step {step_num}/{state.max_steps} ---")

        # REASON: Decide what to do
        print(f"\n💭 REASONING PHASE")
        await updater.update_status(
            TaskState.working,
            new_agent_text_message(f"Step {step_num}: Reasoning...")
        )
        
        thought, action, action_input = await self._reason(state)
        print(f"   Thought: {thought[:150]}...")
        print(f"   🎬 Action decided: {action.value}")
        if action_input:
            print(f"   Action input: {action_input}")
        logger.info(f"Thought: {thought[:100]}...")
        logger.info(f"Action: {action.value}")

        step = ReActStep(
            step_number=step_num,
            thought=thought,
            action=action,
            action_input=action_input
        )

        # ACT: Execute the action
        print(f"\n⚡ ACTION PHASE")
        if action == ActionType.DIAGNOSE:
            print(f"   🔍 Starting diagnosis with collected data...")
            print(f"   Data types available: {list(state.collected_data.keys())}")
            await updater.update_status(
                TaskState.working,
                new_agent_text_message("Analyzing collected data...")
            )
            diagnosis = await self._diagnose(json.dumps(state.collected_data))
            state.diagnosis = diagnosis
            state.is_complete = True
            step.observation = Observation(action=action, success=True, data=diagnosis)
            state.steps.append(step)
            
            print(f"   ✅ Diagnosis complete!")
            print(f"      Entities: {len(diagnosis.get('entities', []))}")
            print(f"      Propagations: {len(diagnosis.get('propagations', []))}")
            print(f"      Alerts explained: {len(diagnosis.get('alerts_explained', []))}")
            
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
                print(f"   ⚠️  Data '{data_type}' already collected, skipping...")
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
            print(f"   📡 Requesting '{data_type}' from evaluator...")
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
            print(f"   🤖 Calling LLM for reasoning...")
            print(f"      Model: {self.provider}/{self.model}")
            print(f"      Prompt length: {len(prompt)} chars")
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
            print(f"   ✅ LLM response received ({len(content)} chars)")
            
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
            print(f"   ❌ JSON parsing failed: {e}")
            print(f"   Using fallback logic...")
            logger.error(f"JSON parsing failed: {e}")
            # Fallback logic
            if not state.collected_data:
                print(f"   → Fallback: Fetching alerts")
                return "Starting by fetching alerts (JSON parse failed)", ActionType.FETCH_ALERTS, {}
            elif "alerts" in state.collected_data and len(state.collected_data) < 3:
                print(f"   → Fallback: Fetching K8s events")
                return "Need more data (JSON parse failed)", ActionType.FETCH_K8S_EVENTS, {}
            else:
                print(f"   → Fallback: Proceeding to diagnose")
                return "Proceeding to diagnose (JSON parse failed)", ActionType.DIAGNOSE, {}
                
        except Exception as e:
            print(f"   ❌ Error in reasoning: {type(e).__name__}: {e}")
            print(f"   Using fallback logic...")
            logger.error(f"Error in reasoning: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            # Fallback logic
            if not state.collected_data:
                print(f"   → Fallback: Fetching alerts")
                return "Starting by fetching alerts", ActionType.FETCH_ALERTS, {}
            elif "alerts" in state.collected_data and len(state.collected_data) < 3:
                print(f"   → Fallback: Fetching more data")
                return "Need more data to diagnose", ActionType.FETCH_K8S_EVENTS, {}
            else:
                print(f"   → Fallback: Proceeding to diagnose")
                return "Have enough data, proceeding to diagnose", ActionType.DIAGNOSE, {}

    async def _diagnose(self, data_str: str) -> dict:
        """Analyze collected data and produce diagnosis."""
        print(f"\n{'='*80}")
        print(f"🔬 DIAGNOSIS PHASE")
        print(f"{'='*80}")
        
        MAX_DATA_CHARS = 100000
        if len(data_str) > MAX_DATA_CHARS:
            print(f"   ⚠️  Data is {len(data_str)} chars, truncating to {MAX_DATA_CHARS}")
            logger.warning(f"Data is {len(data_str)} chars, truncating to {MAX_DATA_CHARS}")
            data_str = data_str[:MAX_DATA_CHARS] + "\n\n...[DATA TRUNCATED]..."

        print(f"   📊 Data size: {len(data_str)} characters")
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
                    print(f"   🤖 Calling LLM for diagnosis (attempt {attempt+1}/3)...")
                    print(f"      Model: {self.provider}/{self.model}")
                    print(f"      Max tokens: {max_tokens}")
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
                        print(f"   ✅ LLM response received ({len(content)} chars)")
                        break
                    print(f"   ⚠️  Empty response (attempt {attempt+1}/3)")
                    logger.warning(f"Empty response (attempt {attempt+1}/3)")
                except Exception as e:
                    print(f"   ❌ LLM error (attempt {attempt+1}/3): {e}")
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
            
            print(f"   ✅ Successfully parsed diagnosis JSON")
            print(f"      Entities: {len(result.get('entities', []))}")
            print(f"      Propagations: {len(result.get('propagations', []))}")
            print(f"      Alerts explained: {len(result.get('alerts_explained', []))}")
            
            if "metadata" not in result:
                result["metadata"] = {}
            result["metadata"]["model"] = self.model
            result["metadata"]["timestamp"] = datetime.now(UTC).isoformat()

            return result

        except json.JSONDecodeError as e:
            print(f"   ❌ JSON parse failed: {e}")
            print(f"   Returning empty diagnosis")
            logger.error(f"JSON parse failed: {e}")
            return {
                "entities": [],
                "propagations": [],
                "alerts_explained": [],
                "metadata": {"error": f"JSON parse failed: {e}"}
            }
        except Exception as e:
            print(f"   ❌ Diagnosis error: {type(e).__name__}: {e}")
            print(f"   Returning empty diagnosis")
            logger.error(f"Diagnosis error: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            return {
                "entities": [],
                "propagations": [],
                "alerts_explained": [],
                "metadata": {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            }
