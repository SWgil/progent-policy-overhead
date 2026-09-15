# Qwen3.8-27B ASR 0% 이슈와 최신 IPI 벤치마크 조사 리포트

작성일: 2026-09-15
대상: `progent-policy-overhead` 레포 (AgentDojo / ASB / agentdojo-mcp 기반 Progent 오버헤드 실험)

---

## 1. 요약 (TL;DR)

- **가설은 대체로 타당하다.** AgentDojo(2024년 6월 공개)의 기본 공격(`important_instructions`)은 2025년 이후 모델에서 빠르게 포화되고 있다. 2026년 논문들이 보고하는 무방어(no-defense) 정적 ASR은 Claude Haiku 4.5 0.3%, GPT-5.4-mini 6.9% 수준이다. Qwen3.8은 2026년 8월에 공개되었고 "million-agent 환경에서의 대규모 RL"로 학습되었다고 밝히고 있어, 2024년 벤치마크 데이터가 학습 분포에 포함되었을 가능성과 단순 주입에 대한 정렬(alignment)이 강화되었을 가능성 모두 있다.
- **단, 0%라는 값 자체는 먼저 검증이 필요하다.** 같은 계열의 직전 세대인 Qwen3.5-27B는 AgentDojo에서 vanilla ASR 34.6%(banking/travel 어려운 구간)로 보고되어 있고, Qwen3.6-35B는 GitHub 이슈에 심어둔 단순 주입에도 넘어간 사례가 공개되어 있다. 한 세대 만에 34.6% → 0%로 떨어지는 것은 가능은 하지만, 파이프라인 문제(tool-call 파싱 실패, thinking 모드, 공격 미주입, 평가 스크립트 오류)일 확률도 함께 봐야 한다. 3절의 체크리스트를 먼저 수행할 것을 권한다.
- **추천 벤치마크(우선순위 순):**
  1. **AgentDyn** (2026.02) — AgentDojo 위에 구축된 후속 벤치마크. 같은 CLI/메트릭을 쓰고 **Progent가 이미 defense로 통합**되어 있어 이 레포에 가장 적은 비용으로 붙는다. 3개 신규 suite(shopping, github, dailylife), 60 user task, 560 injection case.
  2. **AutoDojo** (2026.06) — AgentDojo용 **적응형(adaptive) 블랙박스 공격** 확장. 기존 suite를 그대로 쓰면서 공격 강도만 올린다. 정적 ASR 0%인 필터를 28%까지 뚫었고, Progent도 평가 대상에 포함되어 있다. "모델이 벤치마크를 외웠는지 vs 실제로 강건한지"를 가르는 데 가장 직접적이다.
  3. **MSB (MCP Security Bench)** (ICLR 2026) 또는 **MCPTox** — 이 레포의 `agentdojo-mcp` / real-world-agents 축과 맞는 MCP 기반 벤치마크. 툴 설명(metadata) 주입, 파라미터 조작 등 AgentDojo가 다루지 않는 공격면을 다루며 Qwen3 계열에서도 ASR 27~58%가 보고된다.
  4. **AgentLAB** (2026.02) — 장기(long-horizon) 멀티턴 주입. Qwen-3 ASR 81.5%, GPT-5.1 69.9%로 최신 모델에도 잘 통한다. 정책 기반 방어(Progent)가 멀티턴 drift에 어떻게 대응하는지 보기에 좋다.
- 공격 강도만 올리고 싶다면 AgentDojo 안에서 **ChatInject**(chat-template 위장, 5.18% → 32.05%), **AutoInject**(RL 학습 suffix), **IterInject**(피드백 기반 반복 최적화) 같은 최신 공격을 attack 플러그인으로 추가하는 경로도 있다.

---

## 2. 현재 레포 설정 요약

