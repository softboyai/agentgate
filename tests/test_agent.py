import json
import inspect
from types import SimpleNamespace

import pytest

from app import agent, db


@pytest.fixture(autouse=True)
def isolated_gateway_database(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agentgate.db")
    db.init_db()
    agent.identity.seed_demo_agents()


def allowed(data):
    return {
        "status": "allowed",
        "reason": "allowed",
        "message": "Tool completed.",
        "data": data,
    }


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
        agent.gateway,
        "call_tool",
        lambda *_: allowed({"application_id": "APP-1001"}),
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
        agent.gateway,
        "call_tool",
        lambda *_: {
            "status": "error",
            "reason": "tool_execution_failed",
            "message": "Application APP-9999 not found",
        },
    )
    printed: list[str] = []

    result = agent.run_agent("Check an unknown application", print_fn=printed.append)

    assert result == "That application was not found in the fake data."
    assert len(printed) == 1
    assert "APP-9999" in requests[1][-1]["content"]


def test_agent_passes_invalid_json_arguments_back_to_model(monkeypatch) -> None:
    requests = []
    responses = iter(
        [
            make_tool_response("get_application", '{"application_id":'),
            make_final_response("The tool arguments were invalid."),
        ]
    )

    def completion(messages, _tools):
        requests.append([message.copy() for message in messages])
        return next(responses)

    monkeypatch.setattr(agent.llm, "get_chat_completion", completion)
    monkeypatch.setattr(agent.gateway, "call_tool", fail_model_call)

    result = agent.run_agent("Check APP-1001")

    assert result == "The tool arguments were invalid."
    tool_result = json.loads(requests[1][-1]["content"])
    assert tool_result == {
        "status": "denied",
        "reason": "invalid_arguments",
        "message": "Tool arguments are not valid JSON.",
    }


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
        agent.gateway,
        "call_tool",
        lambda *_: allowed(tool_result),
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
        agent.gateway,
        "call_tool",
        lambda *_: allowed(tool_result),
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
        agent.gateway,
        "call_tool",
        lambda token, name, arguments: (
            tool_calls.append((name, arguments))
            or allowed(
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


@pytest.mark.parametrize(
    "prompt",
    [
        "Approve APP-1001",
        "Reject APP-1003",
        "Grant the licence for APP-1002",
    ],
)
def test_agent_refuses_application_decisions_before_model_or_tool(
    prompt: str,
    monkeypatch,
) -> None:
    monkeypatch.setattr(agent.llm, "get_chat_completion", fail_model_call)
    monkeypatch.setattr(agent.gateway, "call_tool", fail_model_call)

    result = agent.run_agent(prompt)

    assert "cannot" in result.lower()
    assert "officer" in result.lower()
    assert "document check" in result.lower()


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


def test_agent_shows_only_tools_permitted_to_selected_agent(monkeypatch) -> None:
    provided_tools = []

    def completion(_messages, tools):
        provided_tools.extend(tools)
        return make_final_response("Done.")

    monkeypatch.setattr(agent.llm, "get_chat_completion", completion)

    assert agent.run_agent("Hello", agent_id="flagger") == "Done."
    assert [tool["function"]["name"] for tool in provided_tools] == [
        "get_application",
        "list_returned_files",
    ]


def test_agent_reports_token_error_while_loading_permitted_tools(monkeypatch) -> None:
    def fail_verification(_token):
        raise agent.identity.TokenVerificationError(
            "suspended_agent",
            "Agent 'reader' is suspended.",
        )

    monkeypatch.setattr(
        agent.gateway,
        "get_permitted_tool_definitions",
        fail_verification,
    )
    monkeypatch.setattr(agent.llm, "get_chat_completion", fail_model_call)

    result = agent.run_agent("Hello")

    assert result == "Cannot load permitted tools: Agent 'reader' is suspended."


def test_suspended_reader_returns_clear_message_before_model_call(monkeypatch) -> None:
    agent.identity.suspend_agent("reader")
    monkeypatch.setattr(agent.llm, "get_chat_completion", fail_model_call)

    result = agent.run_agent("Is APP-1003 ready?")

    assert result == "Cannot start agent: Agent 'reader' is suspended."


def test_agent_reports_application_service_unavailable_without_guessing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        agent.gateway,
        "call_tool",
        lambda *_: {
            "status": "error",
            "reason": "tool_execution_failed",
            "message": "The downstream service is unavailable.",
        },
    )
    monkeypatch.setattr(agent.llm, "get_chat_completion", fail_model_call)

    result = agent.run_agent("Is APP-1003 ready?")

    assert result == (
        "The document check could not be completed: "
        "The downstream service is unavailable."
    )


def test_agent_module_does_not_reference_gateway_service_key() -> None:
    assert "GATEWAY_SERVICE_KEY" not in inspect.getsource(agent)


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
        agent.gateway,
        "call_tool",
        lambda token, name, arguments: (
            executions.append((name, arguments)) or allowed([])
        ),
    )
    printed: list[str] = []

    result = agent.run_agent("List returned files", print_fn=printed.append)

    assert result == "Stopped after reaching the 5-step limit."
    assert len(executions) == 5
    assert calls == 6
    assert len(printed) == 6
