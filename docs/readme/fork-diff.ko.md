[English](../content/fork-diff.md) · 한국어 · [日本語](fork-diff.ja.md) · [简体中文](fork-diff.zh-CN.md) · [Русский](fork-diff.ru.md)

# Fork 차이

원본 LlamaForge에 아래 변경 사항을 더했습니다. 나머지는 업스트림 그대로입니다.
Ubuntu Server에서만 테스트했습니다.

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

## 설치 및 업데이트

README의 한 줄 설치 명령은 원본 프로젝트를 설치합니다. 이 빌드는 이 저장소에서
배포됩니다. 앱 내 업데이터는 설치 매니페스트(`.lf-files.json`)가 없는 사본을
거부합니다. 그런 사본은 수동으로 업데이트합니다.