| 항목 | 현재 상태 |
|---|---|
| 벤치마크 | AgentDojo v0.1.x (banking, slack, travel, workspace 4개 suite, 97 user task, 629 security case) |
| 공격 | `important_instructions` (정적 템플릿) 하나만 사용 (`agentdojo/run.sh`) |
| 추가 공격 코드 | `attacks/` 에 direct, ignore_previous, system_message, injecagent, tool_knowledge, DoS 계열 존재 |
| 로컬 모델 경로 | `secalign_local_llm.py` 의 `LocalLLM` 이 `http://localhost:8000/v1` (vLLM OpenAI 호환) 을 사용 |
| 방어 | Progent 정책 (`SECAGENT_*` 환경변수로 자동 정책 업데이트) |
| 기타 | ASB(Agent Security Bench), agentdojo-mcp + real-world-agents |

AgentDojo 공식 저장소는 2024년 10월 v0.1.35 이후 **새 suite나 새 공격이 추가되지 않았고**, 공식 results 페이지의 최신 모델도 Claude 3.7 Sonnet(2025-02, ASR 7.31%)에서 멈춰 있다. 즉 벤치마크 자체가 2024년 수준에 고정되어 있다.

---

## 3. ASR 0% 원인 분석

### 3.1 "벤치마크 포화" 가설을 뒷받침하는 근거

| 근거 | 출처 |
|---|---|
| AgentDojo·ASB·InjecAgent·tau-Bench 네 벤치마크 모두 단순한 firewall(tool 입출력 sanitizer)만으로 ASR 0%에 도달. 저자들은 원인을 "flawed success metrics, implementation bugs, and most importantly, **weak attacks**"로 지목하고, "newer LLMs trained on security robustness readily ignore" 단순 공격이라고 명시 | Firewalls or Stronger Benchmarks? (arXiv 2510.05244) |
| 무방어 정적 `important_instructions` ASR: Claude Haiku 4.5 **0.3%**, GPT-5.4-mini 6.9%, GPT-4o-mini 58.6%. 적응형 공격으로 바꾸면 Haiku 1.8%, GPT-5.4-mini 8.7%로 소폭 회복 | AutoDojo (arXiv 2606.15057) |
| 46만 4천 명 참가, 27만 2천 회 공격 대회에서도 Claude Opus 4.5 ASR 0.5%, Gemini 2.5 Pro 8.5% | IPI Arena 대회 (arXiv 2603.15714) |
| "Existing single-turn prompt-injection attacks produce near-zero ASR on GPT-5.4" | ClawTrojan (arXiv 2605.31042) |
| Qwen3.8: 2026-08-14 공개, "RL scaled across million-agent environments with progressively complex task distributions" — 에이전트 환경 RL 규모가 커져 공개 벤치마크(2024년 AgentDojo 포함)가 학습 분포에 들어갔을 가능성 | QwenLM/Qwen3.8 README |

→ 최신 프론티어급 모델이라면 AgentDojo 기본 공격에서 0~2% 수준이 나오는 것이 이제는 이상하지 않다.

### 3.2 반대로 "측정 오류"를 의심해야 하는 근거

| 근거 | 출처 |
|---|---|
| **Qwen3.5-27B**: AgentDojo banking/travel 어려운 구간 vanilla ASR **34.6%** (Qwen3.5-9B 43.2%, Qwen3-8B 47.2%) | arXiv 2608.02657 (IPI-exposure-signal) |
| **Qwen3.6-35B-A3B**: GitHub 이슈 본문에 심은 단순 주입 지시를 그대로 실행 | QwenLM/Qwen3.8 issue #172 |
| Qwen3-32B: MCPTox 58.5%, Qwen3-235B: AgentDyn 22.67%, Qwen-3: AgentLAB 81.5% | 각 논문 |
| Qwen3.8 README·모델 카드에 프롬프트 주입 강건성이나 AgentDojo 언급 없음 (정렬 강화 근거가 공개된 바 없음) | QwenLM/Qwen3.8 |

Qwen3.8-27B에 대한 공개된 AgentDojo 수치는 **찾지 못했다**. 따라서 다음을 먼저 확인해야 한다.

