## Quickstart
### Run with python
1. Create env.
```bash
uv synv
```

2. Download Scenarios.
```bash
uv run hf download \
    ibm-research/ITBench-Lite \
    --repo-type dataset \
    --include "snapshots/sre/v0.2-*" \
    --local-dir ./Scenarios
```

3. Move scenarios from Scenarios/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7 to Scenarios
The folder structure should be:
Scenarios/
└── Scenario_1/
└── Scenario_2/
└── Scenario_3/

4. Create a .env file with your model access credentials.
```bash
cp sample.env .env
```

5. Run the evaluation.
```bash
uv run agentbeats-run itbench/scenario.toml

# with logs
uv run agentbeats-run itbench/scenario.toml --show-logs
```

### Run dockerized version
1. Build the images.
```bash
docker build -f itbench/Dockerfile.evaluator --platform linux/amd64 -t ghcr.io/<your-id>/it-evaluator:v1.0 .
docker build -f itbench/Dockerfile.agent --platform linux/amd64 -t ghcr.io/<your-id>/it-agent:v1.0 .
```

2. Run the images.
```bash
docker run --network=host -p 9009:9009 --env-file .env <evaluator-image-id>
docker run --network=host -p 9019:9019 --env-file .env <agent-image-id>
```

3. Run the evaluation.
```bash
uv run python -m agentbeats.client_cli itbench/scenario.toml
```