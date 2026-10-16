[English](../content/fork-diff.md) · [한국어](fork-diff.ko.md) · [日本語](fork-diff.ja.md) · 简体中文 · [Русский](fork-diff.ru.md)

# Fork 差异

原版 LlamaForge 加上以下改动，减去「本构建中移除」一节所列功能。仅在 Ubuntu Server 上测试过。

## 局域网暴露（opt-in）

`config.json` 中的 `panel_host` 和 `router_allow_keyless_lan` 把
面板和路由器移到局域网。默认值不变：绑定回环地址，
且路由器在无密钥时仍然 fail closed。
Host/Origin 校验保持开启，并额外接受机器自身的名称。详情：[配置](../content/config.md)、
[安全](https://github.com/dadwritestech/LlamaForge/blob/master/SECURITY.md)。

## MCP over HTTP（opt-in）

`mcp_host` / `mcp_port`（默认 `8092`）在 stdio 旁边通过无状态
Streamable HTTP 提供 MCP server。默认关闭。详情：[MCP Server](../content/mcp.md)。

## 无需厂商工具的 GPU 遥测

缺少 `nvidia-smi` 时，GPU 状态（显存 已用/总量、利用率、
温度）从内核的 DRM sysfs 读取。设备名称和标识来自
`llama-server --list-devices`，因此多模型规划器说的是 `Vulkan0`
而不只是 `CUDA0`。

## 模型网关（LiteLLM）

**Gateway** 标签页把 [LiteLLM](https://github.com/BerriAI/litellm)（MIT）作为受管
边车运行：一个虚拟模型名在多个真实模型间轮换请求（`gateway_models`），用自由格式的
LiteLLM 预设（`gateway_presets`）调校。每个虚拟名有两种工作方式：轮换（请求严格按序
轮转各模型——LlamaForge 装入 LiteLLM 的 round-robin）或主备（第一个模型有空闲槽位时
流量留在它上面，满了才溢出到备用模型）。设置 `router_allow_keyless_lan` 时网关同样
免密钥响应。默认关闭；独立端口（`gateway_port`，默认 8300）、
独立 venv（`tools/litellm/venv`），通过面板 API 和 MCP 工具管理。
详情：[模型网关](../content/gateway.md)。

## Engines（预构建）

Build / Update 标签页现在是 **Engines**：下载并选择三个 llama.cpp 系列引擎 — llama.cpp（ggml-org）、
ik_llama.cpp（Thireus 构建）和 beellama.cpp（Anbeeld）— 的构建，并切换路由器运行哪一个。
启动配置文件按引擎家族分开：未固定构建的配置文件拒绝在其他引擎上启动，目标引擎不认识的
预设键会被丢弃并列在启动报告中。从源码构建和 vLLM 后端已移除。详情：[Engines](../content/build.md)。

## 界面本地化

Web UI 支持英语、俄语、日语、韩语和简体中文。语言在引擎徽章旁边的顶栏选择，
按浏览器记住（localStorage `lf_lang`）；`config.json` 的 `web_lang` 设置面板默认值。
缺少翻译的字符串回退到英文。

## 本构建中移除

Chat、Voice、Embers（迷你代理）、Context wiki、Help 查看器、顶栏时钟和
credit/star 横幅已删除。vLLM 后端已完整移除：`backend/vllm_*` 模块、
驱动它的 WSL2 辅助模块、其文档页和测试全部删除。

## 安装与更新

README 中的一键安装命令安装的是原项目。本构建从本仓库部署。应用内更新器拒绝没有
安装清单（`.lf-files.json`）的副本；此类副本需手动更新。