### 3.3 0% 검증 체크리스트

1. **Utility 확인**: no-attack utility와 utility-under-attack이 정상 범위(예: 60~90%)인지. utility가 낮으면 tool-call 파싱 실패(reasoning 블록, 잘못된 chat template, `LocalLLM` 포맷 불일치)로 공격도 실행이 안 된 것일 수 있다.
2. **주입 실제 삽입 여부**: 로그에서 tool 출력 안에 `<INFORMATION>` 블록이 들어갔는지, 그리고 모델의 응답이 그 내용을 인지했는지(예: "I noticed an instruction...") 확인. 인지하고 거부했다면 정렬, 인지조차 못했다면 파이프라인 문제.
3. **thinking / reasoning 모드**: Qwen3.x 계열은 thinking on/off에 따라 tool-call 신뢰도와 주입 순응도가 달라진다(MCPTox: reasoning 모델이 오히려 더 취약). 두 설정을 모두 측정.
4. **방어 없이 측정했는지**: `SECAGENT_*` 환경변수가 켜져 있으면 Progent 정책이 tool을 막아 ASR이 0이 된다. 레포 README의 slack injection task 5 관련 주의사항(차단된 tool call도 trace에 남아 오판)도 재확인.
5. **공격 다양화**: `tool_knowledge`, `important_instructions` 외에 이미 레포에 있는 `direct`, `ignore_previous`, `system_message`, `injecagent`를 돌려 모든 공격에서 0%인지 확인. 전부 0%면 정렬/암기 쪽 신호가 강해진다.
6. **알려진 AgentDojo 버그**: 주입이 task-critical 정보를 덮어써서 애초에 풀 수 없는 케이스, 엄격한 cardinality 기반 utility 체크 등(arXiv 2510.05244에서 수정 패치 제안). 이 케이스들은 모델과 무관하게 결과를 왜곡한다.

---

## 4. 후보 벤치마크 비교

### 4.1 한눈에 보기

