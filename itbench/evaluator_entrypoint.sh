#!/bin/sh
set -e

if [ ! -d "Scenarios" ]; then
    if [ -f "Scenarios.zip" ]; then
        echo "Unzipping Scenarios.zip..."
        unzip -q Scenarios.zip
    else
        echo "Scenarios.zip not found, skipping unzip."
    fi
else
    echo "Scenarios directory already exists, skipping unzip."
fi

exec uv run itbench/evaluator/src/server.py "$@"
