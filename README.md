# codex-worker

让 Claude Code 把一个边界清楚的任务交给本机的 OpenAI Codex CLI 去做，并为每次运行留下完整记录。macOS、Linux 和 Windows 都能用，只需要 Python 3.9 及以上（只用标准库）。

## 组成

| 文件 | 作用 |
|---|---|
| `codex_task.py` | wrapper：限制模型和推理强度，控制并发，组装 `codex exec` 命令，记录每次运行 |
| `preamble.md` | 每个任务前面自动加上的固定说明（可在配置里换成项目自己的） |
| `browser_preflight.md` | 加 `--browser` 时再加上的浏览器说明：怎样连上你的浏览器，只用一个专用标签页 |
| `agent/codex-worker.md` | Claude Code 子代理模板，由 `install.py` 填入本机路径 |
| `install.py` | 把子代理装到 `~/.claude/agents/`（所有项目可用）或某个项目的 `.claude/agents/` |
| `tests/` | 用假的 Codex 测试 wrapper，不联网、不耗额度 |

## 在新电脑（包括 Windows）上安装

1. 安装 Python 3.9+、Git 和 Node.js。
2. 安装并登录 Codex CLI：
   ```
   npm install -g @openai/codex
   codex login
   ```
3. 把本文件夹复制到那台电脑（或从 git 仓库克隆），在文件夹里运行：
   ```
   python install.py --check
   python install.py
   ```
   Windows 上 `python` 可能叫 `py`。`--check` 只检查 Python、git、Codex 是否找得到；不带参数则安装子代理。只想装到某个项目时用 `python install.py --project <项目路径>`。
4. 重启 Claude Code 或新开一个会话。之后在任何 git 项目里都可以让 Claude 用 `codex-worker` 子代理派活，或者直接运行：
   ```
   python <路径>/codex_task.py --name my-task --effort medium --prompt-file brief.md
   ```

## 用法

```
python codex_task.py --name <slug> --effort <low|medium|high|xhigh>
                     [--model <id>] [--network] [--browser] [--fast | --no-fast] [--auth <login|api>]
                     [--approvals <auto|never>] [--resume <session_id> | --continue]
                     [--wait <秒>] [--root <仓库>]
                     [--with <额外前言名>] [--prompt-file <文件>]     # 也可以从标准输入传入任务
```

