# RevPilot Pre-Release Verification Checklist

## 1. Environment & Setup
- [x] Clean clone executes without developer-specific paths or usernames
- [x] `.env.example` documents all supported configuration variables
- [x] No credentials or secret keys committed to Git tracking
- [x] Portable path handling with `pathlib.Path` across macOS, Linux, and Windows
- [x] Python 3.12+ compatibility verified

## 2. Server & API
- [x] FastAPI application starts cleanly on `http://localhost:8000`
- [x] Dashboard single-page application served at `/dashboard` and `/`
- [x] All 8 Control Room endpoints respond with 200 OK
- [x] OpenAPI interactive documentation available at `/docs`
- [x] Graceful 404 and 422 error handling for invalid payloads and missing entities

## 3. Financial Safety & Deterministic Guardrails
- [x] Invariant: Zero unsafe executions on fraudulent or duplicate payments (`unsafe_executions = 0`)
- [x] Invariant: Blocked actions unconditionally halt with ₹0.00 financial mutation
- [x] Atomic idempotency store prevents TOCTOU concurrency race conditions
- [x] Currency validation strictly enforces `INR`
- [x] Transaction amount validation strictly enforces positive values (`amount > 0`)

## 4. Statistical Strategy & Learning
- [x] Thompson Sampling priors initialized with independent Beta(1,1) distributions
- [x] Strict information barrier: Strategy Engine cannot access hidden simulator ground truth
- [x] Autonomous policy adaptation proven under non-stationary environmental shifts (`NO CODE CHANGE`)
- [x] 27-cell context state matrix ($9 \text{ failure classes} \times 3 \text{ value tiers}$) verified

## 5. Adversarial Testing & Chaos Suite
- [x] 10/10 Chaos Engineering scenarios verified 100% financially safe
- [x] Fault lifecycle inspection traces `FAULT` $\rightarrow$ `VALIDATION` $\rightarrow$ `DIAGNOSIS` $\rightarrow$ `STRATEGY` $\rightarrow$ `GUARDRAIL` $\rightarrow$ `EXECUTION` $\rightarrow$ `OUTCOME` $\rightarrow$ `AUDIT`
- [x] Zero cross-scenario state contamination

## 6. Test Suite & Verification
- [x] 316/316 automated tests passing cleanly (`pytest -v`)
- [x] Unit, integration, benchmark fairness, and portability suites verified
- [x] Clean-machine isolated installation test verified
