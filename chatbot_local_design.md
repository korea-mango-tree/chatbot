# RAG 챗봇 프로젝트 설계 문서

> 최종 업데이트: 2026-03-18 | 버전: 0.2.0

---

## 1. 프로젝트 개요

로컬 환경에서 **AI 챗봇 MVP**를 구축하고, 이후 납품형 SaaS로 확장 가능한 구조로 설계한다.

### 핵심 기술 스택

| 구분 | 기술 | 용도 |
|------|------|------|
| API 서버 | FastAPI + Uvicorn | 비동기 REST/WebSocket 서버 |
| 데이터베이스 | PostgreSQL 16 + pgvector | 벡터 검색 + 풀텍스트 검색 |
| AI 워크플로우 | LangGraph | 질문 재작성 → 검색 → 리랭킹 → 답변 |
| LLM | OpenAI gpt-4o-mini | 답변 생성, 질문 재작성, 리랭킹 |
| 임베딩 | OpenAI text-embedding-3-small | 1536차원 벡터 임베딩 |
| 인증 | JWT + bcrypt | 관리자 인증 |
| 실시간 통신 | WebSocket | 사용자-AI 채팅, 관리자 모니터링 |
| 컨테이너 | Docker Compose | PostgreSQL, pgAdmin |
| 프론트엔드 | Vanilla JS + HTML/CSS | 사용자 채팅 UI, 관리자 대시보드 |

---

## 2. 아키텍처

```
사용자 브라우저 ──── WebSocket /ws/chat/{session} ──────┐
                                                         │
관리자 브라우저 ──── REST API /api/* ────────────────────┤
                 └── WebSocket /ws/admin-watch ──────────┤
                 └── WebSocket /ws/admin/{session} ──────┤
                                                         │
                    ┌────────────────────────────────────┘
                    ▼
              FastAPI (Uvicorn)
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
   LangGraph    PostgreSQL   OpenAI API
   워크플로우   + pgvector   (LLM/Embedding)
```

### AI 워크플로우 (LangGraph)

```
START → rewrite_question → retrieve_chunks → rerank_chunks → generate_answer → END
```

| 노드 | 설명 |
|------|------|
| rewrite_question | 사용자 질문을 검색에 최적화된 형태로 재작성 |
| retrieve_chunks | 하이브리드 검색 (벡터 + 풀텍스트 + RRF 병합) |
| rerank_chunks | LLM으로 검색 결과 관련도 재평가 (5개 초과 시) |
| generate_answer | 검색 결과 기반 답변 생성 |

### 검색 파이프라인

```
사용자 질문 → 임베딩 → 벡터 검색 (top 20) ─┐
                    └→ 풀텍스트 검색 (top 20) ┤
                                              ▼
                                     RRF 병합 → 문서 그룹 확장 →
                                     중복 제거 → 리랭킹 → Top 10 반환
```

---

## 3. 프로젝트 구조

