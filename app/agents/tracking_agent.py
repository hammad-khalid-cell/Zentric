import json
from app.core.groq_client import client

from app.services.parcel_data import (
    get_shipment_status,
    get_expected_delivery,
    check_delay,
)

# Maps tool name (string) -> actual Python function
AVAILABLE_TOOLS = {
    "get_shipment_status": get_shipment_status,
    "get_expected_delivery": get_expected_delivery,
    "check_delay": check_delay,
}

# Tool schemas — this is the "contract" Groq uses to know what it can call
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_shipment_status",
            "description": "Get the current status and hub location of a parcel by tracking number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tracking_number": {
                        "type": "string",
                        "description": "The parcel's tracking number, e.g. TRK12345",
                    }
                },
                "required": ["tracking_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_expected_delivery",
            "description": "Get the expected delivery date of a parcel by tracking number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tracking_number": {
                        "type": "string",
                        "description": "The parcel's tracking number, e.g. TRK12345",
                    }
                },
                "required": ["tracking_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_delay",
            "description": "Check whether a parcel is delayed compared to its expected delivery date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tracking_number": {
                        "type": "string",
                        "description": "The parcel's tracking number, e.g. TRK12345",
                    }
                },
                "required": ["tracking_number"],
            },
        },
    },
]


def run_tracking_agent(user_message: str) -> str:
    system_prompt = (
        "You are a helpful logistics customer support assistant for a Pakistani "
        "courier company. Customers may write in English, Roman Urdu, or a mix of "
        "both. Use the available tools to answer questions about parcel tracking, "
        "delivery dates, and delays. Never guess a parcel's status — always use a "
        "tool. Reply naturally and concisely, in the same language style the "
        "customer used."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # First call — model decides whether to use a tool
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=messages,
        tools=TOOL_SCHEMAS,
        tool_choice="auto",
        temperature=0,
    )

    response_message = response.choices[0].message

    if not response_message.tool_calls:
        # Model answered directly without needing a tool
        return response_message.content

    # Model wants to call one or more tools
    messages.append(response_message)

    for tool_call in response_message.tool_calls:
        function_name = tool_call.function.name
        function_args = json.loads(tool_call.function.arguments)

        function_to_call = AVAILABLE_TOOLS.get(function_name)
        if function_to_call:
            result = function_to_call(**function_args)
        else:
            result = {"error": f"Unknown tool {function_name}"}

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": function_name,
                "content": json.dumps(result),
            }
        )

    # Second call — model turns the tool's result into a natural language reply
    second_response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=messages,
        temperature=0,
    )

    return second_response.choices[0].message.content