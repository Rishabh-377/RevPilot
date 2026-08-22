"""
Diagnosis Agent
===============

Converts messy, unstructured raw gateway/payment error strings and failure codes
into a normalized internal taxonomy with confidence, retryability assessment,
risk level, extracted evidence, and root-cause explanations.

ARCHITECTURAL PRINCIPLES & BOUNDARIES
-------------------------------------
1. The Diagnosis Agent ONLY classifies and explains root causes.
2. It NEVER selects or authorizes a financial recovery action.
3. It NEVER overrides guardrails or modifies transaction amounts.
4. It operates on raw error strings without requiring pre-cleaned categories.
5. Low confidence (< 0.60) remains explicitly visible.
6. Unrecognized or ambiguous cases remain UNKNOWN.
7. A deterministic fallback pipeline guarantees graceful degradation if the LLM
   is unavailable, times out, or produces malformed JSON.
8. Hidden ground truth simulator data is NEVER passed to the LLM.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from backend.agents.llm_client import GeminiClient, LLMClientProtocol
from backend.config import Settings, get_settings
from backend.models.schemas import (
    DiagnosisResult,
    FailureClass,
    PaymentFailureEvent,
    RiskLevel,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strict LLM Response Contract
# ---------------------------------------------------------------------------


class LLMDiagnosisPayload(BaseModel):
    """Strict Pydantic contract for LLM semantic diagnosis output."""

    normalized_failure_class: FailureClass = Field(
        description="Normalized failure taxonomy classification"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Model confidence score between 0.0 and 1.0"
    )
    retryability: bool = Field(
        description="Whether this payment failure is operationally retryable"
    )
    risk_level: RiskLevel = Field(
        description="Assessed fraud or financial risk level"
    )
    evidence: list[str] = Field(
        default_factory=list, description="Extracted keywords, signals, or tokens"
    )
    explanation: str = Field(
        default="", description="Concise root-cause analysis and semantic interpretation"
    )


# ---------------------------------------------------------------------------
# Diagnosis System Prompt
# ---------------------------------------------------------------------------

DIAGNOSIS_SYSTEM_PROMPT = """You are RevPilot's Diagnosis Agent for payment failures in the Indian payment ecosystem.
Your ONLY role is to classify the raw payment gateway error string into the normalized failure taxonomy and provide root-cause analysis.

TAXONOMY CLASSES:
- TIMEOUT_TRANSIENT: Read/connect/upstream timeouts, network socket resets, transient connection drops.
- HARD_FUNDS_ISSUE: Insufficient balance in bank/card account, credit/daily/monthly limit exhaustion.
- ISSUER_DECLINE: Generic issuing bank decline (e.g. ISO code 05, 51, do not honour, restricted card, policy decline).
- AUTH_BLOCKED: 3DS timeout, OTP expiration, auth window expired, PIN entry failed, customer challenge rejected.
- INFRA_OUTAGE: PSP/acquirer downtime (502/503), switch maintenance, NPCI switch unavailable (U30).
- DUPLICATE: Idempotency conflict, duplicate reference/order ID already processed or captured.
- CUSTOMER_ABANDONMENT: Customer closed modal, navigated away, rejected UPI collect request in app, session expired.
- FRAUD_SUSPECTED: Velocity spikes, card-testing patterns, blacklist/watchlist triggers, high fraud risk scores.
- UNKNOWN: Garbage strings, unparseable logs, corrupted data, ambiguous error messages without clear signals.

CONSTRAINTS:
1. You may ONLY output a valid JSON object matching the exact schema.
2. You MUST NOT suggest or select any financial recovery actions.
3. You MUST NOT modify transaction amounts.
4. If the error is ambiguous or lacks diagnostic tokens, classify as UNKNOWN with low confidence (< 0.50).

