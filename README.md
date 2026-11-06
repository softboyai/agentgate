# AgentGate Stage 2: Fake Systems

AgentGate is a demo control layer for AI agents. This stage uses fake food and
cosmetics licensing applications and fake review queues. It is inspired by a
public harmonised-service case, but is not an official RAIA, Irembo, MINICOM, or
government product. The sample data and document checks are invented for this
demo and are not official rules. No real personal or application data is used.

## Run the fake systems

Set a local `GATEWAY_SERVICE_KEY` in `.env` (copy `.env.example` if needed).
The key is required; the placeholder from `.env.example` is for local demos only.
Open two PowerShell windows at the repository root and run one command in each:

```powershell
.\.venv\Scripts\python.exe -m uvicorn systems.application_system:app --reload --host 127.0.0.1 --port 8001
```

```powershell
.\.venv\Scripts\python.exe -m uvicorn systems.review_system:app --reload --host 127.0.0.1 --port 8002
```

OpenAPI docs are at `http://127.0.0.1:8001/docs` and
`http://127.0.0.1:8002/docs`. Both services require the `X-Gateway-Key` header.
The Stage 1 agent tools call these services over HTTP.

Run the Stage 2 tests from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

This is a demo only. Real use would require official rules, security review,
real integration work, and in-country hosting.
