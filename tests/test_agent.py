from types import SimpleNamespace

from app import agent


def make_tool_response(name: str, arguments: str, call_id: str = "call-1"):
    tool_call = SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )
    message = SimpleNamespace(
        tool_calls=[tool_call],
        content=None,
        model_dump=lambda exclude_none=True: {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        },
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_final_response(content: str):
    message = SimpleNamespace(tool_calls=None, content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_agent_looks_up_kivu_juice_and_prints_tool_call(monkeypatch) -> None:
    responses = iter(
        [
            make_tool_response(
                "get_licence_status",
                '{"business": "Kivu Juice Co"}',
            ),
            make_final_response("The fake licence is active."),
        ]
    )
    monkeypatch.setattr(agent.llm, "get_chat_completion", lambda *_: next(responses))
    printed: list[str] = []

    result = agent.run_agent("Check LIC-1001", print_fn=printed.append)

    assert result == "The fake licence is active."
    assert printed == [
        'Tool call: get_licence_status({"business": "Kivu Juice Co"})'
    ]


def test_agent_returns_unknown_business_error_to_model(monkeypatch) -> None:
    requests = []
    responses = iter(
        [
            make_tool_response(
                "get_licence_status",
                '{"business": "Unknown Fake Business"}',
            ),
            make_final_response("That business was not found in the fake data."),
        ]
    )

    def completion(messages, _):
        requests.append([message.copy() for message in messages])
        return next(responses)

    monkeypatch.setattr(agent.llm, "get_chat_completion", completion)
    printed: list[str] = []

    result = agent.run_agent("Check an unknown business", print_fn=printed.append)

    assert result == "That business was not found in the fake data."
    assert len(printed) == 1
    assert "Business not found" in requests[1][-1]["content"]


def test_agent_answers_unrelated_question_without_tool_call(monkeypatch) -> None:
    monkeypatch.setattr(
        agent.llm,
        "get_chat_completion",
        lambda *_: make_final_response("Kigali is the capital of Rwanda."),
    )
    printed: list[str] = []

    result = agent.run_agent("What is the capital of Rwanda?", print_fn=printed.append)

    assert result == "Kigali is the capital of Rwanda."
    assert printed == []


def test_agent_caps_execution_at_five_tool_calls(monkeypatch) -> None:
    calls = 0
    executions = []

    def completion(*_):
        nonlocal calls
        calls += 1
        return make_tool_response(
            "list_open_complaints",
            "{}",
            call_id=f"call-{calls}",
        )

    monkeypatch.setattr(agent.llm, "get_chat_completion", completion)
    monkeypatch.setattr(
        agent,
        "execute_tool",
        lambda name, arguments: executions.append((name, arguments)) or "[]",
    )
    printed: list[str] = []

    result = agent.run_agent("List complaints", print_fn=printed.append)

    assert result == "Stopped after reaching the 5-step limit."
    assert len(executions) == 5
    assert calls == 6
    assert len(printed) == 6
