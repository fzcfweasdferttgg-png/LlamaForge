<p align="center"><b>真正的 llama.cpp 服务器，在浏览器标签页中运行。</b><br>
选一个模型，看它是否塞得进你的显存，加载，聊天。需要时，每个 llama-server 参数依然都在。</p>


<h2 align="center">
  <a href="../../README.md">English</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.ja.md">日本語</a> ·
  简体中文 ·
  <a href="README.ru.md">Русский</a>
</h2>

> [!IMPORTANT]
> [dadwritestech/LlamaForge](https://github.com/dadwritestech/LlamaForge) 的 Fork 构建 — 仅在 Ubuntu Server 上测试过。与原版的差异：[Fork 差异](fork-diff.zh-CN.md)。

```powershell
irm https://raw.githubusercontent.com/dadwritestech/LlamaForge/master/install.ps1 | iex   # Windows, no admin
```
```bash
curl -fsSL https://raw.githubusercontent.com/dadwritestech/LlamaForge/master/install.sh | sh   # Linux / macOS
```
这些命令安装的是原项目；本构建从本仓库部署。然后 **Install llama.cpp**（适配你 GPU 的官方构建，无需编译器）→ **Discover** → **Load**。
早期预览：Windows + NVIDIA 是测试最充分的路径。Linux 和 macOS 通过 CI，但真机使用很少。

LlamaForge 本身不运行任何模型。它安装并驱动 llama.cpp 自带的 `llama-server` 路由，并代你编辑其 `models.ini`。
与 ggml-org 无关。如果你要的是打磨度而非可控性，请用 [LM Studio](https://lmstudio.ai)、[Ollama](https://ollama.com) 或 [Jan](https://jan.ai)。

## 为什么选 LlamaForge

新的模型架构首先落地 llama.cpp。桌面应用要等下次更新内置引擎才跟上。
LlamaForge 直接运行**官方 llama.cpp 发布版本身**（或你自己的构建 / fork），
新模型只差一次 **Update** 点击；它给每个服务器参数都配了 UI，而不是只挑一个子集。

| | **LlamaForge** | LM Studio | Ollama |
|---|---|---|---|
| 开源 | ✅ MIT | ❌ 闭源应用 | ✅ MIT |
| 引擎 | 官方上游 llama.cpp 构建，任意版本，或你自己的 fork | LM Studio 内置的 llama.cpp / MLX 运行时 | Ollama 自有引擎，基于 ggml |
| 每模型设置 | `llama-server --help` 列出的每个参数（当前构建 200+） | 很多，精选 | Modelfile 参数 |
| Hugging Face 上任意 GGUF，下载前按你的显存评级 | ✅（粗略估计） | ✅ | 拉取 GGUF，无适配评级 |
| OpenAI + Anthropic 兼容 API | ✅，另有一键 Claude Code / Codex / pi.dev 配置 | ✅ | ✅ |
| 后端依赖 | 无（Python 标准库） | – | – |
| 原生桌面应用 | ❌ 在浏览器中运行 | ✅ | ✅ |
| 成熟度 | **早期预览** | 成熟 | 成熟 |

<sub>截至 2026 年 10 月，据我们所知。发现有误？欢迎提 PR 修正此表。</sub>

## 内含什么

- **Models**：机器上所有模型汇成一个列表，附每个 GPU 的实时显存/占用率/温度。可在行内加载、卸载或调参。
  - 展开模型可编辑每个 llama-server 参数，分组且可搜索，旁边是 GGUF 元数据卡片（架构、量化、训练上下文、层数）。
  - 保存会就地重新加载模型。加载失败时显示路由器日志中的最后一条错误，并给出推测提示。
  - 预设、启动配置（模型 + 预设 + 固定的 llama.cpp 构建，一键完成）、并排对比，以及可复制粘贴的客户端片段。
  - 在 Setup 中开启 **Multi-model** 可同时保持一个主模型和若干 worker 加载。规划器按在你机器上实测的占用量，把每个模型放到放得下的 GPU 上。模型也可固定到各自的 llama.cpp（或 ik_llama.cpp）构建。
- **Discover**：Hugging Face GGUF 搜索，打开即见本周新增。下载前每个量化版本都给出针对你显存的粗略适配评级（FITS / TIGHT / CPU OFFLOAD）。下载可断点续传、自动注册，最后进入 **Load** 就绪状态。
- **Will it run?**：选一个仓库和量化版本，得到适配结果和粗略速度估计。
- **Build / Update**：一键官方 llama.cpp 构建并可回滚，或从源码构建（参数按你的 GPU 检测）。也驱动 [ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp) 以及 Windows 上 WSL2 里的 [vLLM](https://github.com/vllm-project/vllm)。
- **Stats**：来自路由器自身指标的每模型 token 数、速度和运行次数。无法做每客户端统计，因为客户端直连路由器。
- **模型网关**：多个真实模型之上的一个虚拟模型名 — 请求按 LiteLLM 预设轮流分发。[详见](../content/gateway.md)。
- **Recipes**：把配置以可读 JSON 分享；他人一次粘贴即可导入，缺模型时 LlamaForge 会下载。设有[社区画廊](../../recipes/)。

首次运行向导和 **Lite / Advanced** 开关让深层旋钮在你需要之前不碍事。默认外观 **Stowage** 把每个 GPU 画成按 1 GiB 格线划分的舱位图；**Hearth** 和 **Classic** 一键可换，每种都有浅色、深色和色盲友好三种配色。

## 从其他应用使用

任何支持 OpenAI API 的应用（Open WebUI、SillyTavern、Continue、Cline、Aider、OpenAI SDK）都能对接
`http://127.0.0.1:8080/v1`。路由器始终带 API 密钥运行，任意模型的 **Client Config** 会给出
可直接粘贴的 base URL、密钥和 model id。

- **Anthropic 兼容**：面板上的 `POST /v1/messages`，支持流式和工具使用。
- **Connect an agent** 为 **Claude Code**、**Codex** 和 **pi.dev** 写入配置（动过的文件都会先备份）。
- 加载/卸载端点让 agent 按需换模型。
- **MCP server**（stdio，`backend/mcp_server.py`；本构建还附带可选的 Streamable
  HTTP 传输，见文档）：Claude Code、Codex 或任何 MCP 客户端可以查看已加载的内容、
  加载和卸载模型、检查什么放得下、从 Hugging Face 拉取 GGUF，并把整个任务交给
  在已加载的本地模型上运行的 [pi](https://github.com/earendil-works/pi)（`pi_run`）。**Setup -> MCP server** 下一行命令即可配置，例如
  `claude mcp add --scope user llamaforge -- python <LlamaForge>/backend/mcp_server.py`。

## 安装

上面的一键命令安装的是原项目；本构建从本仓库部署。见 [Fork 差异](fork-diff.zh-CN.md)。

安装器会查找 Python 3.10+（Windows 上若没有，会放置一份私有的、SHA-256 固定的
python.org 嵌入式 Python 副本），下载最新发布版，添加开始菜单 / 应用菜单项
（macOS 上是 `~/Applications/LlamaForge.app`，Linux/macOS 上另加 `llamaforge`
命令）并打开面板。更新会保留你的设置、模型和引擎。Windows 上从 **Apps & Features** 卸载，
或运行 `llamaforge uninstall`；动你的设置或模型之前会先询问。

<details><summary>从源码安装（git clone）</summary>

```powershell
git clone https://github.com/dadwritestech/LlamaForge
cd LlamaForge
powershell -ExecutionPolicy Bypass -File bootstrap.ps1   # Windows
./bootstrap.sh                                           # Linux / macOS
```

bootstrap 脚本会检查 Python 和 Git（安装任何东西之前先询问），
写入 `config.json` 并打开面板。
</details>

**日常使用：**从开始菜单 / 应用菜单打开 **LlamaForge**，或运行 `llamaforge`。
它会启动路由器和面板并打开浏览器。

- 面板：http://127.0.0.1:8090
- 给你其他应用的 API：http://127.0.0.1:8080/v1

`llamaforge stop`（或 `stop.ps1` / `stop.sh`）关闭面板、路由器以及它衍生的
模型进程。它只停止 LlamaForge 启动的进程；你运行的其他
llama-server 不受影响。

**系统要求：**Windows 10/11、Linux 或 Apple Silicon 上的 macOS。Python 3.10+（仅标准库；
Windows 安装器自带）。有 NVIDIA (CUDA)、AMD/Intel (Vulkan) 或 Apple (Metal) GPU 时会用上；
纯 CPU 也能跑。从源码构建还需要 Git、CMake、Ninja 和
C++ 编译器，在包管理器允许的情况下 Setup 标签页可以安装它们。

## 工作原理

LlamaForge 不含任何 llama.cpp 代码。纯标准库的 Python 后端驱动 llama.cpp 自带的
路由器 API，编辑 `models.ini`，并获取官方构建（或调用 `git` / `cmake`）。
旋钮列表从 `llama-server --help` 实时解析，因此跟随你所运行的构建。

LlamaForge 不固定上下文大小、GPU 层数或多 GPU 切分。llama.cpp 的 `--fit`
（默认开启）在加载时按你的空闲显存确定这三者，并在需要时把 MoE 专家移到 CPU。
固定其中任何一项都会关掉 fit，所以在你自己设置之前它们保持未设置。

**安全：**除非 `panel_host`（本构建）把它共享到局域网，面板只监听 `127.0.0.1`。
路由器即使在本地也带密钥，你访问的网页驱动不了它；局域网
访问需从 Setup 选择加入且需要密钥（本构建的 `router_allow_keyless_lan` 可以
退出这一要求）。Host/Origin 校验在每种模式下都保持开启。详情及如何私下报告
漏洞：[SECURITY.md](https://github.com/dadwritestech/LlamaForge/blob/master/SECURITY.md)。

## 文档

一切都在 **[dadwritestech.github.io/LlamaForge](https://dadwritestech.github.io/LlamaForge/)**：
[配置](../content/config.md)、[键盘快捷键](../content/keymap.md),
[主题与色盲友好模式](../content/theming.md)、[vLLM](../content/vllm.md)、
[故障排除](../content/troubleshooting.md)、[更新内容](../content/whats-new.md)。
[ROADMAP.md](../../ROADMAP.md) 有已发布和计划中的内容；这是早期预览，优先级跟随反馈。

## 致谢与许可

LlamaForge 采用 MIT 许可（[LICENSE](../../LICENSE)）。它构建并驱动
**[llama.cpp](https://github.com/ggml-org/llama.cpp)** - MIT, (c) The ggml authors -
见 [NOTICE](../../NOTICE) 和 [LICENSE.llama.cpp.txt](../../LICENSE.llama.cpp.txt)。
难的部分是他们的；请给上游项目点星并支持。

`pi_run` 驱动 **[pi](https://github.com/earendil-works/pi)**，Mario Zechner 的开源编码 agent（MIT）。
LlamaForge 不附带它：**Setup → Install pi** 用你的 Node.js 把已发布的 npm 包取到
LlamaForge 自己的 `agents/` 目录（或自行用
`npm install -g @earendil-works/pi-coding-agent` 安装）。