```
chatbot/
├── .env                          # 환경변수 설정
├── .env.example                  # 환경변수 예시
├── docker-compose.yml            # PostgreSQL + pgAdmin 컨테이너
├── requirements.txt              # Python 의존성
├── chatbot_local_design.md       # 이 문서
│
├── app/
│   ├── main.py                   # FastAPI 앱 진입점 (라우터 등록, 라이프사이클)
│   │
│   ├── core/
│   │   ├── config.py             # 환경변수 설정 (pydantic-settings)
│   │   ├── db.py                 # DB 엔진/세션 관리, 테이블 생성
│   │   └── auth.py               # JWT 토큰, 비밀번호 해싱, 인증 의존성
│   │
│   ├── models/
│   │   ├── document.py           # Document, Chunk (pgvector)
│   │   ├── chat.py               # ChatSession, ChatMessage
│   │   ├── document_group.py     # DocumentGroup, DocumentGroupMember
│   │   ├── admin.py              # AdminUser
│   │   ├── settings.py           # SystemSetting (key-value)
│   │   └── faq.py                # FaqTemplate
│   │
│   ├── api/
│   │   ├── health.py             # GET /api/health
│   │   ├── auth.py               # 로그인, 인증 확인, 비밀번호 변경
│   │   ├── ingest.py             # 텍스트/파일 업로드 및 임베딩
│   │   ├── chat.py               # POST /api/chat (HTTP 채팅)
│   │   ├── chat_ws.py            # WebSocket 채팅 (사용자/관리자)
│   │   ├── documents.py          # 문서 CRUD, 청크 조회, 재청킹
│   │   ├── groups.py             # 문서 그룹 관리
│   │   ├── sessions.py           # 채팅 세션 관리 (상태, 삭제, 관리자 답변)
│   │   ├── stats.py              # 대시보드 통계
│   │   ├── settings_api.py       # 시스템 설정 조회/수정
│   │   ├── search.py             # 검색 테스트
│   │   └── faq.py                # FAQ 템플릿 CRUD
│   │
│   ├── services/
│   │   ├── ingest_service.py     # 문서 적재 (청킹 → 임베딩 → 저장)
│   │   ├── embedding_service.py  # OpenAI 임베딩 생성
│   │   ├── retrieval_service.py  # 하이브리드 검색 (벡터 + 풀텍스트 + RRF)
│   │   ├── llm_service.py        # OpenAI LLM 호출
│   │   ├── chunking_service.py   # source_type별 청킹 전략
│   │   └── sql_parser_service.py # SQL → 자연어 변환
│   │
│   ├── graphs/
│   │   └── chat_graph.py         # LangGraph 워크플로우
│   │
│   └── prompts/
│       ├── answer_prompt.txt     # 답변 생성 시스템 프롬프트
│       └── rewrite_prompt.txt    # 질문 재작성 프롬프트
│
├── static/
│   ├── user/
│   │   └── index.html            # 사용자 채팅 페이지
│   └── admin/
│       ├── index.html            # 관리자 대시보드 (SPA)
│       └── login.html            # 관리자 로그인 페이지
│
└── scripts/
    └── seed_data.py              # 테스트 데이터 적재 스크립트
```

---

## 4. DB 테이블 구조

### documents
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | 자동 증가 |
| source_type | VARCHAR(50) | faq, sql, document, manual |
| title | VARCHAR(500) | 문서 제목 |
| content | TEXT | 원본 내용 |
| metadata | JSON | 추가 메타데이터 |
| created_at | TIMESTAMPTZ | 생성 시각 |

### chunks
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | 자동 증가 |
| document_id | INTEGER FK | → documents.id (CASCADE) |
| chunk_index | INTEGER | 청크 순번 |
| chunk_text | TEXT | 청크 텍스트 |
| embedding | VECTOR(1536) | pgvector 임베딩 |
| metadata | JSON | 추가 메타데이터 |
| search_vector | TSVECTOR | 풀텍스트 검색용 (GIN 인덱스) |

### chat_sessions
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | 자동 증가 |
| session_key | VARCHAR(100) UNIQUE | 세션 식별자 (UUID) |
| status | VARCHAR(20) | active / delayed / closed |
| created_at | TIMESTAMPTZ | 생성 시각 |

### chat_messages
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | 자동 증가 (메시지 순서 보장) |
| session_id | INTEGER FK | → chat_sessions.id (CASCADE) |
| role | VARCHAR(20) | user / assistant |
| message | TEXT | 메시지 내용 |
| retrieval_meta | JSON | 검색 출처, 관리자 답변 여부 등 |
| created_at | TIMESTAMPTZ | 생성 시각 |

### document_groups / document_group_members
| 테이블 | 설명 |
|--------|------|
| document_groups | 문서 그룹 (id, name, description, created_at) |
| document_group_members | 그룹-문서 매핑 (group_id, document_id) |

### admin_users
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | 자동 증가 |
| username | VARCHAR(100) UNIQUE | 로그인 ID |
| password_hash | VARCHAR(200) | bcrypt 해시 |
| name | VARCHAR(100) | 표시명 |
| role | VARCHAR(50) | admin |
| created_at | TIMESTAMPTZ | 생성 시각 |

