[English](../content/fork-diff.md) · [한국어](fork-diff.ko.md) · 日本語 · [简体中文](fork-diff.zh-CN.md) · [Русский](fork-diff.ru.md)

# フォークの差分

オリジナルの LlamaForge に以下の変更を加えたもの。「このビルドで削除されたもの」に挙げた機能は除く。テストは Ubuntu Server のみ。

## LAN 露出(オプトイン)

`config.json` の `panel_host`、`router_allow_keyless_lan` は、ダッシュボード、ルーターを LAN 上に移す。既定は変わらない: ループバック待受のままで、ルーターはキーなしではフェイルクローズする。Host/Origin ガードは有効のままに加えて、マシン自身の名前も受け付ける。詳細: [設定](../content/config.md)、[セキュリティ](https://github.com/dadwritestech/LlamaForge/blob/master/SECURITY.md)。

## HTTP 経由の MCP(オプトイン)

`mcp_host` / `mcp_port`(既定 `8092`)が、stdio に並ぶステートレス Streamable HTTP で MCP サーバーを提供する。既定では無効。詳細: [MCP サーバー](../content/mcp.md)。

## ベンダーツールなしの GPU テレメトリ

`nvidia-smi` がない場合、GPU 状態(VRAM 使用/合計、使用率、温度)はカーネルの DRM sysfs から読む。デバイス名とトークンは `llama-server --list-devices` に由来するため、マルチモデルプランナーは `CUDA0` だけでなく `Vulkan0` も扱える。

## モデルゲートウェイ(LiteLLM)

**Gateway** タブが [LiteLLM](https://github.com/BerriAI/litellm)(MIT)を管理サイドカーとして動かす。仮想モデル名 1つが複数の実モデルにリクエストを
交互に振り分ける(`gateway_models`)。調整は自由形式の LiteLLM プリセット
(`gateway_presets`)。仮想名ごとの動作は 2種類: 交互(リクエストが厳密に順番に回る
— LlamaForge が LiteLLM に入れる round-robin)か primary + reserve(最初のモデルに
空きスロットがある間そこに留まり、満杯なら予備にこぼれる)。`router_allow_keyless_lan`
ではゲートウェイもキーなしで応答する。既定ではオフ。専用ポート(`gateway_port`、既定 8300)、
専用 venv(`tools/litellm/venv`)、パネルの API と MCP ツールで管理する。
詳細: [モデルゲートウェイ](../content/gateway.md)。

## Engines(プリビルトビルド)

Build / Update タブは **Engines** になった: llama.cpp 系エンジン 3つ — llama.cpp(ggml-org)、
ik_llama.cpp(Thireus ビルド)、beellama.cpp(Anbeeld) — のビルドを取得して選び、ルーターが動かすものを切り替える。
起動プロファイルはエンジンファミリーごとに分かれる: ピン留めのないプロファイルは
他のエンジンでの起動を拒否し、対象エンジンが知らないプリセットキーは破棄されて
起動レポートに列挙される。ソースからのビルドと vLLM バックエンドは削除された。詳細: [Engines](../content/build.md)。

## モデルベンチマーク(Bench)

**Bench** タブは [llama-bench](https://github.com/ggml-org/llama.cpp) を batch-size /
ubatch-size のグリッドで実行し、モデルが必要とするサイズを選びます。モデルの
オフロードキー、キャッシュ型、デバイス一覧は対応する llama-bench フラグになり
(モデルが列挙する全 GPU にわたる 1 回の実行)、数字は実際の実行を表します。
ワークロードはプロンプト、生成、複合テストをカバーします。ベンチは一度にひとつ
だけ動き、モデルの読み込みはベンチ自身が行うため、通常の llama.cpp にモデルが
読み込まれている間は開始を拒否します。完了した実行は `logs/bench/history.jsonl`
に生の llama-bench 出力とともに記録されます。各行は測定に使ったデバイスを示し、
各セルは同じモデルの前回実行からの変化を示し、最適なペアが推奨されます
(合計スループット最高。3% 以内の差ではより小さい micro-batch が勝ち、次に
より小さい batch)。ワンクリックでモデルの `models.ini` セクションに書き込まれます。
MCP でも `bench_run`、`bench_status`、`bench_stop`、`bench_apply`、`bench_history`
として同じことができます。

## UI のローカライズ

Web UI は英語・ロシア語・日本語・韓国語・簡体字中国語に対応する。言語はエンジン
バッジの横のトップバーで選び、ブラウザーに記憶される(localStorage `lf_lang`)。
`config.json` の `web_lang` がパネルの既定値。翻訳のない文字列は英語で表示される。

## このビルドで削除されたもの

Chat、Voice、Embers(ミニエージェント)、Context ウィキ、Help ビューア、
トップバーの時計、クレジット/スターのバナーは取り除かれた。vLLM バックエンドは
完全に削除された: `backend/vllm_*` モジュール、それを動かす WSL2 ヘルパー、
ドキュメントのページとテストも削除された。

## インストールと更新

README のワンラインインストーラはオリジナルのプロジェクトをインストールする。このビルドはこのリポジトリからデプロイする。アプリ内アップデータは、インストールマニフェスト(`.lf-files.json`)のないコピーを拒否する。そのようなコピーは手動で更新する。
