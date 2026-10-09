<p align="center"><b>本物の llama.cpp サーバーを、ブラウザのタブから。</b><br>
モデルを選び、GPU に収まるかを確認して、読み込んで、チャット。llama-server のフラグはすべて残っており、望めばいつでも使える。</p>


<h2 align="center">
  <a href="../../README.md">English</a> ·
  <a href="README.ko.md">한국어</a> ·
  日本語 ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.ru.md">Русский</a>
</h2>

> [!IMPORTANT]
> [dadwritestech/LlamaForge](https://github.com/dadwritestech/LlamaForge) のフォークビルド — テストは Ubuntu Server のみ。オリジナルとの差分: [フォークの差分](fork-diff.ja.md)。

```powershell
irm https://raw.githubusercontent.com/dadwritestech/LlamaForge/master/install.ps1 | iex   # Windows, no admin
```
```bash
curl -fsSL https://raw.githubusercontent.com/dadwritestech/LlamaForge/master/install.sh | sh   # Linux / macOS
```
これらはオリジナルのプロジェクトをインストールする。このビルドはこのリポジトリからデプロイする。その後は **Install llama.cpp**(GPU 向けの公式ビルド、コンパイラ不要)→ **Discover** → **Load**。
アーリープレビュー: Windows + NVIDIA が最もテストされている経路。Linux と macOS は CI は通るが、実機での使用は少ない。

LlamaForge はモデル自体を実行しない。llama.cpp 純正の `llama-server` ルーターをインストールして操作し、`models.ini` を代わりに編集する。ggml-org とは無関係。制御より洗練を優先するなら [LM Studio](https://lmstudio.ai)、[Ollama](https://ollama.com)、[Jan](https://jan.ai) を使うこと。

## なぜ LlamaForge か

新しいモデルアーキテクチャはまず llama.cpp に登場する。デスクトップアプリがそれを受け取るのは、同梱エンジンを次に更新するときだ。LlamaForge は **公式 llama.cpp リリースそのもの**(または自分のビルドやフォーク)を動かすため、新しいモデルは **Update** 1クリック先にあり、厳選された一部ではなくすべてのサーバーフラグに UI を与える。

| | **LlamaForge** | LM Studio | Ollama |
|---|---|---|---|
| オープンソース | ✅ MIT | ❌ プロプライエタリアプリ | ✅ MIT |
| エンジン | 公式 upstream llama.cpp ビルド、任意のバージョン、自分のフォークも可 | LM Studio 同梱の llama.cpp / MLX ランタイム | ggml 上の Ollama 独自エンジン |
| モデルごとの設定 | `llama-server --help` に列挙されるすべてのフラグ(現行ビルドで 200 以上) | 多数、厳選 | Modelfile パラメータ |
| Hugging Face の任意の GGUF、ダウンロード前に VRAM への適合を評定 | ✅(おおまかな推定) | ✅ | GGUF を取得、適合評定なし |
| OpenAI + Anthropic 互換 API | ✅、さらに Claude Code / Codex / pi.dev のワンクリック設定 | ✅ | ✅ |
| バックエンド依存関係 | なし(Python stdlib) | – | – |
| ネイティブデスクトップアプリ | ❌ ブラウザ内で実行 | ✅ | ✅ |
| 成熟度 | **アーリープレビュー** | 成熟 | 成熟 |

<sub>2026 年 10 月時点、当方の知る限り。誤りがあれば、この表の修正 PR を歓迎する。</sub>

## 何ができるか

- **Models**: 機上のすべてのモデルを 1 つのリストにまとめ、GPU ごとの VRAM・使用率・温度をライブ表示。行から読み込み、アンロード、調整ができる。
  - モデルを展開すると llama-server のすべてのフラグを編集できる。グループ化・検索可能で、GGUF メタデータカード(アーキテクチャ、quant、学習時コンテキスト、レイヤー)の隣に並ぶ。
  - 保存するとモデルをその場で再読み込みする。読み込み失敗時はルーターログの直近エラーと推測のヒントを表示する。
  - プリセット、起動プロファイル(モデル + プリセット + 固定した llama.cpp ビルドを 1 クリック)、並列比較、コピー&ペースト用クライアントスニペット。
  - Setup で **Multi-model** を有効にすると、メインモデルとワーカーを同時に読み込んだままにできる。プランナーが自機で測定したフットプリントを使い、収まる GPU に各モデルを配置する。モデルごとに専用の llama.cpp(または ik_llama.cpp)ビルドを固定することもできる。
- **Embers**: トピックを見守り、独自の wiki を管理し、スケジュールに沿ってブリーフを書く小さなローカルエージェント。wiki の項目は、出典を逐語的に引用して初めて成立する。**Forge** は対話しながら 1 つを作り、**Model Scout** は設定不要。Embers にブラウザやツールはなく、プッシュ通知を有効にしない限り何もマシン外に出ない。
- **Discover**: 今週の新着から開く Hugging Face の GGUF 検索。ダウンロード前に各 quant へ VRAM への適合のおおまかな評定(FITS / TIGHT / CPU OFFLOAD)。ダウンロードは中断後に再開し、自動登録され、**Load** 準備で終わる。
- **Will it run?**: リポジトリと quant を選ぶと、適合と速度の概算が出る。
- **Build / Update**: ロールバック付きの公式 llama.cpp ビルドをワンクリック、または GPU 向けフラグを検出してソースからビルド。[ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp) も、Windows では WSL2 上の [vLLM](https://github.com/vllm-project/vllm) も操作する。
- **Stats**: ルーター自身のメトリクスから、モデルごとのトークン数、速度、実行回数。クライアントはルーターと直接通信するため、クライアント別の統計は不可能。
- **Context**: Markdown のコンテキスト文書をプロファイルに合成し、リクエストへ注入するか `CLAUDE.md` / `AGENTS.md` に書き込む。
- **Recipes**: プロファイルを可読な JSON で共有。受け取った側は 1 回のペーストでインポートでき、モデルがなければ LlamaForge がダウンロードする。[コミュニティギャラリー](../../recipes/)がある。

初回ウィザードと **Lite / Advanced** トグルで、細かい設定項目は必要なときまで視界から外しておく。既定の外観 **Stowage** は各 GPU を 1 GiB 目盛りのベイ平面図として描く。**Hearth** と **Classic** は 1 クリック先にあり、それぞれライト、ダーク、色覚安全を持つ。

## 他のアプリから使う

OpenAI API に対応するもの(Open WebUI、SillyTavern、Continue、Cline、Aider、OpenAI SDK)はすべて `http://127.0.0.1:8080/v1` に対して使える。ルーターは常に API キー付きで動き、モデルの **Client Config** がベース URL、キー、モデル id を貼り付け可能な形で出す。

- **Anthropic 互換** `POST /v1/messages` をパネルに提供。ストリーミングとツール使用に対応。
- **Connect an agent** が **Claude Code**、**Codex**、**pi.dev** の設定を書き込む(触れるファイルはすべて事前にバックアップされる)。
- Load/unload エンドポイントで、エージェントが必要に応じてモデルを差し替えられる。
- **MCP server**(stdio、`backend/mcp_server.py`。このビルドではオプトインの Streamable HTTP トランスポートも同梱、ドキュメント参照): Claude Code、Codex、任意の MCP クライアントが、読み込み済みの確認、モデルの読み込み・アンロード、適合確認、Hugging Face からの GGUF 取得、タスク全体をローカルの読み込み済みモデル上で動く [pi](https://github.com/earendil-works/pi) への引き渡し(`pi_run`)ができる。**Setup -> MCP server** で 1 行のセットアップ、例: `claude mcp add --scope user llamaforge -- python <LlamaForge>/backend/mcp_server.py`。

## インストール

上のワンライナーはオリジナルのプロジェクトをインストールする。このビルドはこのリポジトリからデプロイする。[フォークの差分](fork-diff.ja.md) を参照。

インストーラは Python 3.10+ を探す(Windows では見つからない場合、python.org の埋め込み Python を SHA-256 固定のプライベートコピーとして置く)。最新リリースをダウンロードし、スタートメニュー/アプリメニューのエントリ(macOS では `~/Applications/LlamaForge.app`、Linux/macOS では `llamaforge` コマンドも)を追加してダッシュボードを開く。更新しても設定、モデル、エンジンは保たれる。アンインストールは Windows の **Apps & Features** または `llamaforge uninstall`。設定やモデルに触れる前に確認する。

<details><summary>ソースから(git clone)</summary>

```powershell
git clone https://github.com/dadwritestech/LlamaForge
cd LlamaForge
powershell -ExecutionPolicy Bypass -File bootstrap.ps1   # Windows
./bootstrap.sh                                           # Linux / macOS
```

bootstrap スクリプトは Python と Git を確認し(何かをインストールする前に確認する)、`config.json` を書き出してダッシュボードを開く。
</details>

**日常の使い方:** スタートメニュー/アプリメニューから **LlamaForge** を開くか、`llamaforge` を実行する。ルーターとダッシュボードを起動し、ブラウザを開く。

- ダッシュボード: http://127.0.0.1:8090
- 他のアプリ用 API: http://127.0.0.1:8080/v1

`llamaforge stop`(`stop.ps1` / `stop.sh` も可)はダッシュボード、ルーター、それが生んだモデルを停止する。止めるのは LlamaForge が起動したプロセスだけで、他に動かしている llama-server には触れない。

**必要な環境:** Windows 10/11、Linux、または Apple Silicon 上の macOS。Python 3.10+(stdlib のみ。Windows インストーラは同梱)。NVIDIA(CUDA)、AMD/Intel(Vulkan)、Apple(Metal) GPU があれば使う。CPU のみでも動作する。ソースからのビルドには Git、CMake、Ninja、C++ コンパイラも必要で、パッケージマネージャが許す範囲では Setup タブから入れられる。

## 仕組み

LlamaForge には llama.cpp のコードは含まれない。純 stdlib の Python バックエンドが llama.cpp 純正のルーター API を操作し、`models.ini` を編集し、公式ビルドを取得する(または `git` / `cmake` を呼び出す)。ノブの一覧は `llama-server --help` からその場でパースするので、動かしているビルドに追従する。

LlamaForge はコンテキストサイズ、GPU レイヤー、マルチ GPU 分割を固定しない。llama.cpp の `--fit`(既定で有効)がこの 3 つを読み込み時の空き VRAM に合わせ、必要なら MoE エキスパートを CPU へ移す。いずれかを固定すると fit は切れるため、自分で設定しない限り未設定のままになる。

**セキュリティ:** ダッシュボードは `panel_host`(このビルド)で LAN 共有しない限り `127.0.0.1` だけで待受ける。ルーターはローカルでもキー必須なので、訪れた Web ページから操作できない。LAN アクセスは Setup からのオプトインでキーが必要(このビルドの `router_allow_keyless_lan` でそれを外せる)。Host/Origin ガードは全モードで有効のまま。詳細と脆弱性の非公開報告: [SECURITY.md](https://github.com/dadwritestech/LlamaForge/blob/master/SECURITY.md)。

## ドキュメント

すべてアプリ内の **Help** タブと **[dadwritestech.github.io/LlamaForge](https://dadwritestech.github.io/LlamaForge/)** にある: [設定](../content/config.md)、[キーボードショートカット](../content/keymap.md)、[テーマと色覚安全モード](../content/theming.md)、[vLLM](../content/vllm.md)、[トラブルシューティング](../content/troubleshooting.md)、[新着](../content/whats-new.md)。[ROADMAP.md](../../ROADMAP.md) に出荷済みと計画がある。アーリープレビューなので優先順位はフィードバックに従う。

## クレジットとライセンス

LlamaForge は MIT ライセンス([LICENSE](../../LICENSE))。**[llama.cpp](https://github.com/ggml-org/llama.cpp)** - MIT, (c) The ggml authors - をビルドして操作する。[NOTICE](../../NOTICE) と [LICENSE.llama.cpp.txt](../../LICENSE.llama.cpp.txt) を参照。骨の折れる部分は彼らの成果。upstream プロジェクトにスターと支援を。

`pi_run` は **[pi](https://github.com/earendil-works/pi)**(Mario Zechner のオープンソースコーディングエージェント、MIT)を操作する。LlamaForge は同梱しない: **Setup → Install pi** が公開 npm パッケージをあなたの Node.js で LlamaForge 自身の `agents/` フォルダに取得する(自分で `npm install -g @earendil-works/pi-coding-agent` してもよい)。
