[English](../content/fork-diff.md) · [한국어](fork-diff.ko.md) · [日本語](fork-diff.ja.md) · 简体中文 · [Русский](fork-diff.ru.md)

# Fork 差异

原版 LlamaForge 加上以下改动。其余全部保持上游原样。仅在 Ubuntu Server 上测试过。

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

## 安装与更新

README 中的一键安装命令安装的是原项目。本构建从本仓库部署。应用内更新器拒绝没有
安装清单（`.lf-files.json`）的副本；此类副本需手动更新。
