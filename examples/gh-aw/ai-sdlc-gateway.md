---
description: "Example gh-aw gateway that routes an AI-SDLC dispatch decision to an allowlisted worker workflow."
on:
  workflow_dispatch:
    inputs:
      dispatch_payload:
        description: "JSON payload produced by the AI-SDLC control plane"
        required: true
        type: string
safe-outputs:
  dispatch-workflow:
    workflows:
      - ai-sdlc-worker
    max: 5
---

# AI-SDLC gh-aw Gateway Example

You are an adapter, not the AI-SDLC state machine.

The authoritative workflow state, Gate rules, runtime routing policy, and Feature transitions are computed outside this workflow by the deterministic AI-SDLC control plane.

Read `dispatch_payload` and decide whether the requested action can be handed to the allowlisted `ai-sdlc-worker` workflow. Do not invent lifecycle state or bypass a Gate. If the payload is incomplete or conflicts with repository policy, emit no dispatch.

When dispatching, preserve the original task/feature identifiers in the worker inputs so its result can be converted back into a Feature Event.