### system_settings
| 컬럼 | 타입 | 설명 |
|------|------|------|
| key | VARCHAR(100) PK | 설정 키 |
| value | TEXT | 설정 값 |
| updated_at | TIMESTAMPTZ | 수정 시각 |

### faq_templates
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | 자동 증가 |
| title | VARCHAR(200) | FAQ 제목 |
| content | TEXT | FAQ 내용 |
| category | VARCHAR(100) | 카테고리 |
| created_at | TIMESTAMPTZ | 생성 시각 |

---

## 5. API 엔드포인트

### 인증
| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| POST | /api/auth/login | 관리자 로그인 (JWT 발급) | - |
| GET | /api/auth/me | 현재 관리자 정보 | ✅ |
| POST | /api/auth/change-password | 비밀번호 변경 | ✅ |

### 채팅
| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| POST | /api/chat | HTTP 채팅 (단건) | - |
| WS | /ws/chat/{session_key} | 사용자 WebSocket 채팅 | - |
| WS | /ws/admin/{session_key} | 관리자 WebSocket (세션 참여) | - |
| WS | /ws/admin-watch | 관리자 채팅 목록 실시간 알림 | - |

### 세션 관리
| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | /api/sessions | 세션 목록 (필터/페이징) | ✅ |
| GET | /api/sessions/{key}/messages | 세션 메시지 조회 | ✅ |
| PUT | /api/sessions/{key}/status | 상태 변경 (active/delayed/closed) | ✅ |
| DELETE | /api/sessions/{key} | 세션 삭제 | ✅ |
| POST | /api/sessions/{key}/reply | 관리자 답변 (WebSocket 브로드캐스트 포함) | ✅ |

### 문서 관리
| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| POST | /api/ingest/text | 텍스트 적재 | - |
| POST | /api/ingest/file | 파일 업로드 (.txt, .sql, .json 등) | - |
| GET | /api/documents | 문서 목록 | - |
| GET | /api/documents/{id} | 문서 상세 | - |
| PUT | /api/documents/{id} | 문서 수정 (재청킹) | - |
| DELETE | /api/documents/{id} | 문서 삭제 | - |
| POST | /api/documents/batch-delete | 일괄 삭제 | - |
| DELETE | /api/documents/all | 전체 삭제 | - |
| GET | /api/documents/{id}/chunks | 청크 조회 | - |
| POST | /api/documents/{id}/rechunk | 재청킹 | - |

### 문서 그룹
| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | /api/groups | 그룹 목록 | - |
| POST | /api/groups | 그룹 생성 | - |
| PUT | /api/groups/{id} | 그룹 수정 | - |
| DELETE | /api/groups/{id} | 그룹 삭제 | - |
| POST | /api/groups/{id}/documents | 문서 추가 | - |
| DELETE | /api/groups/{id}/documents | 문서 제거 | - |

### 기타
| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | /api/health | 서버 상태 확인 | - |
| GET | /api/stats/dashboard | 대시보드 통계 | ✅ |
| GET | /api/settings | 시스템 설정 조회 | ✅ |
| PUT | /api/settings | 시스템 설정 수정 | ✅ |
| POST | /api/search/test | 검색 테스트 | ✅ |
| GET/POST/PUT/DELETE | /api/faq | FAQ 템플릿 CRUD | ✅ |

### 페이지 라우트
| 경로 | 설명 |
|------|------|
| / | 사용자 채팅 페이지 |
| /admin | 관리자 대시보드 (SPA) |
| /admin/login | 관리자 로그인 |

---

## 6. 사용 라이브러리

### Python 패키지 (requirements.txt)

