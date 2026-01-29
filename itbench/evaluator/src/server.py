import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)


def _default_itbench_data_dir() -> Path:
    """Find the ITBench scenarios data directory."""
    tau2_dir = Path(__file__).resolve().parents[2]
    possible_dirs = [
        tau2_dir.parent / "ITBench-Lite" / "snapshots" / "sre" / "v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7",
        tau2_dir.parent / "Scenarios",
        Path("ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"),
        Path("Scenarios"),
    ]
    for d in possible_dirs:
        if d.exists():
            return d
    return possible_dirs[0]  # Return first option even if not found


def _ensure_itbench_data_dir() -> None:
    if os.environ.get("ITBENCH_DATA_DIR"):
        return

    default_dir = _default_itbench_data_dir()
    if default_dir.exists():
        os.environ["ITBENCH_DATA_DIR"] = str(default_dir)
        print(f"ITBENCH_DATA_DIR not set; defaulting to {default_dir}")


def main():
    load_dotenv()
    _ensure_itbench_data_dir()

    from executor import Executor

    parser = argparse.ArgumentParser(description="Run the ITBench Scenario Evaluator.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9009, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, help="URL to advertise in the agent card")
    args = parser.parse_args()

    skill = AgentSkill(
        id="itbench_evaluation",
        name="ITBench Scenario Evaluation",
        description="Evaluates SRE agents on Kubernetes incident diagnosis scenarios.",
        tags=["benchmark", "evaluation", "sre", "kubernetes"],
        examples=[
            '{"participants": {"agent": "http://localhost:9019"}, "config": {"trials": 1}}',
            'START',
            'GET alerts',
        ],
    )

    agent_card = AgentCard(
        name="ITBench Evaluator",
        description="ITBench evaluator - tests SRE agents on Kubernetes incident diagnosis scenarios.",
        url=args.card_url or f"http://{args.host}:{args.port}/",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=Executor(),
        task_store=InMemoryTaskStore(),
    )
    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    uvicorn.run(server.build(), host=args.host, port=args.port, timeout_keep_alive=300)


if __name__ == "__main__":
    main()