| 벤치마크 | 시기 / 출처 | 유형 | 규모 | 최신·오픈 모델 무방어 ASR | Progent 적용 난이도 | 코드 |
|---|---|---|---|---|---|---|
| **AgentDyn** | 2026.02, arXiv 2602.03117 | AgentDojo 확장, 동적·개방형 task | 60 task / 560 case / 3 suite | GPT-4o 37.8%, Gemini-2.5 Pro 20.6%, Qwen3-235B 22.7%, Llama-3.3-70B 11.9% | **매우 낮음** (Progent 이미 통합, 같은 CLI) | github.com/leolee99/AgentDyn |
| **AutoDojo** | 2026.06, arXiv 2606.15057 | AgentDojo용 적응형 블랙박스 공격 | 기존 3 suite 재사용 | GPT-5.4-mini 8.7%, Haiku 4.5 1.8%, GPT-4o-mini 52.4%; PIGuard 0%→28% | **낮음** (Progent 평가 포함) | github.com/xhOwenMa/AutoDojo |
| **MSB** | ICLR 2026, arXiv 2510.15994 | MCP 파이프라인 12종 공격, 실제 MCP 서버 실행 | 2,000+ case / 405 tool / 25 서버 | Qwen3-8B 47.2%, Qwen3-30B 27.1%, GPT-4o-mini 58.6%, DeepSeek-V3.1 60.9% | 중간 (`agentdojo-mcp` 경험 활용 가능) | github.com/dongsenzhang/MSB |
| **MCPTox** | 2025.08, arXiv 2508.14925 | MCP tool poisoning (툴 설명 주입) | 1,312 case / 45 서버 / 353 tool | Qwen3-32B 58.5%, Qwen3-235B 50.6%, Qwen3-8B 41.8%, Claude 3.7 34.3% | 중간 (컨텍스트에 툴 설명만 주입, 서버 실행 불필요) | 익명 저장소 (anonymous.4open.science/r/AAAI26-7C02) |
| **AgentLAB** | 2026.02, arXiv 2602.16901 | 장기 멀티턴 주입 5개 계열 | 644 case / 28 env | Qwen-3 81.5%, GPT-5.1 69.9%, GPT-4o 78.1%, Claude-4.5 28.9% | 중간~높음 (별도 env, 멀티턴 정책 필요) | github.com/TanqiuJiang/AgentLAB |
| **IPI Arena Bench** | 2026.03, arXiv 2603.15714 | 인간 레드팀 공격 corpus + 하네스 (tool / coding / browser) | 41 behavior / 95 attack string | Claude Opus 4.5 0.5%, Gemini 2.5 Pro 8.5% | 중간 (vLLM 지원, MIT) | github.com/grayswansecurity/ipi_arena_os |
| **b3 (Backbone Breaker)** | ICLR 2026, Lakera/AISI | 백본 LLM 단위 threat snapshot, 194k 인간 공격 | 34개 이상 모델 리더보드 | 리더보드 공개 | 낮음 (Inspect Evals) 단, 단일 스텝이라 Progent 정책과 결이 다름 | inspect_evals/b3 |
| **LivePI** | 2026.05, arXiv 2605.17986 | 실제 VM + OpenClaw, 7개 입력 surface | 12 공격 계열 / 5 목표 | Gemini 3.1 Pro 29.6%, GLM-5 27.8%, GPT-5.3-Codex 27.2%, Opus 4.6 10.7% | 높음 (EC2 VM, 실제 서비스 계정) | github.com/leizhao7/livepi |
| **WASP** | NeurIPS 2025, arXiv 2504.18575 | 웹 에이전트, 현실적 공격자 모델 | 부분 성공 최대 86% | 브라우저 에이전트 대상 | 높음 (웹 환경) | github.com/facebookresearch/wasp |
| **RedTeamCUA / RTC-Bench** | ICLR 2026 Oral, arXiv 2505.21936 | 컴퓨터 사용 에이전트, 웹+OS 하이브리드 | 864 case | Claude 4.5 Sonnet CUA 60% | 높음 (VM + Docker) | github.com/OSU-NLP-Group/RedTeamCUA |
| **ClawTrojan** | 2026.05, arXiv 2605.31042 | 워크스페이스 파일에 잠복하는 다단계 트로이 주입 | OpenClaw 스타일 | GPT-5.4 95.5% | 높음 | github.com/RUC-NLPIR/ClawTrojan |

### 4.2 상세

#### (1) AgentDyn — 1순위
- AgentDojo의 세 가지 한계(정적 task, 도움말 지시 부재, 단순 task)를 겨냥. 평균 궤적 길이 7.1 step / 3.17 시나리오 (AgentDojo는 3.49 / 1.38).
- **Progent, CaMeL, DRIFT가 external defense로 이미 붙어 있다.** 논문 표 기준 Progent는 ASR을 0.5~13.6%로 낮추지만 utility가 2~25%로 급락(과잉 방어). 이 레포의 주제(정책 오버헤드)와 정확히 맞물리는 데이터 포인트다.
- 오픈 모델은 OpenRouter 경유(Llama 3.3, Qwen 3.x)로 실험했으므로, `LocalLLM`(vLLM OpenAI 호환)을 그대로 끼워 Qwen3.8-27B를 돌릴 수 있다.
- 주의: 공격은 여전히 `important_instructions`. task 난이도로 ASR을 끌어올리는 구조라, 모델이 주입 자체를 완전히 무시하면 여기서도 낮을 수 있다. 그래서 (2)와 조합을 권한다.