| 패키지 | 버전 | 용도 |
|--------|------|------|
| fastapi | 0.115.6 | 웹 프레임워크 (REST + WebSocket) |
| uvicorn[standard] | 0.34.0 | ASGI 서버 |
| sqlalchemy[asyncio] | 2.0.36 | ORM + 비동기 DB 접근 |
| asyncpg | 0.30.0 | PostgreSQL 비동기 드라이버 |
| pgvector | 0.3.6 | PostgreSQL 벡터 검색 확장 |
| alembic | 1.14.0 | DB 마이그레이션 |
| openai | 1.58.1 | OpenAI API 클라이언트 |
| langchain | 0.3.13 | LLM 프레임워크 (텍스트 분할 등) |
| langchain-openai | 0.3.0 | LangChain OpenAI 연동 |
| langchain-text-splitters | 0.3.3 | 텍스트 청킹 |
| langgraph | 0.2.60 | AI 워크플로우 그래프 |
| pydantic | 2.10.3 | 데이터 검증 |
| pydantic-settings | 2.7.0 | 환경변수 설정 관리 |
| python-dotenv | 1.0.1 | .env 파일 로딩 |
| python-multipart | 0.0.20 | 파일 업로드 처리 |
| httpx | 0.28.1 | 비동기 HTTP 클라이언트 |
| pyjwt | 2.9.0 | JWT 토큰 생성/검증 |
| bcrypt | 4.2.1 | 비밀번호 해싱 |

### 프론트엔드 CDN

| 라이브러리 | 용도 |
|-----------|------|
| marked.js | 마크다운 렌더링 |

### 인프라 (Docker)

| 서비스 | 이미지 | 포트 | 용도 |
|--------|--------|------|------|
| chatbot-db | pgvector/pgvector:pg16 | 5432 | PostgreSQL + pgvector |
| chatbot-pgadmin | dpage/pgadmin4 | 5050 | DB 관리 도구 |

---

## 7. 환경 설정

### .env 설정 항목

```env
# OpenAI
OPENAI_API_KEY=sk-your-key

# Database
DATABASE_URL=postgresql+asyncpg://chatbot:chatbot1234@localhost:5432/chatbot_db

# Embedding
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536

# LLM
LLM_MODEL=gpt-4o-mini

# Chunking
CHUNK_SIZE=500
CHUNK_OVERLAP=50

# JWT
JWT_SECRET=your-secret-key
JWT_EXPIRE_HOURS=24

# Admin (초기 계정)
ADMIN_DEFAULT_USERNAME=admin
ADMIN_DEFAULT_PASSWORD=admin1234
```

### 접속 정보

| 서비스 | URL | 계정 |
|--------|-----|------|
| 사용자 채팅 | http://localhost:8000 | - |
| 관리자 대시보드 | http://localhost:8000/admin | admin / admin1234 |
| pgAdmin | http://localhost:5050 | admin@admin.com / admin1234 |
| PostgreSQL | localhost:5432 | chatbot / chatbot1234 |

---

## 8. 실시간 통신 구조

### WebSocket 연결 관리

```
ConnectionManager (메모리 기반)
├── connections: {session_key → [(ws, role), ...]}
└── admin_watchers: [ws, ...]
```

### 메시지 흐름

**사용자 → AI 답변:**
```
사용자 WS → 메시지 저장 → 관리자에게 브로드캐스트 → admin-watch 알림
         → LangGraph 실행 → AI 답변 저장 → 전체 세션 브로드캐스트
```

**관리자 → 사용자:**
```
관리자 REST API (/reply) → 메시지 저장 → WebSocket 브로드캐스트 → 사용자에게 전달
```

### 관리자 채팅 목록 갱신
- WebSocket admin-watch: 새 메시지 알림 수신
- 폴링 백업 (3초 간격): API 호출로 목록 갱신
- 채팅 상세 폴링 (2초 간격): 선택된 세션 메시지 자동 갱신

### 세션 상태 관리

| 상태 | 조건 |
|------|------|
| active (상담중) | 기본 상태 |
| delayed (상담지연) | 마지막 메시지 후 24시간 경과 시 자동 전환 |
| closed (상담종료) | 관리자가 수동으로 종료 |

---

## 9. 청킹 전략

source_type에 따라 다른 청킹 설정 적용:

| source_type | chunk_size | overlap | 구분자 |
|-------------|-----------|---------|--------|
| sql | 1000 | 100 | SQL 구문 기준 (CREATE, INSERT 등) |
| document, manual | 800 | 100 | 헤더, 섹션 기준 |
| faq, 기타 | 500 (설정값) | 50 (설정값) | 기본 구분자 |

