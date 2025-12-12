#!/bin/bash
# Cron wrapper for YouTube transcript pipeline
# This script activates the virtual environment and runs the pipeline

set -e

# Change to project directory
cd /home/pbrown/AInewsletter

# Activate virtual environment
source .venv/bin/activate

# Load environment variables
set -a
source .env
set +a

# Run the pipeline
python scripts/run_youtube_transcripts.py 2>&1

# Deactivate virtual environment
deactivate
