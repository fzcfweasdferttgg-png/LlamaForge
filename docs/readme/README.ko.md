<p align="center"><b>브라우저 탭에서 돌리는 진짜 llama.cpp 서버.</b><br>
모델을 고르고, GPU에 들어가는지 확인하고, 로드하고, 대화하세요. llama-server 플래그는 전부 그대로 있어, 필요할 때 쓸 수 있습니다.</p>


<h2 align="center">
  <a href="../../README.md">English</a> ·
  한국어 ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.ru.md">Русский</a>
</h2>

> [!IMPORTANT]
> [dadwritestech/LlamaForge](https://github.com/dadwritestech/LlamaForge)의 포크 빌드 — Ubuntu Server에서만 테스트했습니다. 원본과의 차이: [Fork 차이](fork-diff.ko.md).

```powershell
irm https://raw.githubusercontent.com/dadwritestech/LlamaForge/master/install.ps1 | iex   # Windows, no admin
```
```bash
curl -fsSL https://raw.githubusercontent.com/dadwritestech/LlamaForge/master/install.sh | sh   # Linux / macOS
```
이 명령은 원본 프로젝트를 설치합니다. 이 빌드는 이 저장소에서 배포됩니다. 그다음 **Install llama.cpp**(GPU에 맞는 공식 빌드, 컴파일러 불필요) → **Discover** → **Load**.
얼리 프리뷰: Windows + NVIDIA가 가장 많이 테스트된 경로입니다. Linux와 macOS는 CI를 통과하지만 실제 하드웨어 사용 경험이 거의 없습니다.

LlamaForge는 모델을 직접 실행하지 않습니다. llama.cpp의 `llama-server` 라우터를 설치·구동하고
`models.ini`를 대신 편집합니다. ggml-org와 무관합니다. 제어보다 다듬어진 완성도를 원하면
[LM Studio](https://lmstudio.ai), [Ollama](https://ollama.com) 또는 [Jan](https://jan.ai)을 사용하세요.

## 왜 LlamaForge인가

새 모델 아키텍처는 llama.cpp에 먼저 들어옵니다. 데스크톱 앱은 동봉된 엔진을 다음에
갱신할 때 이를 반영합니다. LlamaForge는 **공식 llama.cpp 릴리스 자체**(또는 직접 만든
빌드나 포크)를 실행하므로, 새 모델은 **Update** 클릭 한 번이면 되고, 서버 플래그 중
선별된 일부가 아니라 전부에 UI를 제공합니다.

| | **LlamaForge** | LM Studio | Ollama |
|---|---|---|---|
| 오픈 소스 | ✅ MIT | ❌ 독점 앱 | ✅ MIT |
| 엔진 | 공식 업스트림 llama.cpp 빌드, 모든 버전, 또는 자체 포크 | LM Studio가 동봉한 llama.cpp / MLX 런타임 | ggml 위의 Ollama 자체 엔진 |
| 모델별 설정 | `llama-server --help`에 나오는 모든 플래그 (현재 빌드 기준 200개 이상) | 많지만 선별됨 | Modelfile 매개변수 |
| Hugging Face의 모든 GGUF, 다운로드 전 VRAM 적합성 평가 | ✅ (대략적인 추정치) | ✅ | GGUF 가져오기, 적합성 평가 없음 |
| OpenAI + Anthropic 호환 API | ✅, 여기에 Claude Code / Codex / pi.dev 원클릭 설정 | ✅ | ✅ |
| 백엔드 의존성 | 없음 (Python 표준 라이브러리) | – | – |
| 네이티브 데스크톱 앱 | ❌ 브라우저에서 실행 | ✅ | ✅ |
| 성숙도 | **얼리 프리뷰** | 성숙 | 성숙 |

<sub>2026년 10월 기준, 저희가 아는 한에서의 내용입니다. 틀린 점을 발견하면 이 표를 고치는 PR을 환영합니다.</sub>

## 구성

- **Models**: 이 기기의 모든 모델을 하나의 목록에, GPU별 실시간 VRAM/사용률/온도와 함께. 해당 행에서 로드, 언로드, 조정이 가능합니다.
  - 모델을 펼치면 GGUF 메타데이터 카드(아키텍처, 양자화, 학습 컨텍스트, 레이어) 옆에서 llama-server 플래그 전부를 그룹별로 검색하며 편집합니다.
  - 저장하면 모델을 그 자리에서 다시 로드합니다. 로드 실패 시 라우터 로그의 마지막 오류와 추정 원인 힌트를 보여줍니다.
  - 프리셋, 실행 프로필(모델 + 프리셋 + 고정된 llama.cpp 빌드를 한 번에), 나란히 비교, 복사-붙여넣기용 클라이언트 코드 조각.
  - Setup에서 **Multi-model**을 켜면 메인 모델과 워커를 동시에 올려 둘 수 있습니다. 플래너가 각 모델을 들어맞는 GPU에 배치하며, 메모리 점유량은 이 기기에서 측정값을 씁니다. 모델마다 llama.cpp(또는 ik_llama.cpp) 빌드를 따로 고정할 수도 있습니다.
- **Voice**: llama.cpp의 `llama-tts`로 GPU에서 구동하는 텍스트 음성 변환: 10개 언어의 Qwen3-TTS, 또는 CPU에서도 실시간보다 빠른 소형 영어 Pocket TTS. 선택적으로 녹음하거나 업로드한 음성 사용(사용 권한이 있는 음성만 클론하세요).
- **Embers**: 주제를 감시하고 자체 위키를 유지하며 일정에 따라 브리핑을 작성하는 소형 로컬 에이전트. 위키 항목은 출처를 그대로 인용해야만 인정됩니다. **Forge**는 인터뷰를 통해 하나를 만들고, **Model Scout**는 설정이 필요 없습니다. Embers는 브라우저나 도구가 없으며, 푸시 알림을 켜지 않는 한 아무것도 기기 밖으로 나가지 않습니다.
- **Discover**: 이번 주 신규 모델을 먼저 보여주는 Hugging Face GGUF 검색. 모든 양자화는 다운로드 전에 VRAM 적합성 대략 등급(FITS / TIGHT / CPU OFFLOAD)을 받습니다. 다운로드는 중단 후 재개되고 스스로 등록되며 **Load** 준비로 끝납니다.
- **Will it run?**: 저장소와 양자화를 고르면 적합성과 대략적인 속도 추정치를 알려줍니다.
- **Build / Update**: 원클릭 공식 llama.cpp 빌드와 롤백, 또는 GPU에 맞게 플래그를 감지해 소스에서 빌드. [ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp)도 구동하고, Windows에서는 WSL2의 [vLLM](https://github.com/vllm-project/vllm)도 구동합니다.
- **Stats**: 라우터 자체 메트릭에서 모델별 토큰, 속도, 실행 횟수를 집계합니다. 클라이언트는 라우터에 직접 연결하므로 클라이언트별 통계는 불가능합니다.
- **Context**: Markdown 컨텍스트 문서를 프로필로 묶어 요청에 주입하거나 `CLAUDE.md` / `AGENTS.md`에 기록합니다.
- **Recipes**: 프로필을 읽기 쉬운 JSON으로 공유; 다른 사람은 붙여넣기 한 번으로 가져오고, 모델이 없으면 LlamaForge가 다운로드합니다. [커뮤니티 갤러리](../../recipes/)가 있습니다.

첫 실행 마법사와 **Lite / Advanced** 토글이 상세 설정을 원할 때까지 치워 둡니다. 기본 테마인 **Stowage**는 각 GPU를 1 GiB 칸으로 그린 적재 구역 도면처럼 그립니다. **Hearth**와 **Classic**은 한 번의 클릭으로 전환되며, 각각 라이트, 다크, 색각 보호 모드를 제공합니다.

## 다른 앱에서 사용하기

OpenAI API를 지원하는 것이라면 무엇이든(Open WebUI, SillyTavern, Continue, Cline, Aider, OpenAI SDK) `http://127.0.0.1:8080/v1`에 동작합니다.
라우터는 항상 API 키로 실행되며, 모델의 **Client Config**에서 base URL, 키, 모델 id를
바로 붙여넣을 수 있게 받습니다.

- **Anthropic 호환** `POST /v1/messages`를 패널에서 제공, 스트리밍과 도구 사용 지원.
- **OpenAI speech 호환** `POST /v1/audio/speech`를 패널에서 제공(WAV 또는 PCM), `llama-tts` 기반.
- **Connect an agent**가 **Claude Code**, **Codex**, **pi.dev**의 설정을 작성합니다(파일을 건드리기 전에 항상 백업).
- Load/unload 엔드포인트로 에이전트가 필요할 때 모델을 교체할 수 있습니다.
- **MCP server** (stdio, `backend/mcp_server.py`; 이 빌드에는 선택적 Streamable
  HTTP 전송도 포함, 문서 참조): Claude Code, Codex 또는 어떤 MCP 클라이언트든 로드된 모델을 보고,
  모델을 로드·언로드하고, 적합성을 확인하고, Hugging Face에서 GGUF를 받아오고, 로드된 로컬 모델에서
  실행되는 [pi](https://github.com/earendil-works/pi)에게 작업 전체를 넘길 수 있습니다(`pi_run`). **Setup -> MCP server**에서
  한 줄로 설정, 예: `claude mcp add --scope user llamaforge -- python <LlamaForge>/backend/mcp_server.py`.

## 설치

위의 한 줄 설치 명령은 원본 프로젝트를 설치합니다. 이 빌드는 이 저장소에서 배포됩니다. [Fork 차이](fork-diff.ko.md) 참조.

설치 프로그램은 Python 3.10+를 찾고(Windows에서는 없으면 SHA-256이 고정된 python.org의
임베디드 Python 사본을 전용으로 내려받습니다), 최신 릴리스를 내려받고, 시작 메뉴/앱 메뉴
항목(macOS는 `~/Applications/LlamaForge.app`, Linux/macOS에는 `llamaforge` 명령 추가)을
만들고 대시보드를 엽니다. 업데이트 시 설정, 모델, 엔진은 유지됩니다. 제거는 Windows의
**Apps & Features** 또는 `llamaforge uninstall`; 설정이나 모델을 건드리기 전에 묻습니다.

<details><summary>소스에서 (git clone)</summary>

```powershell
git clone https://github.com/dadwritestech/LlamaForge
cd LlamaForge
powershell -ExecutionPolicy Bypass -File bootstrap.ps1   # Windows
./bootstrap.sh                                           # Linux / macOS
```

bootstrap 스크립트는 Python과 Git을 확인하고(무엇이든 설치하기 전에 묻습니다),
`config.json`을 작성하고 대시보드를 엽니다.
</details>

**일상 사용:** 시작 메뉴/앱 메뉴에서 **LlamaForge**를 열거나 `llamaforge`를 실행합니다.
라우터와 대시보드를 시작하고 브라우저를 엽니다.

- Dashboard: http://127.0.0.1:8090
- 다른 앱용 API: http://127.0.0.1:8080/v1

`llamaforge stop`(또는 `stop.ps1` / `stop.sh`)은 대시보드, 라우터, 그리고 라우터가
생성한 모델을 종료합니다. LlamaForge가 시작한 프로세스만 멈춥니다. 사용자가 직접 띄운
다른 llama-server는 건드리지 않습니다.

**시스템 요구 사항:** Windows 10/11, Linux, 또는 Apple Silicon macOS. Python 3.10+(표준
라이브러리만 사용; Windows 설치 프로그램이 자체 포함). NVIDIA(CUDA), AMD/Intel(Vulkan),
Apple(Metal) GPU가 있으면 사용하고, CPU만으로도 동작합니다. 소스에서 빌드하려면 추가로
Git, CMake, Ninja, C++ 컴파일러가 필요하며, 패키지 매니저가 허용하는 곳에서는 Setup 탭이
설치해 줄 수 있습니다.

## 동작 방식

LlamaForge에는 llama.cpp 코드가 들어 있지 않습니다. 순수 표준 라이브러리 Python 백엔드가
llama.cpp 자체 라우터 API를 구동하고, `models.ini`를 편집하며, 공식 빌드를 내려받습니다
(또는 `git` / `cmake`를 호출). 설정 항목 목록은 `llama-server --help`에서 실시간으로
파싱하므로, 실행 중인 빌드를 그대로 따라갑니다.

LlamaForge는 컨텍스트 크기, GPU 레이어, 다중 GPU 분할을 고정하지 않습니다. llama.cpp의 `--fit`
(기본 활성)이 로드 시 이 셋을 여유 VRAM에 맞춰 정하고, 필요하면 MoE 전문가(expert)를 CPU로
옮깁니다. 이 중 하나라도 고정하면 fit이 꺼지므로, 직접 설정하지 않는 한 비워 둡니다.

**보안:** 대시보드는 `panel_host`(이 빌드)로 LAN에 공유하지 않는 한 `127.0.0.1`에서만 수신
대기합니다. 라우터는 로컬에서도 키가 필요하므로, 방문한 웹 페이지가 이를 구동할 수 없습니다.
LAN 접근은 Setup에서 선택 사항이며 키가 필요합니다(이 빌드의 `router_allow_keyless_lan`은
이를 해제할 수 있습니다). Host/Origin 가드는 모든 모드에서 켜져 있습니다. 세부 내용과 취약점
비공개 보고 방법: [SECURITY.md](https://github.com/dadwritestech/LlamaForge/blob/master/SECURITY.md).

## 문서

모든 문서는 앱 내 **Help** 탭과 **[dadwritestech.github.io/LlamaForge](https://dadwritestech.github.io/LlamaForge/)**에 있습니다:
[설정](../content/config.md), [키보드 단축키](../content/keymap.md),
[테마 및 색각 보호 모드](../content/theming.md), [vLLM](../content/vllm.md),
[문제 해결](../content/troubleshooting.md), [새로운 점](../content/whats-new.md).
[ROADMAP.md](../../ROADMAP.md)에 출시된 항목과 계획이 있습니다. 얼리 프리뷰이므로 우선순위는 피드백을 따릅니다.

## 크레딧 및 라이선스

LlamaForge는 MIT 라이선스입니다([LICENSE](../../LICENSE)). **[llama.cpp](https://github.com/ggml-org/llama.cpp)**를
빌드하고 구동합니다 - MIT, (c) The ggml authors -
[NOTICE](../../NOTICE)와 [LICENSE.llama.cpp.txt](../../LICENSE.llama.cpp.txt) 참조.
어려운 부분은 그들의 몫입니다. 업스트림 프로젝트에 스타를 눌러 응원해 주세요.

`pi_run`은 Mario Zechner의 오픈 소스 코딩 에이전트 **[pi](https://github.com/earendil-works/pi)**(MIT)를 구동합니다.
LlamaForge는 이를 동봉하지 않습니다: **Setup → Install pi**가 사용자의 Node.js로 게시된 npm
패키지를 LlamaForge 자체 `agents/` 폴더에 가져옵니다(또는 직접
`npm install -g @earendil-works/pi-coding-agent`로 설치).
