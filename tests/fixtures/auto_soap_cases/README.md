# Auto-SOAP Regression Fixtures

The current fixture coverage is implemented in `tests/test_auto_soap_contract.py`.

Required behavioral contracts:

- External LLM disabled must create blocked LLM normalization JSON/audit without creating `llm_normalized_transcript.md`.
- The skeleton fact extractor must not auto-promote transcript text into clinical facts.
- SOAP drafting must block when there are no eligible evidence-grounded facts.
- SOAP validation must block when SOAP lacks source fact coverage.
- Default rollout policy must block auto-finalization even when source fact coverage exists.

Future fixture cases:

- Low-risk follow-up that can pass after policy is explicitly enabled.
- Medication uncertainty must block auto-finalization.
- Dose uncertainty must block auto-finalization.
- Hallucinated lab value must fail or block.
- Very short transcript must block or produce minimal draft only.
