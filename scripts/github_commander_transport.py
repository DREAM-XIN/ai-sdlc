#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from commander import commander_plan_errors


def render_transport(plan):
    errors = commander_plan_errors(plan)
    if errors:
        return {"outcome": "INVALID", "errors": errors}

    summary = [
        "# AI-SDLC Commander",
        "",
        f"- Feature: `{plan.get('feature_id') or 'unknown'}`",
        f"- Outcome: **{plan['outcome']}**",
        f"- Workflow: `{plan['summary'].get('workflow_status')}`",
        f"- Current stage: `{plan['summary'].get('current_stage')}`",
    ]
    prompts = []

    if plan["errors"]:
        summary.extend(["", "## Errors"])
        summary.extend(f"- {item}" for item in plan["errors"])

    if plan["dispatches"]:
        summary.extend(["", "## Dispatches"])
        for index, dispatch in enumerate(plan["dispatches"], start=1):
            action = dispatch["action"]
            runtime = dispatch["runtime"]
            summary.extend(
                [
                    f"### {index}. {action['stage']} → {action['role']}",
                    f"- Runtime: `{runtime['id']}/{runtime['mode']}`",
                    f"- Route: `{', '.join(dispatch['route_ids'])}`",
                ]
            )
            if "prompt" in dispatch:
                summary.append("- ChatGPT Web prompt: included in `chatgpt-web-prompts.txt`")
                prompts.append(
                    f"===== {action['stage']} / {action['role']} =====\n{dispatch['prompt'].rstrip()}\n"
                )
            else:
                summary.append("- Execution payload: delegated to its runtime adapter; not executed by this workflow")

    if not plan["dispatches"]:
        summary.extend(["", "No runtime dispatch is available for this state."])

    return {
        "outcome": "RENDERED",
        "errors": [],
        "summary": "\n".join(summary).rstrip() + "\n",
        "prompts": "\n".join(prompts).rstrip() + ("\n" if prompts else ""),
    }


def main():
    parser = argparse.ArgumentParser(description="Render a Commander Plan for GitHub Actions")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--prompts", type=Path, required=True)
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    result = render_transport(plan)
    if result["outcome"] == "INVALID":
        print(json.dumps(result, indent=2, sort_keys=True))
        raise SystemExit(2)
    args.summary.write_text(result["summary"], encoding="utf-8")
    args.prompts.write_text(result["prompts"], encoding="utf-8")
    print(json.dumps({"outcome": "RENDERED", "prompt_count": result["prompts"].count("=====") // 2}, sort_keys=True))


if __name__ == "__main__":
    main()