#### (2) AutoDojo — 2순위 (공격 강화)
- Gemini 3.1 Pro 같은 optimizer LLM이 성공/실패 신호만 받아 6회 반복으로 주입 문구를 최적화. 상위 5개 후보 리더보드 유지.
- 핵심 발견: 사용자가 동작을 지정하지 않는 **"action-open" task**("TODO 리스트 다 처리해줘")에서 ASR 64%까지 회복. Progent 같은 정책 기반 방어가 action-open task에서 어떻게 동작하는지가 흥미로운 실험 축.
- optimizer는 유료 API(Gemini) 기준으로 작성되어 있으니 비용 예산 필요. 오픈 모델 optimizer로 교체 가능한지는 코드 확인 필요.
- 대안/보완 공격: **ChatInject**(arXiv 2509.22830, chat template 위장 → AgentDojo 5.18%→32.05%, Qwen3-235B InjecAgent 8.5%→39.4%), **AutoInject**(arXiv 2602.05746, GRPO로 1.5B suffix 생성기 학습, GPT-5 nano/Gemini 2.5 Flash 성공), **IterInject**(arXiv 2605.24659, Claude Code 9개 타깃 중 5개 완전 성공). 셋 다 AgentDojo `register_attack` 인터페이스로 이식 가능.

#### (3) MSB / MCPTox — MCP 축
- 이 레포는 이미 `agentdojo-mcp`와 real-world-agents 실험이 있으므로 MCP 기반 벤치마크가 자연스럽다.
- **MSB**: 계획(이름 충돌, 선호 조작, 설명 주입) → 호출(범위 밖 파라미터) → 응답(사용자 사칭, 가짜 오류, 툴 전이, 검색 주입) 3단계 12종 공격. 실제 MCP 서버(Smithery 등록 304개 툴)를 실행하며 NRP = PUA × (1 − ASR) 지표 제공. "성능 좋은 모델일수록 tool-use 능력 때문에 더 취약"이라는 결과가 있어 Qwen3.8에서도 0%가 아닐 가능성이 높다.
- **MCPTox**: 툴 설명(metadata)에 주입. 기존 IPI 페이로드를 그대로 옮기면 ASR이 거의 0%로 떨어진다고 보고 → 별도 공격 설계 필요. 반대로 Progent 정책은 tool-call 인자를 보므로, 파라미터 조작형(725 case, 평균 46.7%)에 대한 방어 효과를 측정하기 좋다. 서버 실행 없이 컨텍스트에 툴 설명만 넣어 평가하므로 비용이 낮다. 단, 코드가 익명 저장소 상태.

#### (4) AgentLAB — 장기 멀티턴 축
- 의도 하이재킹, 툴 체이닝, 목표 표류, task 주입, 메모리 오염 5개 계열. 여러 턴에 걸쳐 조금씩 유도하므로 단일 턴 방어가 실패한다는 것이 주장.
- Qwen-3 81.5%로 오픈 모델 중 가장 취약하게 나왔다. Progent의 정책이 turn을 넘어 어떻게 누적/갱신되는지(`SECAGENT_UPDATE`) 검증에 적합.

#### (5) 참고: 백본 단위 / 인프라 무거운 벤치마크
- **b3**: 백본 LLM 단일 스텝 취약성. Inspect Evals에 포함되어 `pip install inspect-evals[b3]`로 바로 실행. 다만 정책 방어를 끼우기엔 에이전트 루프가 없다.
- **IPI Arena Bench**: 인간 레드팀이 만든 공격 문자열 95개를 코퍼스로 활용 가능. vLLM 지원, MIT. AgentDojo 공격 템플릿을 여기서 가져와 교체하는 방식도 유효.
- **LivePI / WASP / RedTeamCUA / ClawTrojan**: 결과는 강력하지만(각 30%, 86% 부분성공, 60%, 95.5%) 실제 VM·브라우저·서비스 계정이 필요해 이 레포 범위를 넘어선다. 논문 근거용으로 인용만 권장.

---

## 5. 권장 실행 계획

