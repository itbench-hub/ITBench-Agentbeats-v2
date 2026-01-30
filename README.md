# Instructions
## Run with python
1. Create env.
```bash
uv sync
```

2. Download and prepare scenarios.
```bash
./itbench/setup.sh
```
This downloads the complete ITBench-Lite dataset to `./Scenarios/`. The structure will include all snapshots (SRE, etc.) with their scenarios.

4. Create a .env file with your model access credentials. The evaluator model must be set to Gemini 3 Pro Preview to keep evaluations fair.
```bash
cp .env.tmpl .env
```
This project uses litellm to allow models from many different providers to be used. Documentation for providers can be found here: https://docs.litellm.ai/docs/providers.

5. Run the evaluation.
```bash
# Run all scenarios
uv run agentbeats-run itbench/scenario.toml

# Run only SRE scenarios
uv run agentbeats-run itbench/scenario-sre.toml

# Run only FinOps scenarios
uv run agentbeats-run itbench/scenario-finops.toml

# With logs
uv run agentbeats-run itbench/scenario.toml --show-logs
```

**Domain Filtering**: You can filter scenarios by domain in the configuration file:
- `domains = ["all"]` - Run all available scenarios (default)
- `domains = ["sre"]` - Run only SRE scenarios
- `domains = ["finops"]` - Run only FinOps scenarios
- `domains = ["sre", "finops"]` - Run specific domains

## Run containerized version
1. Prepare the Scenarios folder using `./itbench/setup.sh` (see step 2 above).

2. Build the images.
```bash
# Build for local use
make build

# Or build for specific architecture
make build PLATFORMS=linux/amd64

# Or build for remote registry
make build IMG_REGISTRY=ghcr.io IMG_NAMESPACE=<your-id> VERSION=v1.0
```

3. Run the containers (works with both Docker and Podman).

**Using Makefile (recommended):**
```bash
# Run both containers
make run-all

# Or run individually
make run-evaluator
make run-agent

# View logs
make logs-evaluator
make logs-agent

# Stop containers
make stop-all
```

**Manual commands:**
```bash
# Run evaluator with Scenarios mounted (read-only)
docker run -d --name itbench-evaluator \
  -p 9009:9009 \
  --env-file .env \
  -v $(pwd)/Scenarios:/home/agentbeats/itbench_eval/Scenarios:ro \
  localhost/itbench/evaluator:latest

# Run agent
docker run -d --name itbench-agent \
  -p 9019:9019 \
  --env-file .env \
  localhost/itbench/agent:latest
```

**Note:**
- Replace `docker` with `podman` if using Podman

4. Run the evaluation.
```bash
# Run all scenarios
uv run python -m agentbeats.client_cli itbench/scenario.toml

# Run only SRE scenarios
uv run python -m agentbeats.client_cli itbench/scenario-sre.toml

# Run only FinOps scenarios
uv run python -m agentbeats.client_cli itbench/scenario-finops.toml
```
