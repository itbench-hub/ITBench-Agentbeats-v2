# ITBench SRE Diagnosis Scenario

This scenario evaluates SRE agents on Kubernetes incident diagnosis using the ITBench benchmark. The agent analyzes telemetry data (alerts, logs, metrics, events) to identify root causes and fault propagation chains.

## Setup

1. **Install dependencies**:

   ```bash
   uv sync
   ```

2. **Set up your LLM API key** in `.env`:

   ```
   PROVIDER=""
   MODEL=""
   API_KEY=""
   URL=""
   ```

3. **Ensure scenario data exists** in one of:
   - `ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7/`
   - `Scenarios/`
   - Or set `ITBENCH_DATA_DIR` environment variable

## Running the Benchmark

Start both services:

```bash
# Terminal 1: Start the evaluator (green agent)
cd tau2/evaluator/src && python server.py

# Terminal 2: Start the diagnosis agent (purple agent)
cd tau2/agent/src && python server.py
```

Or use agentbeats:

```bash
uv run agentbeats-run tau2/scenario.toml
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PROVIDER` | LLM provider (openai, anthropic, etc.) | openai |
| `MODEL` | Model name | gpt-4.1 |
| `API_KEY` | API key for LLM | - |
| `URL` | Custom API endpoint | - |
| `ITBENCH_DATA_DIR` | Path to scenario data | Auto-detected |
| `TAU2_AGENT_LLM` | Model for agent (litellm format) | openai/gpt-4.1 |

### scenario.toml

```toml
[config]
trials = 1              # trials per scenario
```

## Architecture

- **evaluator/src/** (Green Agent): Manages ITBench scenarios, provides telemetry data on request, and runs batch evaluation
- **agent/src/** (Purple Agent): SRE diagnosis agent using ReAct (Reason, Act, Observe) framework to intelligently collect data and diagnose incidents

## Protocol

The agent and evaluator communicate via JSON messages:

1. Evaluator sends: `{"type": "scenario", "scenario": "Scenario-1", "available_data_types": ["alerts", "metrics", ...]}`
2. Agent requests data: `{"type": "data_request", "data_type": "alerts", "scenario": "Scenario-1"}`
3. Evaluator responds: `{"type": "data_response", "data_type": "alerts", "content": {...}}`
4. Agent submits diagnosis: `{"entities": [...], "propagations": [...], "alerts_explained": [...]}`
5. Evaluator acknowledges and provides next scenario

## Data Types

Available telemetry data for each scenario:
- `alerts` - Prometheus/Alertmanager alerts (JSON)
- `metrics` - Prometheus metrics
- `k8s_events` - Kubernetes events (TSV)
- `k8s_objects` - Kubernetes object states (TSV)
- `otel_logs` - OpenTelemetry logs (TSV)
- `otel_traces` - Distributed traces (TSV)

## Diagnosis Output Format

```json
{
  "entities": [
    {
      "name": "namespace/Kind/name",
      "contributing_factor": true,
      "reasoning": "Explanation",
      "evidence": "Supporting evidence"
    }
  ],
  "propagations": [
    {
      "source": "namespace/Kind/name",
      "target": "namespace/Kind/name",
      "condition": "What caused propagation",
      "effect": "Observed effect"
    }
  ],
  "alerts_explained": [
    {
      "alert": "AlertName",
      "explanation": "Why this alert fired",
      "explained": true
    }
  ]
}
