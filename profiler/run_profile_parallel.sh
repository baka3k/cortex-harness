#!/bin/bash
# Profile cplus with ProcessPool parallelization test
# Usage: ./run_profile_parallel.sh [workers] [target]

WORKERS="${1:-8}"
TARGET="${2:-/Users/hieplq1.rpm/JavaMigration/MigrateCplus/20251008_Sourcecode_utf8}"

cd /Users/hieplq1.rpm/AI/cortex-harness

python3 profile_analyzer.py --target "$TARGET" --language cplus --parallel "$WORKERS" --pool-type process --no-cache-write
