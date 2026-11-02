#!/usr/bin/env bash
# Downloads the public source file into data/. It isn't redistributed here.
set -euo pipefail
cd "$(dirname "$0")/../data"
curl -fsSL -o steam_200k.csv "https://media.githubusercontent.com/media/michalis0/Business-Intelligence-and-Analytics/1ddbf21338e0435e6e7fd840b95590a741fb9d4c/data/steam-200k.csv"
