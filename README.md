# 商品庫存監控與智慧提醒系統 (Inventory Notifier Agent)

支援電商（Costco 好市多、PChome 等）商品庫存即時偵測、背景自動巡檢、桌面推播與 Telegram 雙向通報之智慧助理。

---

## 🚀 本地快速啟動（Web 控制台與本機桌面通知）

無需設定複雜的雲端環境，直接在本地電腦執行即可開啟完整的圖形控制台與桌面通知：

### 1. 安裝必要套件（若尚未安裝）
```bash
pip install fastapi uvicorn httpx beautifulsoup4 python-dotenv
```

### 2. 啟動 Web 控制台
```bash
python3 run_local_app.py
```

啟動後，請在瀏覽器開啟：
👉 **http://localhost:8000**

* 🖥️ **視覺化介面**：支援貼上網址自動解析加入追蹤、查看即時庫存清單、單鍵手動重新爬蟲與移除商品。
* 🔔 **本機桌面通知**：點擊頁面上的「啟用瀏覽器本機通知」，補貨或缺貨時直接在電腦右下角彈出推播與音效。
* ⚙️ **偏好設定**：自由調整背景掃描週期（1~10分鐘）、缺貨通報頻率，並可隨時開啟或關閉 Telegram / 本機通知。

---

## 🤖 啟動純 Telegram Bot（可選）

若只想以純 Telegram 聊天介面進行操作：
```bash
python3 run_local_bot.py
```

---

## Project Structure


```
simple-agent/
├── app/         # Core agent code
│   ├── agent.py               # Main agent logic
│   ├── fast_api_app.py        # FastAPI Backend server
│   └── app_utils/             # App utilities and helpers
├── tests/                     # Unit, integration, and load tests
├── GEMINI.md                  # AI-assisted development guide
└── pyproject.toml             # Project dependencies
```

> 💡 **Tip:** Use [Antigravity CLI](https://antigravity.google/) for AI-assisted development - project context is pre-configured in `GEMINI.md`.

## Requirements

Before you begin, ensure you have:
- **uv**: Python package manager (used for all dependency management in this project) - [Install](https://docs.astral.sh/uv/getting-started/installation/) ([add packages](https://docs.astral.sh/uv/concepts/dependencies/) with `uv add <package>`)
- **agents-cli**: Agents CLI - Install with `uv tool install google-agents-cli`
- **Google Cloud SDK**: For GCP services - [Install](https://cloud.google.com/sdk/docs/install)


## Quick Start

Install `agents-cli` and its skills if not already installed:

```bash
uvx google-agents-cli setup
```

Install required packages:

```bash
agents-cli install
```

Test the agent with a local web server:

```bash
agents-cli playground
```

You can also use features from the [ADK](https://adk.dev/) CLI with `uv run adk`.

## Commands

| Command              | Description                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `agents-cli install` | Install dependencies using uv                                                         |
| `agents-cli playground` | Launch local development environment                                                  |
| `agents-cli lint`    | Run code quality checks                                                               |
| `agents-cli eval`    | Evaluate agent behavior (generate, grade, analyze, and more — see `agents-cli eval --help`) |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests                                                        |
| `agents-cli deploy`  | Deploy agent to Agent Runtime                                                                |
| `agents-cli publish gemini-enterprise` | Register deployed agent to Gemini Enterprise                    || [A2A Inspector](https://github.com/a2aproject/a2a-inspector) | Launch A2A Protocol Inspector                                                        |

## 🛠️ Project Management

| Command | What It Does |
|---------|--------------|
| `agents-cli scaffold enhance` | Add CI/CD pipelines and Terraform infrastructure |
| `agents-cli infra cicd` | One-command setup of entire CI/CD pipeline + infrastructure |
| `agents-cli scaffold upgrade` | Auto-upgrade to latest version while preserving customizations |

---

## Development

Edit your agent logic in `app/agent.py` and test with `agents-cli playground` - it auto-reloads on save.

## Deployment

```bash
gcloud config set project <your-project-id>
agents-cli deploy
```

To add CI/CD and Terraform, run `agents-cli scaffold enhance`.
To set up your production infrastructure, run `agents-cli infra cicd`.

## Observability

Built-in telemetry exports to Cloud Trace, BigQuery, and Cloud Logging.

## A2A Inspector

This agent supports the [A2A Protocol](https://a2a-protocol.org/). Use the [A2A Inspector](https://github.com/a2aproject/a2a-inspector) to test interoperability.
See the [A2A Inspector docs](https://github.com/a2aproject/a2a-inspector) for details.
