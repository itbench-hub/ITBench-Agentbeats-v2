"""
ITBench Scenario Evaluator (Green Agent).

This evaluator manages Kubernetes incident scenarios, provides telemetry data
on request, and runs batch evaluation of agent diagnoses.
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, HttpUrl, ValidationError

from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, Message, Part, TaskState, TextPart
from a2a.utils import get_message_text, new_agent_text_message

from messenger import Messenger


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("evaluator")


class EvalRequest(BaseModel):
    """Request to start an evaluation session."""
    participants: dict[str, HttpUrl]
    config: dict[str, Any]


class Agent:
    """ITBench Scenario Evaluator that manages scenarios and data provisioning."""
    
    # Supported data types that can be requested
    DATA_TYPES = {
        "alerts": "alerts",
        "metrics": "metrics", 
        "k8s_events": "k8s_events_raw.tsv",
        "k8s_objects": "k8s_objects_raw.tsv",
        "otel_logs": "otel_logs_raw.tsv",
        "otel_traces": "otel_traces_raw.tsv",
    }
    
    required_roles: list[str] = ["agent"]
    required_config_keys: list[str] = []

    def __init__(self):
        self.messenger = Messenger()
        self.scenarios: list[str] = []
        self.current_scenario_idx = 0
        self.current_scenario_name: str | None = None
        self.data_dir: Path | None = None
        self.outputs_dir = Path("outputs")
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self._load_scenarios()

    def _load_scenarios(self):
        """Load available scenarios from the data directory."""
        data_dir = Path(__file__).resolve().parents[3] / "Scenarios"
        
        if data_dir.exists() and data_dir.is_dir():
            self.data_dir = data_dir
            self.scenarios = sorted([
                d.name for d in data_dir.iterdir() 
                if d.is_dir() and d.name.startswith("Scenario")
            ])
            if self.scenarios:
                logger.info(f"Loaded {len(self.scenarios)} scenarios from {data_dir}")
                return
        
        logger.warning(f"Scenario data directory not found at {data_dir}")
        self.scenarios = []

    def _load_specific_data(self, scenario_name: str, data_type: str) -> dict:
        """Load a specific type of data for a scenario."""
        if not self.data_dir:
            return {"error": "No data directory configured"}
            
        scenario_path = self.data_dir / scenario_name
        if not scenario_path.exists():
            return {"error": f"Scenario {scenario_name} not found"}
        
        if data_type not in self.DATA_TYPES:
            return {"error": f"Unknown data type: {data_type}. Available: {list(self.DATA_TYPES.keys())}"}
        
        target = self.DATA_TYPES[data_type]
        target_path = scenario_path / target
        
        if not target_path.exists():
            return {"error": f"Data type {data_type} not available for {scenario_name}"}
        
        data = {}
        if target_path.is_dir():
            # Load all files from directory (alerts, metrics)
            for file_path in sorted(target_path.glob("*")):
                if file_path.is_file() and not file_path.name.startswith("."):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read(100000)
                            if file_path.suffix == ".json":
                                try:
                                    data[file_path.name] = json.loads(content)
                                except json.JSONDecodeError:
                                    data[file_path.name] = content
                            else:
                                data[file_path.name] = content
                    except Exception as e:
                        logger.warning(f"Could not read {file_path}: {e}")
        else:
            # Load single file
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data[target_path.name] = f.read(100000)
            except Exception as e:
                return {"error": f"Could not read {data_type}: {e}"}
        
        return data

    def validate_request(self, request: EvalRequest) -> tuple[bool, str]:
        """Validate an evaluation request."""
        missing_roles = set(self.required_roles) - set(request.participants.keys())
        if missing_roles:
            return False, f"Missing roles: {missing_roles}"
        return True, "ok"

    async def run(self, message: Message, updater: TaskUpdater) -> None:
        """Process incoming message and manage evaluation flow."""
        input_text = get_message_text(message)
        logger.info(f"Evaluator received: {input_text[:200]}...")
        
        msg_upper = input_text.strip().upper()

        # Handle START command
        if "START" in msg_upper:
            self.current_scenario_idx = 0
            if not self.scenarios:
                await updater.add_artifact(
                    parts=[Part(root=DataPart(data={"type": "done", "reason": "No scenarios found"}))],
                    name="Response",
                )
                return
            
            response = self._prepare_next_scenario()
            await updater.add_artifact(
                parts=[Part(root=DataPart(data=response))],
                name="Response",
            )
            return

        # Handle DONE command - run batch evaluation
        if "DONE" in msg_upper:
            logger.info("Received DONE. Running batch evaluation...")
            await updater.update_status(
                TaskState.working,
                new_agent_text_message("Running batch evaluation...")
            )
            result = await self._run_batch_evaluation()
            await updater.add_artifact(
                parts=[Part(root=DataPart(data=result))],
                name="Result",
            )
            return

        # Try to parse as JSON
        try:
            msg = json.loads(input_text)
        except json.JSONDecodeError:
            # Handle GET <data_type> command
            if msg_upper.startswith("GET "):
                data_type = input_text.strip()[4:].strip().lower()
                if not self.current_scenario_name:
                    await updater.add_artifact(
                        parts=[Part(root=DataPart(data={"type": "error", "message": "No active scenario"}))],
                        name="Response",
                    )
                    return
                
                data = self._load_specific_data(self.current_scenario_name, data_type)
                if "error" in data:
                    await updater.add_artifact(
                        parts=[Part(root=DataPart(data={"type": "error", "message": data["error"]}))],
                        name="Response",
                    )
                else:
                    await updater.add_artifact(
                        parts=[Part(root=DataPart(data={
                            "type": "data_response",
                            "scenario": self.current_scenario_name,
                            "data_type": data_type,
                            "content": data
                        }))],
                        name="Response",
                    )
                return
            
            await updater.add_artifact(
                parts=[Part(root=DataPart(data={"type": "error", "message": "Invalid command"}))],
                name="Response",
            )
            return

        msg_type = msg.get("type")

        # Handle data request from agent
        if msg_type == "data_request":
            data_type = msg.get("data_type")
            scenario = msg.get("scenario", self.current_scenario_name)
            
            if not scenario:
                await updater.add_artifact(
                    parts=[Part(root=DataPart(data={"type": "error", "message": "No active scenario"}))],
                    name="Response",
                )
                return
            
            data = self._load_specific_data(scenario, data_type)
            if "error" in data:
                await updater.add_artifact(
                    parts=[Part(root=DataPart(data={"type": "error", "message": data["error"]}))],
                    name="Response",
                )
            else:
                await updater.add_artifact(
                    parts=[Part(root=DataPart(data={
                        "type": "data_response",
                        "scenario": scenario,
                        "data_type": data_type,
                        "content": data
                    }))],
                    name="Response",
                )
            return

        # Handle diagnosis submission (JSON with entities, propagations, alerts_explained)
        if "entities" in msg or "propagations" in msg or "alerts_explained" in msg:
            logger.info(f"Received diagnosis for {self.current_scenario_name}")
            
            completed_scenario = self.current_scenario_name
            self.current_scenario_idx += 1
            next_payload = self._prepare_next_scenario()
            
            await updater.add_artifact(
                parts=[Part(root=DataPart(data={
                    "type": "acknowledged",
                    "scenario": completed_scenario,
                    "message": f"Diagnosis received for {completed_scenario}",
                    "next_action": next_payload
                }))],
                name="Response",
            )
            return

        # Handle EvalRequest (full evaluation session)
        try:
            request = EvalRequest.model_validate(msg)
            ok, validation_msg = self.validate_request(request)
            if not ok:
                await updater.reject(new_agent_text_message(validation_msg))
                return
            
            await self._run_full_evaluation(request, updater)
            
        except ValidationError:
            await updater.add_artifact(
                parts=[Part(root=DataPart(data={"type": "error", "message": "Unknown message format"}))],
                name="Response",
            )

    def _prepare_next_scenario(self) -> dict:
        """Prepare the next scenario payload."""
        if self.current_scenario_idx >= len(self.scenarios):
            return {"type": "done", "message": "All scenarios complete"}
        
        self.current_scenario_name = self.scenarios[self.current_scenario_idx]
        
        return {
            "type": "scenario",
            "scenario": self.current_scenario_name,
            "available_data_types": list(self.DATA_TYPES.keys())
        }

    def _save_agent_output(self, scenario: str, output: dict, trial: int = 1) -> None:
        """Save agent output to the outputs directory for batch evaluation.
        
        Structure: outputs/<scenario_id>/<trial>/outputs/agent_output.json
        """
        # Canonicalize scenario name to get ID (e.g. "Scenario-1" -> "1")
        import re
        match = re.search(r'(\d+)', scenario)
        scenario_id = match.group(1) if match else scenario
        
        # Create the output path
        output_path = self.outputs_dir / scenario_id / str(trial) / "outputs"
        output_path.mkdir(parents=True, exist_ok=True)
        
        output_file = output_path / "agent_output.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)
        
        logger.info(f"Saved agent output to: {output_file}")

    async def _run_batch_evaluation(self) -> dict:
        """Run batch evaluation using itbench_evaluations CLI."""
        try:
            if not self.data_dir:
                return {"type": "error", "message": "No data directory configured"}
            
            cmd = [
                "uv", "run", "python", "-m", "itbench_evaluations",
                "--ground-truth", str(self.data_dir),
                "--outputs", str(self.outputs_dir),
                "--eval-criteria",
                "ROOT_CAUSE_ENTITY",
                "ROOT_CAUSE_REASONING", 
                "PROPAGATION_CHAIN",
                "FAULT_LOCALIZATION",
                "ROOT_CAUSE_REASONING_PARTIAL",
                "ROOT_CAUSE_PROXIMITY",
                "ROOT_CAUSE_PROXIMITY_FP",
                "--result-file", "evaluation_results.json",
            ]
            
            logger.info(f"Running evaluation: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Evaluation failed: {result.stderr}")
                return {
                    "type": "error",
                    "message": f"Evaluation failed: {result.stderr}",
                }
            
            logger.info(result.stdout)
            
            return {
                "type": "evaluation_complete",
                "message": "Evaluation complete",
                "report_file": "evaluation_results.json",
            }
            
        except Exception as e:
            logger.error(f"Batch evaluation failed: {e}", exc_info=True)
            return {
                "type": "error",
                "message": f"Evaluation failed: {str(e)}",
            }

    async def _run_full_evaluation(self, request: EvalRequest, updater: TaskUpdater) -> None:
        """Run a full evaluation session with an agent."""
        import time
        
        agent_url = str(request.participants["agent"])
        trials = int(request.config.get("trials", 1))
        scenarios_to_run = self.scenarios
        
        logger.info(f"Running {len(scenarios_to_run)} scenarios with {trials} trial(s) each")
        
        await updater.update_status(
            TaskState.working,
            new_agent_text_message(f"Starting evaluation of {len(scenarios_to_run)} scenarios")
        )
        
        start_time = time.time()
        results: dict[str, Any] = {"scenarios": {}}
        
        try:
            for scenario in scenarios_to_run:
                self.current_scenario_name = scenario
                
                await updater.update_status(
                    TaskState.working,
                    new_agent_text_message(f"Running scenario {scenario}...")
                )
                
                # Send scenario to agent
                scenario_msg = {
                    "type": "scenario",
                    "scenario": scenario,
                    "available_data_types": list(self.DATA_TYPES.keys())
                }
                
                try:
                    response = await self.messenger.talk_to_agent(
                        message=json.dumps(scenario_msg),
                        url=agent_url,
                        new_conversation=True,
                    )
                    
                    # Handle data requests from agent
                    max_exchanges = 20
                    for _ in range(max_exchanges):
                        try:
                            resp_json = json.loads(response)
                        except json.JSONDecodeError:
                            break
                        
                        if resp_json.get("type") == "data_request":
                            data_type = resp_json.get("data_type")
                            data = self._load_specific_data(scenario, data_type)
                            
                            data_response = {
                                "type": "data_response",
                                "scenario": scenario,
                                "data_type": data_type,
                                "content": data if "error" not in data else {}
                            }
                            
                            response = await self.messenger.talk_to_agent(
                                message=json.dumps(data_response),
                                url=agent_url,
                            )
                        elif "entities" in resp_json or "propagations" in resp_json:
                            # Got diagnosis - save it for batch evaluation
                            results["scenarios"][scenario] = resp_json
                            self._save_agent_output(scenario, resp_json, trial=1)
                            break
                        else:
                            break
                    
                except Exception as e:
                    logger.error(f"Error with scenario {scenario}: {e}")
                    results["scenarios"][scenario] = {"error": str(e)}
            
            # Run batch evaluation
            eval_result = await self._run_batch_evaluation()
            
            time_used = time.time() - start_time
            
            # Load actual evaluation results from file if available
            eval_data = {}
            report_file = eval_result.get('report_file')
            if report_file and Path(report_file).exists():
                try:
                    with open(report_file, 'r') as f:
                        eval_data = json.load(f)
                except Exception as e:
                    logger.warning(f"Could not load evaluation results: {e}")
            
            summary = f"""ITBench Evaluation Results
Scenarios: {len(scenarios_to_run)}
Time: {time_used:.1f}s
Report: {report_file or 'N/A'}"""

            await updater.add_artifact(
                parts=[
                    Part(root=TextPart(text=summary)),
                    Part(root=DataPart(data={
                        "scenarios_evaluated": len(scenarios_to_run),
                        "time_used": time_used,
                        "agent_outputs": results,
                        "evaluation_results": eval_data,
                    })),
                ],
                name="Result",
            )
            
        finally:
            self.messenger.reset()
