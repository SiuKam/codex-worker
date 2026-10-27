# codex-worker

让 Claude Code 把一个边界清楚的任务交给本机的 OpenAI Codex CLI 去做，并为每次运行留下完整记录。macOS、Linux 和 Windows 都能用，只需要 Python 3.9 及以上（只用标准库）。

## 组成

| 文件 | 作用 |
|---|---|
| `codex_task.py` | wrapper：限制模型和推理强度，控制并发，组装 `codex exec` 命令，记录每次运行 |
| `preamble.md` | 每个任务前面自动加上的固定说明（可在配置里换成项目自己的） |
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
                     [--model <id>] [--network] [--fast | --no-fast]
                     [--resume <session_id>] [--wait <秒>] [--root <仓库>]
                     [--with <额外前言名>] [--prompt-file <文件>]     # 也可以从标准输入传入任务
```

- **模型**：只接受配置里 `allowed_models` 列出的模型，第一个是默认值。
- **Fast**：Fast 就是 Codex 的 `priority` 服务档。默认关闭，`--fast` 开启。在配置里设 `allow_fast: false` 后，`--fast` 一律无效。
- **沙箱**：沙箱为 `workspace-write`，审批为 `never`；不加 `--network` 时不联网。
- **继续任务**：`--resume <session_id>` 继续之前的会话，session id 在上次运行的输出和 `meta.json` 里。
- **退出码**：退出码与 Codex 的相同。`2` 表示参数或规则错误；`75` 表示并发名额已满，可以加 `--wait 600` 排队等待。

## 配置

依次读取三层设置，后读到的覆盖先读到的：内置默认值、`~/.codex-worker/config.json`（本机通用）、`<仓库>/.codex-worker.json`（单个项目）。可用的键：

```json
{
  "allowed_models": ["gpt-6.1-sol", "gpt-6-astra"],
  "max_concurrent": 8,
  "fast_default": false,
  "allow_fast": true,
  "codex_bin": "",
  "runs_dir": ".codex_runs",
  "preamble": "",
  "extra_preambles": {"browser": "tools/codex/browser_preflight.md"},
  "lock_dir": "",
  "require_git": true,
  "exclude_from_changes": []
}
```

- **`max_concurrent`**：限制整台电脑同时跑的数量。名额是 `~/.codex-worker/locks/` 下的文件夹，进程意外退出后留下的名额会自动回收。
- **`runs_dir`**：每次运行的记录放在 `<runs_dir>/<UTC时间>_<name>/`，里面有：
  - `prompt.md`
  - `command.txt`
  - `events.jsonl`
  - `last_message.md`
  - `stderr.log`
  - `meta.json`：模型、推理强度、Fast、session id、退出码、运行前后的 git HEAD，以及运行期间改动的文件
- **`codex_bin`**：留空时自动查找 Codex：先查 `PATH`，再查 Windows 的 `%APPDATA%\npm\codex.cmd`、macOS 的 `~/.local/bin/codex` 和 Homebrew 路径。也可以用环境变量 `CODEX_BIN` 指定。
- **`preamble` / `extra_preambles`**：相对路径都按仓库根目录解析。`--with browser` 会在任务前再加上对应的说明文件。

`events.jsonl` 可能含有文件片段，受版权保护的内容不要提交到 git。

## 测试

```
python -m unittest discover -s tests -v
```

测试内容：正常运行、模型限制、Fast 开关、继续会话、退出码透传、并发名额与回收、空任务。已在 macOS 上用 Python 3.9 和 3.12 测试通过，并用真实的 Codex CLI 0.163.0 做过一次冒烟测试。在 Windows 11 上用 Python 3.12 测试通过，并用真实的 Codex CLI 0.164.0（`codex.exe`）做过一次冒烟测试。

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
- **Sandbox**: `workspace-write` sandbox, approval `never`. Network is off unless `--network` is given.
- **Concurrency**: limited per machine by `max_concurrent`. Slots left by dead processes are reclaimed.
- **Exit codes**: the wrapper returns Codex's exit code. `2` means an argument or policy error; `75` means all slots are busy (add `--wait <seconds>`).

**Configuration**

Settings are layered: built-in defaults, then `~/.codex-worker/config.json`, then `<repo>/.codex-worker.json`. The later file wins.

**Tests**

`python -m unittest discover -s tests -v` uses a fake Codex, so it needs no network and no quota. The tests passed on macOS with Python 3.9 and 3.12, and one real Codex CLI 0.163.0 smoke run succeeded. They also passed on Windows 11 with Python 3.12, with one real Codex CLI 0.164.0 (`codex.exe`) smoke run. On Windows, `.cmd` launchers are started with `cmd.exe /d` so a cmd.exe AutoRun hook (such as conda/mamba init) cannot swallow the prompt on stdin.
