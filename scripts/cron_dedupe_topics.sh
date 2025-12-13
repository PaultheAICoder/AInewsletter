#!/bin/bash
# Cron wrapper for topic deduplication
# Runs daily after the YouTube transcript pipeline to consolidate similar topics

set -e

# Change to project directory
cd /home/pbrown/AInewsletter

# Activate virtual environment
source .venv/bin/activate

# Load environment variables
set -a
source .env
set +a

# Run the deduplication script
python scripts/dedupe_topics.py 2>&1

# Deactivate virtual environment
deactivate
