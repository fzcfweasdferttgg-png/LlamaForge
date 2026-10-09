[English](../content/fork-diff.md) · [한국어](fork-diff.ko.md) · 日本語 · [简体中文](fork-diff.zh-CN.md) · [Русский](fork-diff.ru.md)

# フォークの差分

オリジナルの LlamaForge に以下の変更を加えたもの。それ以外は upstream そのまま。テストは Ubuntu Server のみ。

## LAN 露出(オプトイン)

`config.json` の `panel_host`、`chat_host`、`router_allow_keyless_lan` は、ダッシュボード、チャットリスナー、ルーターを LAN 上に移す。既定は変わらない: ループバック待受のままで、ルーターはキーなしではフェイルクローズする。Host/Origin ガードは有効のままに加えて、マシン自身の名前も受け付ける。詳細: [設定](../content/config.md)、[セキュリティ](https://github.com/dadwritestech/LlamaForge/blob/master/SECURITY.md)。

## HTTP 経由の MCP(オプトイン)

`mcp_host` / `mcp_port`(既定 `8092`)が、stdio に並ぶステートレス Streamable HTTP で MCP サーバーを提供する。既定では無効。詳細: [MCP サーバー](../content/mcp.md)。

## ベンダーツールなしの GPU テレメトリ

`nvidia-smi` がない場合、GPU 状態(VRAM 使用/合計、使用率、温度)はカーネルの DRM sysfs から読む。デバイス名とトークンは `llama-server --list-devices` に由来するため、マルチモデルプランナーは `CUDA0` だけでなく `Vulkan0` も扱える。

## インストールと更新

README のワンラインインストーラはオリジナルのプロジェクトをインストールする。このビルドはこのリポジトリからデプロイする。アプリ内アップデータは、インストールマニフェスト(`.lf-files.json`)のないコピーを拒否する。そのようなコピーは手動で更新する。
