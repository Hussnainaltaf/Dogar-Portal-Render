#!/usr/bin/env python3
"""Small OpenAI Responses API demo for Dogar Portal.Com.

Usage:
  OPENAI_API_KEY=... python scripts/openai_demo.py --mode text
  OPENAI_API_KEY=... python scripts/openai_demo.py --mode structured
  OPENAI_API_KEY=... python scripts/openai_demo.py --mode tools
  OPENAI_API_KEY=... python scripts/openai_demo.py --mode all

The key is read from the environment and never printed or written to disk.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from openai import APIError, OpenAI

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")


def client() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set. Export it before running this demo.")
    return OpenAI()


def run_text(api: OpenAI, model: str) -> dict[str, Any]:
    response = api.responses.create(
        model=model,
        instructions="You are a concise assistant helping a recruitment-office software team.",
        input="Explain in two sentences how tenant isolation protects candidate data.",
        max_output_tokens=120,
    )
    return {
        "mode": "text",
        "response_id": response.id,
        "model": response.model,
        "output_text": response.output_text,
    }


def run_structured(api: OpenAI, model: str) -> dict[str, Any]:
    response = api.responses.create(
        model=model,
        input=(
            "Extract a candidate contact record from this note: "
            "Ayesha Khan, passport PK1234567, phone +92-300-5550101, "
            "interested in a caregiver role in Riyadh."
        ),
        text={
            "format": {
                "type": "json_schema",
                "name": "candidate_contact",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "full_name": {"type": "string"},
                        "passport_number": {"type": "string"},
                        "phone": {"type": "string"},
                        "role": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": [
                        "full_name",
                        "passport_number",
                        "phone",
                        "role",
                        "destination",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        max_output_tokens=180,
    )
    return {
        "mode": "structured",
        "response_id": response.id,
        "model": response.model,
        "json_text": response.output_text,
        "parsed": json.loads(response.output_text),
    }


def lookup_office(office_slug: str) -> dict[str, Any]:
    """Local function used to demonstrate an application-owned tool."""
    offices = {
        "alpha": {"office": "Alpha Overseas Office", "country": "Pakistan", "status": "active"},
        "bravo": {"office": "Bravo Recruitment Office", "country": "Pakistan", "status": "active"},
    }
    return offices.get(office_slug, {"office": office_slug, "status": "not_found"})


def run_tools(api: OpenAI, model: str) -> dict[str, Any]:
    tools = [
        {
            "type": "function",
            "name": "lookup_office",
            "description": "Look up a recruitment office by its short slug.",
            "parameters": {
                "type": "object",
                "properties": {"office_slug": {"type": "string"}},
                "required": ["office_slug"],
                "additionalProperties": False,
            },
            "strict": True,
        }
    ]
    input_items: list[Any] = [
        {"role": "user", "content": "What is the status of the alpha recruitment office?"}
    ]
    first = api.responses.create(model=model, tools=tools, input=input_items, max_output_tokens=160)
    input_items += first.output
    calls = []
    for item in first.output:
        if item.type != "function_call" or item.name != "lookup_office":
            continue
        args = json.loads(item.arguments)
        result = lookup_office(args["office_slug"])
        calls.append({"name": item.name, "arguments": args, "result": result})
        input_items.append(
            {
                "type": "function_call_output",
                "call_id": item.call_id,
                "output": json.dumps(result),
            }
        )
    if not calls:
        return {
            "mode": "tools",
            "response_id": first.id,
            "model": first.model,
            "tool_calls": [],
            "output_text": first.output_text,
        }
    final = api.responses.create(
        model=model,
        instructions="Answer using the office lookup result. Do not invent office data.",
        tools=tools,
        input=input_items,
        max_output_tokens=160,
    )
    return {
        "mode": "tools",
        "response_id": final.id,
        "model": final.model,
        "tool_calls": calls,
        "output_text": final.output_text,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("text", "structured", "tools", "all"), default="text")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    api = client()
    try:
        if args.mode == "text":
            result: Any = run_text(api, args.model)
        elif args.mode == "structured":
            result = run_structured(api, args.model)
        elif args.mode == "tools":
            result = run_tools(api, args.model)
        else:
            result = [
                run_text(api, args.model),
                run_structured(api, args.model),
                run_tools(api, args.model),
            ]
    except APIError as exc:
        error = getattr(exc, "body", None) or {}
        detail = error.get("error", {}) if isinstance(error, dict) else {}
        result = {
            "ok": False,
            "error_type": getattr(exc, "__class__", type(exc)).__name__,
            "message": detail.get("message") or str(exc),
            "hint": "Check API account billing/quota, then rerun this command. The key itself is not printed.",
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
