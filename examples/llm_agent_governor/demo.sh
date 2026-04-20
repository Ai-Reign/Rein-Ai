#!/usr/bin/env bash
# 60-second demo script — paced for screen recording.
# Usage: bash demo.sh
#
# QuickTime: File → New Screen Recording → record this terminal window
# (or iTerm: Cmd+Shift+5 → Record)

set -e
cd "$(dirname "$0")"

type() {
    # Simulates typing for dramatic effect
    local s="$1"
    for ((i=0; i<${#s}; i++)); do
        printf "%s" "${s:$i:1}"
        sleep 0.015
    done
    echo
}

clear
echo ""
sleep 0.4
type "# Meta Brain — governing an autonomous LLM agent"
sleep 0.6
type "# Problem: broken tool+task combos silently burn tokens."
sleep 0.6
type "# Demo: agent spams 4 tool/task pairs; one is broken."
sleep 0.8
echo ""
type "\$ python3 governor.py"
sleep 0.6
echo ""

# Strip out slow-burn rows; keep the dramatic ones
python3 governor.py 2>&1 | awk '
    /^\[0[0-9]\]/    { print; next }      # first 10 events (learning)
    /BLOCKED/        { print; next }      # the kills — headline
    /^Summary/       { print "---"; print; next }
    /^Strategy/      { print; next }
    /^ {2}[a-z_]+\|/ { print; next }
' | while IFS= read -r line; do
    echo "$line"
    case "$line" in
        *BLOCKED*)    sleep 0.04 ;;
        *Summary*|*"---"*)  sleep 0.9 ;;
        *status=red*)  sleep 0.5 ;;
        *status=green*) sleep 0.3 ;;
        *) sleep 0.12 ;;
    esac
done

sleep 1.2
echo ""
type "# Meta Brain killed the broken tool, kept the healthy ones working."
sleep 0.8
type "# Zero config changes to the agent. Drop-in governance."
sleep 1.0
echo ""
type "# Same code is in production guarding a live Kalshi trading bot."
sleep 1.2
echo ""
