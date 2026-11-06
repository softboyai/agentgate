import json
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


def fail_model_call(*_):
    raise AssertionError("model should not answer a readiness question")


def test_agent_checks_application_and_prints_tool_call(monkeypatch) -> None:
    responses = iter(
        [
            make_tool_response(
                "get_application",
                '{"application_id": "APP-1001"}',
            ),
            make_final_response("The fake application has been checked."),
        ]
    )
    monkeypatch.setattr(agent.llm, "get_chat_completion", lambda *_: next(responses))
    monkeypatch.setattr(
        agent,
        "execute_tool",
        lambda *_: '{"application_id": "APP-1001"}',
    )
    printed: list[str] = []

    result = agent.run_agent("Check APP-1001", print_fn=printed.append)

    assert result == "The fake application has been checked."
    assert printed == [
        'Tool call: get_application({"application_id": "APP-1001"})'
    ]


def test_agent_returns_unknown_application_error_to_model(monkeypatch) -> None:
    requests = []
    responses = iter(
        [
            make_tool_response(
                "get_application",
                '{"application_id": "APP-9999"}',
            ),
            make_final_response("That application was not found in the fake data."),
        ]
    )

    def completion(messages, _):
        requests.append([message.copy() for message in messages])
        return next(responses)

    monkeypatch.setattr(agent.llm, "get_chat_completion", completion)
    monkeypatch.setattr(
        agent,
        "execute_tool",
        lambda *_: '{"error": "Application APP-9999 not found", "status_code": 404}',
    )
    printed: list[str] = []

    result = agent.run_agent("Check an unknown application", print_fn=printed.append)

    assert result == "That application was not found in the fake data."
    assert len(printed) == 1
    assert "APP-9999" in requests[1][-1]["content"]


def test_agent_uses_only_document_check_issues_for_readiness(monkeypatch) -> None:
    calls = []

    def completion(messages, _):
        calls.append(messages)
        return make_tool_response(
            "check_documents",
            '{"application_id": "APP-1007"}',
        )

    tool_result = {
        "application_id": "APP-1007",
        "business_size": "MSME",
        "uses_plastics": False,
        "required_documents": ["lease", "occupancy_permit"],
        "passed": False,
        "issues": [
            {
                "code": "missing",
                "document": "lease",
                "message": "Required document 'lease' is missing.",
            }
        ],
    }
    monkeypatch.setattr(agent.llm, "get_chat_completion", completion)
    monkeypatch.setattr(
        agent,
        "execute_tool",
        lambda *_: json.dumps(tool_result),
    )

    result = agent.run_agent("Is APP-1007 ready?")

    assert calls == []
    assert result == (
        "The document check did not pass.\n"
        "Required documents listed by check_documents: lease, occupancy_permit\n"
        "Issues reported by check_documents:\n"
        "- Required document 'lease' is missing."
    )
    assert "proof_of_payment" not in result
    assert "esia" not in result


def test_agent_stops_with_plain_passed_answer(monkeypatch) -> None:
    calls = []
    tool_result = {
        "application_id": "APP-1002",
        "business_size": "MSME",
        "uses_plastics": False,
        "required_documents": [
            "lease",
            "occupancy_permit",
            "rra_classification",
            "trademark",
        ],
        "passed": True,
        "issues": [],
    }

    def completion(*args):
        calls.append(args)
        return make_tool_response(
            "check_documents",
            '{"application_id": "APP-1002"}',
        )

    monkeypatch.setattr(agent.llm, "get_chat_completion", completion)
    monkeypatch.setattr(
        agent,
        "execute_tool",
        lambda *_: json.dumps(tool_result),
    )

    result = agent.run_agent("Is APP-1002 ready?")

    assert result == "The document check passed."
    assert calls == []


def test_readiness_prompt_directly_uses_check_documents(monkeypatch) -> None:
    monkeypatch.setattr(
        agent.llm,
        "get_chat_completion",
        fail_model_call,
    )
    tool_calls = []
    monkeypatch.setattr(
        agent,
        "execute_tool",
        lambda name, arguments: (
            tool_calls.append((name, json.loads(arguments)))
            or json.dumps(
                {
                    "application_id": "APP-1002",
                    "business_size": "MSME",
                    "uses_plastics": False,
                    "required_documents": [
                        "lease",
                        "occupancy_permit",
                        "rra_classification",
                        "trademark",
                    ],
                    "passed": True,
                    "issues": [],
                }
            )
        ),
    )
    printed = []

    result = agent.run_agent("Is APP-1002 ready?", print_fn=printed.append)

    assert tool_calls == [("check_documents", {"application_id": "APP-1002"})]
    assert printed == ['Tool call: check_documents({"application_id": "APP-1002"})']
    assert result == "The document check passed."


def test_readiness_prompt_without_application_id_does_not_guess(monkeypatch) -> None:
    monkeypatch.setattr(
        agent.llm,
        "get_chat_completion",
        fail_model_call,
    )

    result = agent.run_agent("Are the application documents complete?")

    assert result == "Please provide an application ID so I can check its documents."


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
            "list_returned_files",
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

    result = agent.run_agent("List returned files", print_fn=printed.append)

    assert result == "Stopped after reaching the 5-step limit."
    assert len(executions) == 5
    assert calls == 6
    assert len(printed) == 6
