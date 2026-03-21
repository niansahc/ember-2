# src/state/
#
# State layer for Ember-2.
#
# This module will contain:
#   - StateService     — writes and reads state artifacts from the vault
#   - StateResolver    — computes current state from the latest active records
#   - models.py        — typed state object definitions (active_goal, current_focus,
#                        open_loop, blocker, routine, next_action, etc.)
#
# State is distinct from memory (history) and reflection (derived insight).
# It represents current operational truth: what is active, in-progress, or pending.
# All state changes are append-only — "current state" is resolved by selecting
# the latest active record(s) for each state type.
