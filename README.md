
---

## ✅ `README.md` for `ForgeIQ Backend` (`apps/forgeiq-backend/`)

```markdown
# 🔧 ForgeIQ Backend

The **ForgeIQ backend** is a DAG-native build orchestrator powered by autonomous agents. It provides the core execution engine for agent routing, task execution, and telemetry collection within the ForgeIQ and AutoSoft platforms.

---

## 🧠 Core Capabilities

- 🧬 Agent registration + heartbeat
- 🧪 TestAgent + DebugIQ integration
- 📦 Task routing + pipeline triggering
- 🧠 Optional integration with proprietary AlgorithmAgent + MCP

---

## 🧩 Tech Stack

- **Language:** Python
- **Framework:** [FastAPI](https://fastapi.tiangolo.com/)
- **Architecture:** Agent-oriented microservices
- **Persistent Logs:** Optional file/S3 with GovernanceAgent

---

## 📦 Key Endpoints

| Route             | Function                                  |
|------------------|-------------------------------------------|
| `POST /pipeline` | Trigger a full DAG execution run          |
| `POST /trigger-build` | Plan only, no execution             |
| `POST /execute-tasks` | Explicit agent task execution        |
| `POST /agent/register` | Add new agent to registry          |
| `GET /agents`     | View all registered agents                |

---

## 🔧 Getting Started

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
