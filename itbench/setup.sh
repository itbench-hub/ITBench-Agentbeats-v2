#!/bin/bash
set -e

echo "Downloading ITBench-Lite dataset..."
uv run python -c "
from huggingface_hub import snapshot_download
import sys
print('Fetching complete dataset from HuggingFace...', file=sys.stderr)
snapshot_download(
    repo_id='ibm-research/ITBench-Lite',
    repo_type='dataset',
    local_dir='./Scenarios'
)
print('Download complete!', file=sys.stderr)
"

echo "✓ Setup complete! Dataset downloaded to ./Scenarios"