- **模型**：只接受配置里 `allowed_models` 列出的模型，第一个是默认值。
- **Fast**：Fast 就是 Codex 的 `priority` 服务档。默认关闭，`--fast` 开启。在配置里设 `allow_fast: false` 后，`--fast` 一律无效。
- **登录方式**：默认 `--auth login`，用 `codex login` 存下的登录（例如 ChatGPT 账号）。`--auth api` 只让这一次运行改用 API key 计费：key 从环境变量 `OPENAI_API_KEY`（可用 `api_key_env` 改名）读取，以 `CODEX_API_KEY` 传给 Codex，不写入任何文件，也不改动 `codex login` 的登录。用 API 额度时，Fast 会按更贵的 priority 档计费。
- **沙箱和审批**：沙箱固定为 `workspace-write`。默认 `--approvals auto`：Codex 要做沙箱不允许的事（联网、写项目外的文件、运行被拦下的命令）时，先申请，由 Codex 的 Auto-review 审阅模型当场批准或拒绝（`approval_policy=on-request` 加 `approvals_reviewer=auto_review`）。Codex 不一定会主动申请（比如联网失败时它可能直接汇报失败），所以这个模式下 wrapper 会在任务里加一句提示：被沙箱挡住就申请放开，被拒绝就不要绕。它会拦下外传密钥或隐私、翻找密码和 cookie、大范围削弱安全设置、高风险且不可逆的破坏；连续被拒 3 次或最近 50 次里被拒 10 次，这一轮会中断。它不是确定的安全保证。`--approvals never`（或配置 `approvals: "never"`）恢复旧行为：被沙箱挡住的操作直接失败。`--network` 等于事先允许联网，不必逐次审阅。
- **继续任务**：`--resume <session_id>` 继续指定的会话。`--continue` 继续这个仓库里同一个 `--name` 最近一次的会话（记录在 `<runs_dir>/sessions.json`），Codex 记得上次读过、改过什么。
- **浏览器**：`--browser` 让 Codex 通过 Codex Chrome 扩展操作你自己的浏览器（带着你的登录状态），并在任务前加上 `browser_preflight.md`。Codex 自带的 `cua_repl` 在桌面应用以外会报 “The browser access host is unavailable”（[openai/codex#57641](https://github.com/openai/codex/issues/57641)），所以这里用社区找到的绕法：在 Codex 的 `node_repl` 工具里加载插件自带的 `browser-client.mjs` 并调用 `setupBrowserRuntime()`。这条路没有官方支持，等于绕过了桌面应用的授权，以后的更新可能让它失效。预检要求 Codex 回报 `BROWSER_PREFLIGHT: OK` 或 `FAIL`，只用一个专用标签页，不许输入密码、处理验证码、购买、接受新条款，也不许改插件文件和授权变量。`meta.json` 的 `browser_evidence` 记下 `node_repl` 调用次数、失败次数、初始化次数和预检结果。前提：Chrome 里装了 Codex 扩展，`~/.codex/config.toml` 里有 `[mcp_servers.node_repl]`（装了 ChatGPT 桌面应用会自动写入）。不需要 `--network`。配置 `allow_browser: false` 可彻底关掉。Windows 上还没验证过。
- **退出码**：退出码与 Codex 的相同。`2` 表示参数或规则错误；`75` 表示并发名额已满，可以加 `--wait 600` 排队等待。

## 配置

依次读取三层设置，后读到的覆盖先读到的：内置默认值、`~/.codex-worker/config.json`（本机通用）、`<仓库>/.codex-worker.json`（单个项目）。可用的键：

```json
{
  "allowed_models": ["gpt-6.1-sol", "gpt-6-astra"],
  "max_concurrent": 8,
  "fast_default": false,
  "allow_fast": true,
  "approvals": "auto",
  "allow_browser": true,
  "browser_preamble": "",
  "codex_bin": "",
  "runs_dir": ".codex_runs",
  "preamble": "",
  "extra_preambles": {"style": "docs/codex_style.md"},
  "lock_dir": "",
  "require_git": true,
  "exclude_from_changes": [],
  "track_changes": true,
  "auth": "login",
  "api_key_env": "OPENAI_API_KEY"
}
```

- **`max_concurrent`**：限制整台电脑同时跑的数量。名额是 `~/.codex-worker/locks/` 下的文件夹，进程意外退出后留下的名额会自动回收。
- **`runs_dir`**：每次运行的记录放在 `<runs_dir>/<UTC时间>_<name>/`，里面有：
  - `prompt.md`
  - `command.txt`
  - `events.jsonl`
  - `last_message.md`
  - `stderr.log`
  - `meta.json`：模型、推理强度、Fast、审批方式、登录方式、浏览器、token 用量、session id、退出码、运行前后的 git HEAD，以及运行期间改动的文件
- **`track_changes` / `exclude_from_changes`**：为了在 `meta.json` 里列出运行期间改动的文件，wrapper 在运行前后各跑一次 `git status`，只看每个未提交或未跟踪文件的大小和修改时间，不读文件内容（大量数据文件的仓库里也只要零点几秒）。这一步只在本机进行，不会发给 Codex，也不耗 token。仍嫌慢可以用 `exclude_from_changes` 跳过某些目录，或设 `track_changes: false` 整个关掉（`changed_files` 记为 `null`）。
- **`codex_bin`**：留空时自动查找 Codex：先查 `PATH`，再查 Windows 的 `%APPDATA%\npm\codex.cmd`、macOS 的 `~/.local/bin/codex` 和 Homebrew 路径。也可以用环境变量 `CODEX_BIN` 指定。
- **`preamble` / `extra_preambles`**：相对路径都按仓库根目录解析。`--with style` 会在任务前再加上对应的说明文件。

`events.jsonl` 可能含有文件片段，受版权保护的内容不要提交到 git。

## 让 Codex 继承上下文和风格

Codex 每次运行都从零开始。三个办法，按可靠程度排：

1. **AGENTS.md**：Codex 每次运行都会自动读，前言也要求它遵守。`~/.codex/AGENTS.md` 对所有项目生效，项目根目录的 `AGENTS.md` 只对这个项目生效。把你的风格（用词、格式、命名、不要的做法）和几个“照这个写”的范例文件路径写进去。
2. **`--continue`**：同一件事的后续步骤用同一个 `--name` 加 `--continue`，Codex 接着上次的对话做。
3. **Codex memories**：在 `~/.codex/config.toml` 里设 `[features]` 下的 `memories = true`，Codex 会把闲置的对话整理成本机记忆，以后自动带上。默认关闭；官方文档没说 `codex exec` 的运行会不会被整理进去，只把它当作辅助。

这些都会增加输入 token：AGENTS.md 每次运行都会整份带上；`--continue` 每一轮都会带上之前的全部对话（太长时 Codex 会自动压缩）。重复的部分大多命中缓存（`meta.json` 的 `usage.cached_input_tokens`），用 API key 时按缓存价计费，但用 ChatGPT 订阅时仍算进用量额度。所以 AGENTS.md 只写必须遵守的规则和范例路径，不要贴整篇范例；不相关的新任务换一个 `--name`，不要一直 `--continue`。

## 测试

```
python -m unittest discover -s tests -v
```

测试内容：正常运行、模型限制、Fast 开关、审批方式、按名字续跑、浏览器、登录方式、继续会话、退出码透传、并发名额与回收、空任务。已在 macOS 上用 Python 3.9 和 3.12 测试通过，并用真实的 Codex CLI 0.163.0 做过一次冒烟测试。在 Windows 11 上用 Python 3.12 测试通过，并用真实的 Codex CLI 0.164.0（`codex.exe`）做过一次冒烟测试。

Windows 上使用 `codex.cmd` 时，路径和其他参数不能包含 `%` 或 `!`。如需使用这些字符，请将 `codex_bin` 设置为 `codex.exe` 或 node 入口。wrapper 用 `cmd.exe /d` 启动 `.cmd`，跳过 cmd.exe 的 AutoRun（例如 conda/mamba 的初始化脚本），否则 AutoRun 可能把传给 Codex 的任务内容读空。

---

## English

`codex-worker` lets a Claude Code orchestrator hand one bounded task to the local OpenAI Codex CLI. Each run gets an audit folder. It is a cross-platform Python tool (3.9+, standard library only).

**Install (macOS, Linux or Windows)**

1. Install Python, Git and Node.js.
2. Run `npm install -g @openai/codex`, then `codex login`.
3. Run `python install.py --check`, then `python install.py`. This writes the `codex-worker` agent to `~/.claude/agents/`; use `--project <repo>` to install it for one repository only.
4. Restart Claude Code.

**Policy and defaults**

- **Models**: only those in `allowed_models`; the first one is the default.
- **Fast**: off by default. `--fast` turns it on; `allow_fast: false` disables it entirely.
- **Sandbox and approvals**: `workspace-write` sandbox. By default (`--approvals auto`) Codex asks before anything the sandbox blocks (network, writes outside the workspace, blocked commands) and Codex Auto-review approves or denies each request (`approval_policy=on-request`, `approvals_reviewer=auto_review`). In this mode the wrapper adds a one-paragraph note telling Codex to request escalation instead of giving up, and not to work around denials. It is not a deterministic security guarantee. `--approvals never` restores the old behaviour (blocked actions fail). `--network` pre-approves network access.
- **Continue**: `--continue` resumes the last session recorded for the same `--name` in this repository (`<runs_dir>/sessions.json`); `--resume <id>` resumes a given session.
- **Browser**: `--browser` lets Codex drive your own browser through the Codex Chrome extension, using the community workaround from [openai/codex#57641](https://github.com/openai/codex/issues/57641) (`setupBrowserRuntime()` from the plugin's `browser-client.mjs` inside the `node_repl` tool), because `cua_repl` reports "The browser access host is unavailable" outside the desktop app. Unsupported; it bypasses the desktop app's authorization and may break with any update. The preflight requires one dedicated tab, `BROWSER_PREFLIGHT: OK|FAIL`, and forbids credentials, CAPTCHAs, purchases, new terms and touching plugin files. `meta.json` records `browser_evidence`. `allow_browser: false` turns it off. Not yet verified on Windows.
- **Context**: Codex starts fresh each run. Put style rules and example file paths in `AGENTS.md` (repository or `~/.codex/AGENTS.md`), use `--continue` for follow-ups, and optionally enable Codex memories. Both AGENTS.md and `--continue` add input tokens each turn; most hit the cache.
- **Auth**: `--auth login` (default) uses the stored `codex login`. `--auth api` bills this run to an API key read from `OPENAI_API_KEY` (rename with `api_key_env`); it is passed to Codex as `CODEX_API_KEY`, never written to disk, and the stored login is left alone. `meta.json` records the auth mode and token usage.
- **Changed files**: before and after each run the wrapper runs `git status` and compares only size and modification time of dirty/untracked files (no content reads, nothing sent to Codex). `exclude_from_changes` skips path prefixes; `track_changes: false` turns the scan off.
- **Concurrency**: limited per machine by `max_concurrent`. Slots left by dead processes are reclaimed.
- **Exit codes**: the wrapper returns Codex's exit code. `2` means an argument or policy error; `75` means all slots are busy (add `--wait <seconds>`).

**Configuration**

Settings are layered: built-in defaults, then `~/.codex-worker/config.json`, then `<repo>/.codex-worker.json`. The later file wins.

**Tests**

`python -m unittest discover -s tests -v` uses a fake Codex, so it needs no network and no quota. The tests passed on macOS with Python 3.9 and 3.12, and one real Codex CLI 0.163.0 smoke run succeeded. They also passed on Windows 11 with Python 3.12, with one real Codex CLI 0.164.0 (`codex.exe`) smoke run. On Windows, `.cmd` launchers are started with `cmd.exe /d` so a cmd.exe AutoRun hook (such as conda/mamba init) cannot swallow the prompt on stdin.
