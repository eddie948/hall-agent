# 展厅保障接待 Agent

企业微信智能机器人长连接服务。每个单聊用户或群聊都是独立数据空间，系统会为当前空间自动创建一张固定格式的「外部客户到访登记与接待报备表」，用于登记、查询、更新和取消展厅保障相关到访记录。

本地 SQLite 是唯一业务数据源，不依赖企业微信智能表格或其他云端表格。

## 功能

- 个人和群数据空间隔离。
- 首次使用时自动创建固定接待报备表。
- 按固定表 Schema 创建、确认、查询、修改和取消到访记录。
- 每轮处理前按当前时间自动刷新接待状态：待接待、接待中、已完成。
- 当前未完成接待记录会作为数据库快照放入模型上下文，用于加速查询和冲突初筛。
- 所有正式到访记录固定在到访开始前 2 小时提醒。
- 记录时间修改后自动重算提醒，记录取消后自动取消提醒。
- 企业微信个人/群主动推送、失败重试和发送幂等。
- 消息幂等、多轮历史、工具审计和模型输入快照。

## 安装

推荐在 WSL 中使用 `pyenv + venv` 管理本项目环境：

```bash
cd /home/terra/programs/hall_agent

PYENV_VERSION=3.11.9 python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

后续每次进入项目先激活虚拟环境：

```bash
cd /home/terra/programs/hall_agent
source .venv/bin/activate
```

至少配置：

```text
WECOM_BOT_ID
WECOM_BOT_SECRET
DASHSCOPE_API_KEY
```

初始化本地数据库：

```bash
python -m src.admin.init_db
```

默认后台启动：

```bash
./scripts/start.sh
```

本地调试（不连企业微信，独立端口/库 + 静态聊天页）：

```bash
./scripts/start_local.sh
# 浏览器打开 http://127.0.0.1:8101/
```

查看状态和当天日志：

```bash
./scripts/status.sh
tail -f "data/logs/app_$(date +%F).log"
```

停止服务：

```bash
./scripts/stop.sh
```

日志按 `Asia/Shanghai` 自然日写入 `data/logs/app_YYYY-MM-DD.log`，默认保留 30 天。后台启动时不会重复输出到终端；需要前台调试时使用：

```bash
LOG_CONSOLE=true python -m src.run
```

健康检查：

```bash
curl http://127.0.0.1:8100/health
```

## 运行监控（Logfire）

服务启动时会自动接入 [Pydantic Logfire](https://pydantic.dev/docs/logfire/get-started/)，上报 FastAPI 请求、HTTP 调用、SQLite、Pydantic 校验和 Pydantic AI Agent 调用等 trace。目标项目为 `wanggaoxiang041/hall-agent`，控制台地址：

```text
https://logfire-us.pydantic.dev/wanggaoxiang041/hall-agent
```

在 `.env` 中配置以下变量（详见 `.env.example`）：

```text
LOGFIRE_ENABLED=true
LOGFIRE_TOKEN=
LOGFIRE_BASE_URL=https://logfire-us.pydantic.dev
LOGFIRE_SERVICE_NAME=hall-agent
LOGFIRE_ENVIRONMENT=local
LOGFIRE_CONSOLE=false
LOGFIRE_PYDANTIC_RECORD=failure
```


| 变量                        | 说明                                                                  |
| ------------------------- | ------------------------------------------------------------------- |
| `LOGFIRE_ENABLED`         | 设为 `false` 可完全关闭上报；未设置时，有 token 则自动启用                               |
| `LOGFIRE_TOKEN`           | Logfire 控制台 → **hall-agent** → Settings → **Write token**；CI/容器部署必填 |
| `LOGFIRE_BASE_URL`        | 美区实例，保持 `https://logfire-us.pydantic.dev`                           |
| `LOGFIRE_SERVICE_NAME`    | 在 Live 中按 `service.name` 过滤，默认 `hall-agent`                         |
| `LOGFIRE_ENVIRONMENT`     | 环境标签，如 `local`、`staging`、`production`                               |
| `LOGFIRE_CONSOLE`         | 设为 `true` 可在终端同步输出 span（调试用）                                        |
| `LOGFIRE_PYDANTIC_RECORD` | Pydantic 校验埋点粒度，默认 `failure` 仅记录失败                                  |


**本地开发** 二选一：

1. **Write token（推荐）**：将 token 填入 `.env` 的 `LOGFIRE_TOKEN`。
2. **CLI OAuth**：不填 token，在项目目录执行：

```bash
logfire --base-url='https://logfire-us.pydantic.dev' auth
logfire --base-url='https://logfire-us.pydantic.dev' projects use --org 'wanggaoxiang041' 'hall-agent'
```

凭证会写入 `.logfire/`（已在 `.gitignore` 中，勿提交）。

**验证**：启动服务并访问 `/health` 后，在 Logfire Live 中搜索 `service.name = 'hall-agent'`，应能看到对应 HTTP span。未配置 token 且未执行 CLI 认证时，应用仍可正常运行，只是不会上报监控数据。

## 使用示例

添加到访记录：

```text
下周二下午三点到四点，腾讯科技 5 人到访深圳福田，主来访人张三总监，内部陪同李四和王五，需要展厅接待和 VIP 接待室。
```

查询到访记录：

```text
查一下下周深圳福田有哪些客户到访。
```

更新展厅准备状态：

```text
把腾讯科技那条到访的展厅准备状态改成准备就绪。
```

查询接待状态：

```text
现在还有哪些未完成接待？
```

提醒策略：

```text
所有正式到访记录都会在到访开始前 2 小时自动提醒；暂不开放手工提醒、默认提醒策略调整和提醒明细查询。
```

私聊数据只属于该用户；群聊数据属于当前群。群成员可以共同管理群记录，表格式和默认提醒策略由系统固定。

## 数据与提醒

数据库默认位于：

```text
data/hall_agent.db
```

企业微信主动推送要求目标个人或群此前与机器人发生过消息交互。系统在收到可信消息回调时记录会话可达状态。

提醒调度器每分钟扫描到期投递，使用租约和幂等键避免重复发送，失败后按 1、5、15 分钟退避重试。

## 测试

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
```

禁用自动插件加载是为了避免系统环境中的非项目 pytest 插件影响测试；不影响项目自身测试能力。

## 文档

- [动态记录与提醒系统设计](docs/动态记录与提醒系统设计.md)
- [企业微信端到端测试指南](docs/企业微信端到端测试指南.md)
- [模型输入快照实现说明](docs/模型输入快照实现说明.md)

