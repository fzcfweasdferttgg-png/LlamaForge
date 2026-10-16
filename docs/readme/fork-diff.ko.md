[English](../content/fork-diff.md) · 한국어 · [日本語](fork-diff.ja.md) · [简体中文](fork-diff.zh-CN.md) · [Русский](fork-diff.ru.md)

# Fork 차이

원본 LlamaForge에 아래 변경 사항을 더했습니다. '이 빌드에서 제거됨'에 나열된
기능은 빠졌습니다. Ubuntu Server에서만 테스트했습니다.

## LAN 노출 (옵트인)

`config.json`의 `panel_host`, `router_allow_keyless_lan`이 대시보드,
라우터를 LAN으로 옮깁니다. 기본값은 그대로입니다: 루프백 바인딩,
키가 없으면 라우터는 여전히 fail closed입니다. Host/Origin 가드는 계속 켜져 있고,
추가로 이 기기 자체의 이름을 허용합니다. 세부 내용: [Config](../content/config.md),
[Security](https://github.com/dadwritestech/LlamaForge/blob/master/SECURITY.md).

## HTTP 위의 MCP (옵트인)

`mcp_host` / `mcp_port`(기본값 `8092`)가 MCP 서버를 stdio 옆에서 상태 비유지
Streamable HTTP로 제공합니다. 기본적으로 꺼져 있습니다. 세부 내용: [MCP Server](../content/mcp.md).

## 벤더 도구 없는 GPU 텔레메트리

`nvidia-smi`가 없으면 GPU 상태(VRAM 사용/전체, 사용률, 온도)를 커널의 DRM sysfs에서
읽습니다. 장치 이름과 토큰은 `llama-server --list-devices`에서 오므로, 다중 모델
플래너는 `CUDA0`만이 아니라 `Vulkan0`도 인식합니다.

## 모델 게이트웨이(LiteLLM)

**Gateway** 탭이 [LiteLLM](https://github.com/BerriAI/litellm)(MIT)을 관리되는
사이드카로 실행합니다: 가상 모델 이름 하나가 여러 실제 모델에 요청을 번갈아
보내고(`gateway_models`), 자유 형식의 LiteLLM 프리셋(`gateway_presets`)으로
조정합니다. 각 가상 이름은 두 가지 동작 중 하나입니다: 교대(요청이 모델 사이에서
엄격하게 순서대로 돕니다 — LlamaForge가 LiteLLM에 넣는 round-robin) 또는
기본 + 예비(첫 모델에 여유 슬롯이 있는 동안은 거기에 머물고, 가득 차면 예비로
넘어갑니다). `router_allow_keyless_lan`을 쓰면 게이트웨이도 키 없이 응답합니다.
기본적으로 꺼져 있습니다. 자체 포트(`gateway_port`, 기본값 8300),
자체 venv(`tools/litellm/venv`), 패널 API와 MCP 도구로 관리합니다.
세부 내용: [모델 게이트웨이](../content/gateway.md).

## Engines(사전 빌드)

Build / Update 탭은 이제 **Engines**입니다: llama.cpp 계열 엔진 세 개 — llama.cpp(ggml-org),
ik_llama.cpp(Thireus 빌드), beellama.cpp(Anbeeld) — 의 빌드를 내려받아 고르고, 라우터가 돌릴 엔진을 전환합니다.
실행 프로파일은 엔진 계열별로 분리됩니다: 고정되지 않은 프로파일은 다른 엔진에서
실행을 거부하고, 대상 엔진이 모르는 프리셋 키는 버려져 실행 보고서에 나열됩니다.
소스 빌드와 vLLM 백엔드는 제거되었습니다. 자세한 내용: [Engines](../content/build.md).

## UI 현지화

웹 UI는 영어, 러시아어, 일본어, 한국어, 중국어(간체)를 지원합니다. 언어는 엔진
배지 옆 탑바에서 고르고 브라우저에 저장됩니다(localStorage `lf_lang`); `config.json`의
`web_lang`이 패널 기본값을 정합니다. 번역이 없는 문자열은 영어로 표시됩니다.

## 이 빌드에서 제거됨

Chat, Voice, Embers(미니 에이전트), Context 위키, Help 뷰어, 탑바 시계,
크레딧/스타 배너는 제거되었습니다. vLLM 백엔드도 완전히 제거되었습니다:
`backend/vllm_*` 모듈, 이를 구동하던 WSL2 헬퍼, 문서 페이지와 테스트가
사라졌으며 87개 파일에서 약 2,700줄이 삭제되었습니다. 나머지는 업스트림
그대로입니다.

## 설치 및 업데이트

README의 한 줄 설치 명령은 원본 프로젝트를 설치합니다. 이 빌드는 이 저장소에서
배포됩니다. 앱 내 업데이터는 설치 매니페스트(`.lf-files.json`)가 없는 사본을
거부합니다. 그런 사본은 수동으로 업데이트합니다.
