#!/bin/bash
# Ensures the assess-rfe plugin is available locally.
# Safe to run multiple times — clones on first run, pulls updates after.
#
# Usage: bootstrap-assess-rfe.sh [--type rfe|initiative]
#
# The caller declares which pipeline's assets it needs. Validating only the
# RFE rubric lets a checkout that lacks the initiative rubric or the
# initiative-scorer agent exit 0, after which the ASSESS phase can never
# complete and wait-for-wave spins on exit 3 with nothing to diagnose.

PIPELINE_TYPE="rfe"
while [ $# -gt 0 ]; do
  case "$1" in
    --type)
      PIPELINE_TYPE="$2"
      shift 2
      ;;
    --type=*)
      PIPELINE_TYPE="${1#--type=}"
      shift
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      echo "Usage: bootstrap-assess-rfe.sh [--type rfe|initiative]" >&2
      exit 2
      ;;
  esac
done

# The registered type names come from the registry (scripts/type_registry.py list,
# one name per line): an unknown --type fails with the registered list (design §5
# rung 1) and a type added under types/ is accepted without editing this script.
# Validation stays ahead of the RFE_SKIP_BOOTSTRAP short-circuit — a mistyped
# pipeline must fail even in an offline run. This script IS the dependency
# bootstrap, so it must not need a bootstrapped Python itself: when the registry
# cannot be read (no PyYAML yet, no python3 on PATH) the names fall back to the
# shipped root, <scripts>/../types/<name>/type.yaml — the set `list` prints when
# RFE_CREATOR_EXTRA_TYPES is unset (a drop-in root needs the registry) — and the
# registry's own stderr is not shown, so a valid --type behaves exactly as before
# the registry existed. Only when that root holds no descriptor either is the
# failure fatal (exit 2).
SCRIPT_DIR="$(dirname "$0")"
TYPES_ROOT="$SCRIPT_DIR/../types"

registered_types_from_root() {
  # rfe first, then the rest in glob order — the order `list` prints (names()).
  # Directories starting with "_" (types/_schema) are never types.
  local descriptor name rest="" have_rfe=0
  for descriptor in "$TYPES_ROOT"/*/type.yaml; do
    [ -f "$descriptor" ] || continue
    name="$(basename "$(dirname "$descriptor")")"
    case "$name" in _*) continue ;; esac
    if [ "$name" = "rfe" ]; then
      have_rfe=1
    else
      rest+="$name"$'\n'
    fi
  done
  if [ "$have_rfe" -eq 1 ]; then
    echo "rfe"
  fi
  printf '%s' "$rest"
}

REGISTERED="$(python3 "$SCRIPT_DIR/type_registry.py" list 2>/dev/null)" || REGISTERED=""
if [ -z "$REGISTERED" ]; then
  REGISTERED="$(registered_types_from_root)"
  if [ -z "$REGISTERED" ]; then
    echo "ERROR: could not read the type registry (python3 $SCRIPT_DIR/type_registry.py list failed and $TYPES_ROOT holds no <name>/type.yaml)" >&2
    exit 2
  fi
fi
KNOWN=0
REGISTERED_LIST=""
while IFS= read -r registered_type; do
  [ -n "$registered_type" ] || continue
  if [ "$registered_type" = "$PIPELINE_TYPE" ]; then
    KNOWN=1
  fi
  REGISTERED_LIST="${REGISTERED_LIST:+$REGISTERED_LIST, }$registered_type"
done <<EOF
$REGISTERED
EOF
if [ "$KNOWN" -ne 1 ]; then
  echo "ERROR: unknown --type '$PIPELINE_TYPE' (registered types: $REGISTERED_LIST)" >&2
  exit 2
fi

if [ -n "${RFE_SKIP_BOOTSTRAP:-}" ]; then
  echo "RFE_SKIP_BOOTSTRAP set - skipping dependency bootstrapping step"
  exit 0
fi

CONTEXT_DIR=".context/assess-rfe"
ASSESS_REPO="${ASSESS_RFE_REPO:-https://github.com/opendatahub-io/assess-rfe}"
# Scripts now live under each skill dir (assess-rfe moved them out of the repo
# root in opendatahub-io/assess-rfe#5 "move-scripts-to-skill-dirs").
RUBRIC_FILE="$CONTEXT_DIR/skills/assess-rfe/scripts/agent_prompt.md"
# Mirrors PIPELINE_TYPES["initiative"] rubric_path/scorer_type in
# scripts/pipeline_state.py; tests/test_bootstrap_assess.py pins them together.
INITIATIVE_RUBRIC="$CONTEXT_DIR/skills/assess-initiative/scripts/agent_prompt.md"
INITIATIVE_AGENT="initiative-scorer.md"

if [ ! -d "$CONTEXT_DIR" ]; then
  git clone "$ASSESS_REPO" "$CONTEXT_DIR" 2>&1
else
  git -C "$CONTEXT_DIR" pull --ff-only 2>&1 || echo "WARN: assess-rfe pull failed, using cached version" >&2
fi

# Checkout a specific branch/tag if requested
if [ -n "${ASSESS_RFE_REF:-}" ]; then
  git -C "$CONTEXT_DIR" fetch origin "$ASSESS_RFE_REF" 2>&1
  git -C "$CONTEXT_DIR" checkout FETCH_HEAD 2>&1
fi

# Validate that the rubric file exists after cloning
if [ ! -f "$RUBRIC_FILE" ]; then
  echo "ERROR: Rubric file not found at $RUBRIC_FILE after bootstrap" >&2
  exit 1
fi

if [ "$PIPELINE_TYPE" = "initiative" ] && [ ! -f "$INITIATIVE_RUBRIC" ]; then
  echo "ERROR: Initiative rubric not found at $INITIATIVE_RUBRIC after bootstrap" >&2
  echo "       $ASSESS_REPO${ASSESS_RFE_REF:+ @ $ASSESS_RFE_REF} provides no assess-initiative skill." >&2
  echo "       Set ASSESS_RFE_REPO/ASSESS_RFE_REF to a checkout that has it." >&2
  exit 1
fi

# Copy all skills from the plugin, including their bundled scripts/ so the
# copied SKILL.md's ${CLAUDE_SKILL_DIR}/scripts/... references resolve at
# runtime (scripts are co-located with each SKILL.md as of assess-rfe#5).
for skill_dir in "$CONTEXT_DIR"/skills/*/; do
  skill_name=$(basename "$skill_dir")
  target=".claude/skills/$skill_name"
  mkdir -p "$target"
  cp -r "$skill_dir". "$target/"
done

# Install agent definitions
if [ -d "$CONTEXT_DIR/agents" ]; then
  mkdir -p .claude/agents
  cp "$CONTEXT_DIR"/agents/*.md .claude/agents/
fi

if [ "$PIPELINE_TYPE" = "initiative" ] && [ ! -f ".claude/agents/$INITIATIVE_AGENT" ]; then
  echo "ERROR: Agent definition .claude/agents/$INITIATIVE_AGENT not installed by bootstrap" >&2
  echo "       The initiative assess agent launches with subagent_type: initiative-scorer;" >&2
  echo "       without it the ASSESS phase never completes." >&2
  exit 1
fi

# Export rubric to artifacts
python3 "$CONTEXT_DIR/skills/export-rubric/scripts/export_rubric.py" 2>/dev/null || true
