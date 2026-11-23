# Integration verification (2026-11-21T12:44:41Z)

All results below come from live requests made by scripts/verify_integrations.py.

| Integration | Used for | Credential | Connectivity | Result | Category | Detail | Action |
|---|---|---|---|---|---|---|---|
| .env loading | configuration | yes | yes | **PASS** | AUTHENTICATED | loaded; blank values treated as unset: ['deepseek_api_key', 'groq_api_key'] |  |
| GitHub API | research stars (refresh-stars) | yes | yes | **PASS** | AUTHENTICATED | core limit 5000/h, remaining 5000 |  |
| Hugging Face Hub | products, orgs, papers | yes | yes | **PASS** | AUTHENTICATED | token valid (whoami-v2 HTTP 200); anonymous API HTTP 200 |  |
| Gemini | LLM tier 1 | yes | yes | **PASS** | AUTHENTICATED | model gemini-3.1-flash-lite: live minimal generation OK [transient:ProviderTimeoutError, ok] AND production extraction OK ({'pricing_model': 'FREEMIUM'}, VERIFIED); api_calls=3 |  |
| Groq | LLM fallback (Groq) | no | no | **SKIP** | MISSING_CREDENTIALS | GROQ_API_KEY empty | Optional: set GROQ_API_KEY to enable this fallback tier |
| DeepSeek | LLM fallback (DeepSeek) | no | no | **SKIP** | MISSING_CREDENTIALS | DEEPSEEK_API_KEY empty | Optional: set DEEPSEEK_API_KEY to enable this fallback tier |
| Product Hunt API | products | yes | yes | **FAIL** | AUTH_FAILED | HTTP 401: Product Hunt rejected PRODUCTHUNT_TOKEN (error code: invalid_oauth_token); the API itself is reachable. A valid Developer Token is required; no Product Hunt data can be collected until then | At api.producthunt.com/v2/oauth/applications open your app and copy the 'Developer Token' (not API Key / API Secret); if it was regenerated, update PRODUCTHUNT_TOKEN |
| Google service account | Sheets export | yes | yes | **PASS** | AUTHENTICATED | OAuth token issued for <service-account-redacted> |  |
| Google Sheets API | Sheets export | yes | yes | **PASS** | AUTHENTICATED | spreadsheet 'AI Intelligence Platform' readable; service account has Editor (canEdit=true); anyone-with-link access: off |  |
| Google Drive API | public sharing | yes | yes | **PASS** | AUTHENTICATED | enabled; SA storage limit=0 |  |
| Primary DB (sqlite) | all persistence | yes | yes | **PASS** | AUTHENTICATED | connected; records {'JOB': 93, 'NEWS': 16, 'PRODUCT': 1800, 'RESEARCH_PAPER': 2290, 'STARTUP': 2509} |  |
| Redis | distributed dedup, Celery (multi-node only) | no | no | **SKIP** | MISSING_CREDENTIALS | REDIS_URL unset -> in-process dedup (single node) | Only needed for multi-worker deployments (docker compose) |
| Qdrant | optional (documented projection; not used by the ingestion pipeline) | yes | no | **N/A** | NOT_REQUIRED | not reachable at qdrant:6333 (gaierror); hostname is the docker-compose service name | Not required to run or evaluate the pipeline; start with: docker compose --profile graph up -d |
| Neo4j | optional (documented projection; not used by the ingestion pipeline) | yes | no | **N/A** | NOT_REQUIRED | not reachable at neo4j:7687 (gaierror); hostname is the docker-compose service name | Not required to run or evaluate the pipeline; start with: docker compose --profile graph up -d |
