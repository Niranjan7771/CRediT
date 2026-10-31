#!/usr/bin/env bash
set -e

# ==============================================================================
# CRediT Contributor Roles Parser Runner
# Usage: ./run.sh <input_folder> <output.csv>
# ==============================================================================

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <input_folder> <output.csv>" >&2
    exit 1
fi

INPUT_FOLDER="$1"
OUTPUT_CSV="$2"

if [ ! -d "$INPUT_FOLDER" ]; then
    echo "Error: Directory '$INPUT_FOLDER' does not exist." >&2
    exit 1
fi

# Ensure python3 is available
if ! command -v python3 &>/dev/null; then
    echo "python3 could not be found. Please install Python 3.12+." >&2
    exit 1
fi

# The parser uses only Python standard library (urllib, xml.etree, zipfile, etc.)
# No external dependencies required. If pip packages were needed, we would install them here.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "${SCRIPT_DIR}/credit_parser.py" "$INPUT_FOLDER" "$OUTPUT_CSV"
