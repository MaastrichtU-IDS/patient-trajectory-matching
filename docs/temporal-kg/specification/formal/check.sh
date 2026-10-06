#!/bin/sh
set -eu
cd "$(dirname "$0")"
build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT HUP INT TERM
export LEAN_PATH="$build_dir${LEAN_PATH:+:$LEAN_PATH}"
lean -o "$build_dir/IntegratedSemantics.olean" IntegratedSemantics.lean
lean -o "$build_dir/OWLDirectSemantics.olean" OWLDirectSemantics.lean
lean ProposalSemantics.lean