| 단계 | 내용 | 예상 비용 |
|---|---|---|
| 0 | 3.3 체크리스트로 현재 0% 검증. 특히 utility, 주입 삽입 여부, thinking on/off, 6종 공격 전부 실행 | 반나절 |
| 1 | **AgentDyn** 설치 후 Qwen3.8-27B를 vLLM `LocalLLM`으로 연결. shopping/github/dailylife 3 suite × (no defense, Progent) 측정. 기존 4 suite도 같은 CLI로 재측정 | GPU 시간만 |
| 2 | **AutoDojo**로 AgentDojo·AgentDyn 두 벤치마크에 적응형 공격 적용. 최소한 action-open task 부분집합에서 측정. optimizer API 비용 확인 | optimizer API 비용 |
| 3 | (선택) **ChatInject** 템플릿을 `attacks/`에 `register_attack`으로 추가해 정적 공격 강도 비교 | 1일 |
| 4 | **MSB** 또는 **MCPTox**로 MCP 축 측정. `agentdojo-mcp` 경험 재활용 | 2~3일 |
| 5 | (선택) **AgentLAB**으로 멀티턴 정책 갱신 오버헤드 측정 | 3일 이상 |

보고 시에는 AgentDyn·AutoDojo 논문과 동일하게 **Benign Utility / Utility under Attack / ASR** 세 지표를 함께 제시하고, Firewalls 논문(arXiv 2510.05244)이 지적한 AgentDojo·ASB 버그(주입이 task 정보를 덮어쓰는 케이스, ASB의 강제 attack-tool 삽입으로 ASR 약 8배 과대)를 어떻게 처리했는지 명시하는 것이 좋다.

---

## 6. 참고 자료

- AgentDyn: https://arxiv.org/abs/2602.03117 , https://github.com/leolee99/AgentDyn
- AutoDojo: https://arxiv.org/abs/2606.15057 , https://github.com/xhOwenMa/AutoDojo
- Indirect Prompt Injections: Are Firewalls All You Need, or Stronger Benchmarks?: https://arxiv.org/abs/2510.05244
- MCP Security Bench (MSB): https://arxiv.org/abs/2510.15994 , https://github.com/dongsenzhang/MSB
- MCPTox: https://arxiv.org/abs/2508.14925
- AgentLAB: https://arxiv.org/abs/2602.16901 , https://github.com/TanqiuJiang/AgentLAB
- IPI Arena (대규모 공개 대회): https://arxiv.org/abs/2603.15714 , https://github.com/grayswansecurity/ipi_arena_os
- b3 Backbone Breaker: https://b3.lakera.ai/ , https://ukgovernmentbeis.github.io/inspect_evals/evals/b3/index.html
- LivePI: https://arxiv.org/abs/2605.17986 , https://github.com/leizhao7/livepi
- WASP: https://arxiv.org/abs/2504.18575 , https://github.com/facebookresearch/wasp
- RedTeamCUA: https://arxiv.org/abs/2505.21936 , https://github.com/OSU-NLP-Group/RedTeamCUA
- ClawTrojan: https://arxiv.org/abs/2605.31042 , https://github.com/RUC-NLPIR/ClawTrojan
- ChatInject: https://arxiv.org/abs/2509.22830
- AutoInject (Learning to Inject): https://arxiv.org/abs/2602.05746
- IterInject: https://arxiv.org/abs/2605.24659
- AdapTools: https://arxiv.org/abs/2602.20720
- Assessing Automated Prompt Injection Attacks in Agentic Environments: https://arxiv.org/abs/2606.10525
- Qwen3.5-27B AgentDojo 수치 출처: https://arxiv.org/abs/2608.02657 , https://github.com/jianshuod/IPI-exposure-signal
- Qwen3.6-35B 주입 실패 사례: https://github.com/QwenLM/Qwen3.8/issues/172
- Qwen3.8 공개 정보: https://github.com/QwenLM/Qwen3.8
- AgentDojo 릴리스/결과: https://github.com/ethz-spylab/agentdojo/releases , https://agentdojo.spylab.ai/results/
