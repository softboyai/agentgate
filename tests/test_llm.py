from types import SimpleNamespace

from app import config, llm


def test_chat_completion_uses_deepseek_through_openai_client(monkeypatch) -> None:
    calls = {}

    def create_client(*, api_key, base_url):
        calls["api_key"] = api_key
        calls["base_url"] = base_url
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: calls.update(request=kwargs) or "response"
                )
            )
        )

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "fake-test-key")
    monkeypatch.setattr(config, "MODEL", "deepseek-chat")
    monkeypatch.setattr(llm, "OpenAI", create_client)
    messages = [{"role": "user", "content": "Check fake data"}]
    tools = []

    response = llm.get_chat_completion(messages, tools)

    assert response == "response"
    assert calls["api_key"] == "fake-test-key"
    assert calls["base_url"] == "https://api.deepseek.com"
    assert calls["request"]["model"] == "deepseek-chat"
    assert calls["request"]["messages"] == messages
    assert calls["request"]["tools"] == tools
    assert calls["request"]["temperature"] == 0
