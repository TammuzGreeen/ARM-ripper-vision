# DVD/CD field-risk benchmark

This benchmark is additive and independent of the direct-recognition and two-stage pipeline reports. It evaluates silent wrong recognitions at the field level. It must not mutate either existing workflow or its retained evidence.

## Permanent report families

1. Direct recognition: image → one model → immutable raw transcription.
2. Two-stage pipeline: retain the recognizer, routing, optional fallback, evaluator, and structured output as a separate benchmark.
3. Field-risk/precision: score each verified visible field against independently maintained ground truth.

All reports and raw artifacts are additive. Use exclusive creation for new request/response/evidence names and refuse to overwrite an existing report. Preserve originals and the established 1024-pixel JPEG-quality-92 inference copies. Recognition inputs must not include user disc notes or the ground-truth file.

## Field classifications

For every verified visible field, assign exactly one primary class: `CORRECT`, `OMITTED`, `DETECTABLY_FAILED`, `WRONG_BUT_PLAUSIBLE`, or `HALLUCINATED`. `PARTIALLY_CORRECT` and `UNCERTAIN_GROUND_TRUTH` are explicit non-primary annotations and must not be silently folded into a primary class. A run-level detectable failure (including output cap) marks the affected scorable fields as `DETECTABLY_FAILED`; retain its partial raw output separately. Count unsupported assertions as hallucinations at output level and retain each assertion and evidence rationale.

Ground truth must be manually verified from retained images or explicit user facts. Never use model output to set truth. Store per-field evidence, criticality, source image/event IDs, and uncertainty in a separate versioned JSON file. Do not add fields just because a model emitted them.

## Metrics and comparison

Report counts and denominators before rates. Include overall and critical-only correct, omitted, detectable-failure, plausible-wrong, and hallucinated measures; silent wrong among returned claims; runtime; and camera aggregates. Keep the direct and pipeline summaries separate. For promising model pairs, compare field-by-field agreement, agreement-and-correct, agreement-and-wrong, disagreement, and mistake-catching. Rank first by plausible-wrong rate, then hallucinations, correctness, omissions, detectable failures, and runtime. Do not penalize detectable failures as if they were silent errors.

Do not tune prompts, field definitions, validation, or routing to the current four discs. This set is a small development sample; revisit decisions when new CDs/DVDs are added.

## Model identity and execution

Before inference, save the requested tag, resolved local tag, immutable digest, parameter count, quantization, model size, Ollama version, and exact settings. Missing requested tags are failures to resolve, not permission to substitute. Record a closest equivalent only after reporting the missing exact tag and keep it clearly labeled as a distinct model. Run sequentially and without a benchmark timeout; record long runs, caps, transport errors, and partial output instead of discarding them.

The additive direct runner is `tools/run_direct_dvd_benchmark.py`. It performs an exact-tag preflight before inference, reuses only the prior five valid-size runs with matching prompt and model digest, and saves new raw streams under exclusive filenames. If interrupted, resume the same new report with `--resume`; never remove prior evidence to make a retry fit. After all available models have eight image records, run `tools/score_field_risk.py` to produce the separate field report. The scorer refuses incomplete model/image batches.

For disc-identification model ranking, use `tools/score_priority_disc_identification.py` with the versioned priority ground truth. Only user-established series, season, episode-range, and explicitly supplied title facts decide this ranking; the broader `score_field_risk.py` output is secondary OCR evidence and must not be used to offset priority-field mistakes. Save each run as a new report revision. Non-exact priority matches remain visible for review, and peripheral text absent from a disc note is not by itself a hallucination.

Keep operational reliability separate from recognition quality. A run is usable for recognition precision only after HTTP success, a nonempty transcription, and natural `done_reason=stop`; loader/runtime failures count toward operational failures but not precision, silent-wrong, omission, or correct-field denominators. If there are zero usable runs, recognition metrics are `N/A`. The Llama correction runner writes immutable `diagnostic-disc-llama32-vision-correction-vN.json` snapshots; resume publishes a new revision instead of replacing a prior report. Do not run the seven remaining images until the first-image smoke report explicitly passes.

## Current diagnostic data

The retained ground truth is `diagnostic-disc-ground-truth-v1.1.json` in the external queue-state evidence directory. `diagnostic-disc-ground-truth-v1.json` is retained unchanged as its initial annotation. The current revision covers only confidently legible fields across eight existing daylight-plus-room-light images. It is a starter annotation and should be reviewed/extended as more verified images are enrolled. `diagnostic-disc-model-preflight-v1.json` records the first local exact-tag check and Ollama version; recheck all tags after any pull attempt before inference.