---

## 10. 프론트엔드

### 사용자 채팅 페이지 (/)
- WebSocket 기반 실시간 채팅
- 사용자 메시지(왼쪽 흰색), AI/관리자 답변(오른쪽 파란색)
- 마크다운 렌더링 (marked.js)
- 참고 문서 출처 표시 (접기/펼치기)
- 추천 질문 칩
- 세션: sessionStorage 기반 (새로고침 시 새 세션)
- 나가기 버튼 (세션 초기화)
- 모바일 반응형

### 관리자 대시보드 (/admin)
- SPA (해시 기반 라우팅)
- JWT 토큰 인증 (localStorage)
- 9개 메뉴:

| 메뉴 | 기능 |
|------|------|
| 대시보드 | 문서/청크/세션/메시지 통계 |
| 내 채팅 | 채팅 목록 실시간 갱신, 세션 상세, 관리자 답변, 상담종료/나가기 |
| 채팅 설정 | LLM 모델, temperature, top_k, max_tokens, 시스템 프롬프트 |
| 고객정보 | 세션 목록 (날짜 필터) |
| 자동응답 메시지 | 프롬프트 편집기 |
| 자주 쓰는 답변 | FAQ 템플릿 CRUD |
| 채팅방 라벨 | 문서 그룹 관리 |
| 검색 테스트 | 하이브리드 검색 테스트 |
| 데이터참고 | 문서 관리 (업로드, 수정, 삭제, 청크 뷰어) |

---

## 11. 실행 방법

### 1. Docker 컨테이너 시작
```bash
docker-compose up -d
```

### 2. Python 의존성 설치
```bash
pip install -r requirements.txt
```

### 3. 환경변수 설정
```bash
cp .env.example .env
# .env 파일에 OPENAI_API_KEY 입력
```

### 4. 서버 실행
```bash
# 개발 모드
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 운영 모드
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 12. 동시 접속 성능

현재 단일 서버 기준:

| 구간 | 현재 설정 | 동시 처리 |
|------|----------|----------|
| Uvicorn | worker 1개 (async) | ~100 연결 |
| DB 커넥션풀 | pool_size=5, max_overflow=10 | ~10 동시 쿼리 |
| OpenAI API | 제한 없음 | ~5-10 동시 호출 |
| WebSocket | 메모리 기반 | ~500 연결 |

**안정적 동시 채팅: 5~10명, 최대 ~20명**

---

## 13. 향후 개선사항

### 성능 확장
- [ ] **Uvicorn worker 수 증가** (`--workers 4`): CPU 코어 활용, 처리량 4배 향상
- [ ] **DB 커넥션풀 확대** (`pool_size=20, max_overflow=30`): 동시 DB 쿼리 50개까지 처리
- [ ] **OpenAI API 동시 호출 제한 + 큐잉**: asyncio.Semaphore로 동시 호출 수 제한, 초과 시 큐에서 대기하여 API rate limit 방지
- [ ] **Redis 기반 WebSocket**: 서버 다중화 시 Redis Pub/Sub으로 WebSocket 메시지 동기화 (현재 메모리 기반은 단일 서버만 지원)

### 기능 확장
- [ ] 멀티테넌트 지원
- [ ] 고객사 DB 연동
- [ ] OAuth 연동 (카카오/네이버)
- [ ] 파일 업로드 확장 (PDF, Excel, HWP)
- [ ] 대화 내보내기 (CSV, Excel)
- [ ] 챗봇 응답 피드백 (좋아요/싫어요)
- [ ] 관리자 알림 (이메일, 슬랙)

### 인프라
- [ ] Nginx 리버스 프록시
- [ ] HTTPS (Let's Encrypt)
- [ ] Docker 이미지화 (앱 서버 포함)
- [ ] CI/CD 파이프라인
- [ ] 로그 수집 (ELK 또는 Loki)
- [ ] 모니터링 (Prometheus + Grafana)