OUTPUT JSON SCHEMA:
{
  "normalized_failure_class": "TIMEOUT_TRANSIENT | HARD_FUNDS_ISSUE | ISSUER_DECLINE | AUTH_BLOCKED | INFRA_OUTAGE | DUPLICATE | CUSTOMER_ABANDONMENT | FRAUD_SUSPECTED | UNKNOWN",
  "confidence": float (0.0 to 1.0),
  "retryability": bool,
  "risk_level": "LOW | MEDIUM | HIGH | CRITICAL",
  "evidence": [string],
  "explanation": string
}
"""


# ---------------------------------------------------------------------------
# Deterministic Classification Rules (Fallback & Baseline)
# ---------------------------------------------------------------------------

# Pattern groups ordered by diagnostic specificity.
# Fraud and duplicate checks run with high priority to prevent financial leakage.
_PATTERNS: list[tuple[FailureClass, list[re.Pattern[str]], bool, RiskLevel, float, str]] = [
    # 1. FRAUD_SUSPECTED
    (
        FailureClass.FRAUD_SUSPECTED,
        [
            re.compile(r"fraud", re.I),
            re.compile(r"velocity\s*(?:check|spike|count)", re.I),
            re.compile(r"card[-_ ]testing", re.I),
            re.compile(r"(?:on|in)\s*watchlist", re.I),
            re.compile(r"blacklist(?:ed)?", re.I),
            re.compile(r"suspicious(?:\s+activity)?", re.I),
            re.compile(r"risk\s*score\s*(?:exceeded|>|0\.[89])", re.I),
            re.compile(r"device\s*fingerprint\s*mismatch", re.I),
        ],
        False,
        RiskLevel.CRITICAL,
        0.95,
        "Transaction flagged by risk/fraud detection engine.",
    ),
    # 2. DUPLICATE
    (
        FailureClass.DUPLICATE,
        [
            re.compile(r"duplicate\s*(?:reference|order|transaction|ref|request)", re.I),
            re.compile(r"already\s*(?:exists|processed|captured|settled)", re.I),
            re.compile(r"idempotency(?:\s*key)?\s*conflict", re.I),
            re.compile(r"transaction\s*id\s*reuse", re.I),
        ],
        False,
        RiskLevel.HIGH,
        0.95,
        "Duplicate transaction or idempotency conflict detected.",
    ),
    # 3. INFRA_OUTAGE
    (
        FailureClass.INFRA_OUTAGE,
        [
            re.compile(r"psp\s*unavailable", re.I),
            re.compile(r"npci\s*(?:switch|down|unavailable|u30)", re.I),
            re.compile(r"502\s*bad\s*gateway", re.I),
            re.compile(r"503\s*(?:service|downstream)?\s*unavailable", re.I),
            re.compile(r"(?:gateway|switch|host)\s*(?:down|maintenance|unreachable)", re.I),
            re.compile(r"connection\s*refused", re.I),
            re.compile(r"acquirer\s*host\s*not\s*responding", re.I),
            re.compile(r"network\s*partition", re.I),
        ],
        True,
        RiskLevel.LOW,
        0.90,
        "Downstream infrastructure or central switch outage.",
    ),
    # 4. HARD_FUNDS_ISSUE
    (
        FailureClass.HARD_FUNDS_ISSUE,
        [
            re.compile(r"code\s*51", re.I),
            re.compile(r"insufficient\s*(?:funds|balance)", re.I),
            re.compile(r"not\s*sufficient\s*funds", re.I),
            re.compile(r"balance\s*too\s*low", re.I),
            re.compile(r"credit\s*limit\s*exhausted", re.I),
            re.compile(r"spend\s*limit\s*reached", re.I),
            re.compile(r"limit\s*exhausted", re.I),
            re.compile(r"available\s*credit\s*limit", re.I),
        ],
        True,
        RiskLevel.LOW,
        0.92,
        "Customer account has insufficient funds or exceeded limits.",
    ),
    # 5. AUTH_BLOCKED
    (
        FailureClass.AUTH_BLOCKED,
        [
            re.compile(r"auth\s*window\s*expired", re.I),
            re.compile(r"collect\s*request\s*expired", re.I),
            re.compile(r"otp\s*(?:not\s*entered|not\s*submitted|expired|incorrect)", re.I),
            re.compile(r"3ds\s*authentication\s*failed", re.I),
            re.compile(r"3ds|3d\s*secure", re.I),
            re.compile(r"vbv|verified\s*by\s*visa", re.I),
            re.compile(r"pin\s*entry\s*abandoned", re.I),
            re.compile(r"step-up\s*challenge", re.I),
        ],
        True,
        RiskLevel.LOW,
        0.88,
        "Cardholder authentication or OTP verification blocked or timed out.",
    ),
    # 6. CUSTOMER_ABANDONMENT
    (
        FailureClass.CUSTOMER_ABANDONMENT,
        [
            re.compile(r"payment\s*abandoned\s*by\s*user", re.I),
            re.compile(r"closed\s*(?:the\s*)?(?:checkout|payment|modal|page)", re.I),
            re.compile(r"navigated\s*away", re.I),
            re.compile(r"upi\s*collect\s*request\s*rejected", re.I),
            re.compile(r"rejected\s*by\s*customer", re.I),
            re.compile(r"session\s*expired", re.I),
            re.compile(r"declined\s*intent", re.I),
        ],
        True,
        RiskLevel.LOW,
        0.88,
        "Customer abandoned checkout or explicitly rejected payment intent.",
    ),
    # 7. TIMEOUT_TRANSIENT
    (
        FailureClass.TIMEOUT_TRANSIENT,
        [
            re.compile(r"timed?\s*out", re.I),
            re.compile(r"timeout", re.I),
            re.compile(r"no\s*data\s*received", re.I),
            re.compile(r"connection\s*reset", re.I),
            re.compile(r"socket\s*closed", re.I),
            re.compile(r"response\s*delayed", re.I),
        ],
        True,
        RiskLevel.LOW,
        0.85,
        "Transient network latency or upstream gateway read timeout.",
    ),
    # 8. ISSUER_DECLINE
    (
        FailureClass.ISSUER_DECLINE,
        [
            re.compile(r"code\s*05", re.I),
            re.compile(r"do\s*not\s*honou?r", re.I),
            re.compile(r"issuer\s*declined", re.I),
            re.compile(r"declined\s*by\s*(?:issuing\s*)?bank", re.I),
            re.compile(r"transaction\s*not\s*permitted", re.I),
            re.compile(r"restricted\s*card", re.I),
            re.compile(r"card\s*blocked\s*by\s*issuer", re.I),
            re.compile(r"lost\s*or\s*stolen", re.I),
            re.compile(r"decline", re.I),
        ],
        True,
        RiskLevel.MEDIUM,
        0.85,
        "Issuing bank declined transaction without specific operational fault.",
    ),
]


class DiagnosisAgent:
    """Intelligent payment failure root-cause diagnosis agent.

    Uses Gemini LLM for semantic classification when configured and available,
    with an automated deterministic fallback pipeline guaranteeing graceful degradation.
    """

    def __init__(
        self,
        use_llm: Optional[bool] = None,
        llm_client: Optional[Any] = None,
        config: Optional[Settings] = None,
    ) -> None:
        cfg = config or get_settings()
        self.config = cfg

        if use_llm is not None:
            self.use_llm = use_llm
        else:
            env_enabled = os.environ.get("REVPILOT_LLM_ENABLED", "").lower() in ("true", "1", "yes")
            self.use_llm = cfg.llm_enabled or env_enabled

        if llm_client is not None:
            self.llm_client = llm_client
        elif self.use_llm:
            api_key = (
                cfg.gemini_api_key
                or os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
                or os.environ.get("REVPILOT_GEMINI_API_KEY")
            )
            self.llm_client = GeminiClient(
                api_key=api_key,
                model=cfg.llm_model,
                timeout_seconds=cfg.llm_timeout_seconds,
                max_retries=cfg.llm_max_retries,
            )
        else:
            self.llm_client = None

    async def diagnose(self, event: PaymentFailureEvent) -> DiagnosisResult:
        """Run diagnosis on a PaymentFailureEvent."""
        raw_error = event.raw_gateway_error or event.failure_code or ""
        return self.diagnose_raw(
            raw_error=raw_error,
            event_id=event.event_id,
            metadata=event.metadata,
        )

    def diagnose_sync(self, event: PaymentFailureEvent) -> DiagnosisResult:
        """Synchronous convenience wrapper for diagnose."""
        raw_error = event.raw_gateway_error or event.failure_code or ""
        return self.diagnose_raw(
            raw_error=raw_error,
            event_id=event.event_id,
            metadata=event.metadata,
        )

    def diagnose_raw(
        self,
        raw_error: str,
        event_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> DiagnosisResult:
        """Diagnose a raw gateway error string directly.

        Parameters
        ----------
        raw_error:
            Unprocessed error string from gateway.
        event_id:
            Optional event identifier for trace linkage.
        metadata:
            Optional contextual metadata.

        Returns
        -------
        DiagnosisResult
        """
        import time

        t_start = time.perf_counter()
        evt_id = event_id or str(uuid.uuid4())

        # If LLM execution is enabled and client provided, attempt LLM semantic classification
        if self.use_llm and self.llm_client:
            is_ready = getattr(self.llm_client, "is_configured", True)
            if is_ready:
                try:
                    llm_result = self._call_llm_diagnosis(raw_error, evt_id, metadata, t_start)
                    if llm_result:
                        return llm_result
                except Exception as e:
                    latency_ms = (time.perf_counter() - t_start) * 1000.0
                    logger.warning(
                        f"LLM diagnosis failed ({e}), falling back to deterministic classifier."
                    )
                    return self._deterministic_classify(
                        raw_error=raw_error,
                        event_id=evt_id,
                        metadata=metadata,
                        diagnosis_source="DETERMINISTIC_FALLBACK",
                        fallback_reason=str(e),
                        latency_ms=latency_ms,
                    )
            else:
                logger.debug("LLM client configured but not ready (e.g. missing API key), using fallback.")
                latency_ms = (time.perf_counter() - t_start) * 1000.0
                return self._deterministic_classify(
                    raw_error=raw_error,
                    event_id=evt_id,
                    metadata=metadata,
                    diagnosis_source="DETERMINISTIC_FALLBACK",
                    fallback_reason="API key not configured",
                    latency_ms=latency_ms,
                )

        # Deterministic baseline pipeline
        latency_ms = (time.perf_counter() - t_start) * 1000.0
        return self._deterministic_classify(
            raw_error=raw_error,
            event_id=evt_id,
            metadata=metadata,
            diagnosis_source="DETERMINISTIC_RULE",
            latency_ms=latency_ms,
        )

    @staticmethod
    def _extract_json_payload(text: str) -> str:
        """Extract and clean raw JSON string from model response."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        if not cleaned.startswith("{"):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                cleaned = cleaned[start : end + 1]

        return cleaned

    def _call_llm_diagnosis(
        self,
        raw_error: str,
        event_id: str,
        metadata: Optional[dict[str, Any]] = None,
        t_start: Optional[float] = None,
    ) -> Optional[DiagnosisResult]:
        """Invoke LLM and strictly validate output schema via Pydantic."""
        import time

        clean_input = raw_error.strip()
        if not clean_input:
            return None

        # Build clean prompt with sanitized raw error — NEVER pass hidden ground truth or probabilities
        prompt = (
            f"Raw Payment Gateway Error to Diagnose:\n"
            f"```\n{clean_input}\n```\n\n"
            f"Analyze the error, classify it into the normalized taxonomy, and respond ONLY with a JSON object matching the schema."
        )

        response_text = self.llm_client.generate(
            prompt=prompt,
            system_prompt=DIAGNOSIS_SYSTEM_PROMPT,
        )
        if not response_text:
            return None

        cleaned_json = self._extract_json_payload(response_text)

        # Strict validation through Pydantic model
        payload = LLMDiagnosisPayload.model_validate_json(cleaned_json)

        latency_ms = (time.perf_counter() - (t_start or time.perf_counter())) * 1000.0
        model_name = getattr(self.llm_client, "model", "gemini-2.5-flash")

        return DiagnosisResult(
            event_id=event_id,
            normalized_failure_class=payload.normalized_failure_class,
            confidence=payload.confidence,
            retryability=payload.retryability,
            risk_level=payload.risk_level,
            evidence=payload.evidence or [f"llm_extracted:{clean_input[:30]}"],
            explanation=payload.explanation or f"LLM semantic classification: {payload.normalized_failure_class.value}",
            diagnosis_source="LLM",
            model_provider=model_name,
            fallback_reason=None,
            latency_ms=latency_ms,
            engine="llm_gemini",
            context_signals=metadata or {},
        )

    def _deterministic_classify(
        self,
        raw_error: str,
        event_id: str,
        metadata: Optional[dict[str, Any]] = None,
        diagnosis_source: str = "DETERMINISTIC_RULE",
        fallback_reason: Optional[str] = None,
        latency_ms: float = 0.0,
    ) -> DiagnosisResult:
        """Robust deterministic rule-based classifier with evidence extraction."""
        clean_text = raw_error.strip()

        if not clean_text or len(clean_text) < 3:
            return DiagnosisResult(
                event_id=event_id,
                normalized_failure_class=FailureClass.UNKNOWN,
                confidence=0.10,
                retryability=False,
                risk_level=RiskLevel.MEDIUM,
                evidence=["empty_or_too_short"],
                explanation="Error message contains no diagnostic information.",
                diagnosis_source=diagnosis_source,
                fallback_reason=fallback_reason,
                latency_ms=latency_ms,
                engine=diagnosis_source.lower(),
                context_signals=metadata or {},
            )

        matched_evidence: list[str] = []

        for failure_class, patterns, retryable, risk, base_conf, explanation in _PATTERNS:
            for pattern in patterns:
                match = pattern.search(clean_text)
                if match:
                    matched_evidence.append(match.group(0))

            if matched_evidence:
                return DiagnosisResult(
                    event_id=event_id,
                    normalized_failure_class=failure_class,
                    confidence=base_conf,
                    retryability=retryable,
                    risk_level=risk,
                    evidence=matched_evidence,
                    explanation=f"{explanation} (matched: {', '.join(matched_evidence)})",
                    diagnosis_source=diagnosis_source,
                    fallback_reason=fallback_reason,
                    latency_ms=latency_ms,
                    engine=diagnosis_source.lower(),
                    context_signals=metadata or {},
                )

        # No pattern matched -> UNKNOWN with low confidence
        return DiagnosisResult(
            event_id=event_id,
            normalized_failure_class=FailureClass.UNKNOWN,
            confidence=0.25,
            retryability=False,
            risk_level=RiskLevel.MEDIUM,
            evidence=["no_taxonomy_pattern_match"],
            explanation=f"Unrecognized error payload: '{clean_text[:50]}...'",
            diagnosis_source=diagnosis_source,
            fallback_reason=fallback_reason,
            latency_ms=latency_ms,
            engine=diagnosis_source.lower(),
            context_signals=metadata or {},
        )

