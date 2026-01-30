## Instructions
### Run with python
1. Create env.
```bash
uv sync
```

2. Download Scenarios.
```bash
uv run hf download \
    ibm-research/ITBench-Lite \
    --repo-type dataset \
    --include "snapshots/sre/v0.2-*" \
    --local-dir ./Scenarios
```

3. Move scenarios from Scenarios/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7 to Scenarios. The folder structure should be:  
Scenarios/  
└── Scenario_1/  
└── Scenario_2/  
└── Scenario_3/  
Once you have moved them delete .cache and snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7.

cp sample.env .env
```
This project uses litellm for model access, so you can use any model that is supported by litellm. You can find the litellm documentation for different providers here: https://docs.litellm.ai/docs/providers.

5. Run the evaluation.

# with logs
uv run agentbeats-run itbench/scenario.toml --show-logs
```

### Run containerized version
1. Zip up the Scenarios folder, make sure it is called Scenarios.zip.

2. Build the images.
```bash
docker build -f itbench/Dockerfile.evaluator --platform linux/amd64 -t ghcr.io/<your-id>/it-evaluator:v1.0 .
docker build -f itbench/Dockerfile.agent --platform linux/amd64 -t ghcr.io/<your-id>/it-agent:v1.0 .
```

3. Run the images.
```bash
docker run --network=host -p 9009:9009 --env-file .env <evaluator-image-id>
docker run --network=host -p 9019:9019 --env-file .env <agent-image-id>
```

4. Run the evaluation.
```bash
uv run python -m agentbeats.client_cli itbench/scenario.toml
```