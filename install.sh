#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_DIR="$HOME/.hermes/scripts"
CONFIG_DIR="$HOME/.config/gmgn-bsc-base-radar"
FILTER_DIR="$HOME/gmgn-bsc-base-radar"

mkdir -p "$SCRIPT_DIR" "$CONFIG_DIR" "$FILTER_DIR"
install -m 0755 "$ROOT/src/gmgn-dlmm-radar.py" "$SCRIPT_DIR/gmgn-dlmm-radar.py"
install -m 0644 "$ROOT/config/filter-query.json" "$FILTER_DIR/filter_query.json"
install -m 0644 "$ROOT/config/arc-filter-query.json" "$FILTER_DIR/arc_filter_query.json"
install -m 0644 "$ROOT/config/bsc-filter-query.json" "$FILTER_DIR/bsc_filter_query.json"
install -m 0644 "$ROOT/config/base-filter-query.json" "$FILTER_DIR/base_filter_query.json"
install -m 0644 "$ROOT/config/sol-filter-query.json" "$FILTER_DIR/sol_filter_query.json"
install -m 0644 "$ROOT/config/eth-filter-query.json" "$FILTER_DIR/eth_filter_query.json"
install -m 0644 "$ROOT/config/arbitrum-filter-query.json" "$FILTER_DIR/arbitrum_filter_query.json"
install -m 0644 "$ROOT/config/hyperevm-filter-query.json" "$FILTER_DIR/hyperevm_filter_query.json"
install -m 0644 "$ROOT/config/robinhood-filter-query.json" "$FILTER_DIR/robinhood_filter_query.json"
install -m 0644 "$ROOT/config/stable-filter-query.json" "$FILTER_DIR/stable_filter_query.json"

if [[ ! -f "$CONFIG_DIR/telegram.env" ]]; then
  install -m 0600 "$ROOT/telegram.env.example" "$CONFIG_DIR/telegram.env"
  printf '%s\n' "Created $CONFIG_DIR/telegram.env"
fi

printf '%s\n' "Installed $SCRIPT_DIR/gmgn-dlmm-radar.py"
printf '%s\n' "Edit $CONFIG_DIR/telegram.env, configure GMGN CLI, then add the job from config/cron.json."
