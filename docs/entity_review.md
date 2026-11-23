# Entity resolution — human review queue and manual-check merges

Reviewed 2026-11-21 against the live database. **No mapping was auto-approved.** Every REVIEW case is stored as its own
entity, the safe default (no merge), until a human decides. "Assessment" is a recommendation, not an applied change.

## REVIEW queue (12) — kept separate, pending a human decision

| Raw name | Closest existing entity | Why flagged | Evidence | Assessment |
|---|---|---|---|---|
| Cohere (YC `/companies/cohere`) | Cohere (seed) | domain_conflict | cohere.io vs cohere.com | Different companies — keep separate |
| Lantern AI (YC) | Lantern | domain_conflict | lantern.md vs other domain | Different companies — keep separate |
| Starling (YC) | StarSling | domain_conflict | starlingmedical.com | Different companies — keep separate |
| Caire Health (YC) | Cair Health | domain_conflict | cairehealthinc.com | Different companies — keep separate |
| Palomma (YC) | Paloma | domain_conflict | palomma.com | Different companies — keep separate |
| Moonshot (YC `moonshot-computer`) | Moonshot AI (seed, Kimi) | domain_conflict | moonshot.computer vs moonshot.ai | Different companies — keep separate |
| Conntour (YC) | Contour | single-word brand + domain | conntour.com | Different companies — keep separate (was a wrong merge before the fix) |
| Datasite (Himalayas job) | DataSuite (YC) | single-word brand mismatch | job board company page | Different companies — keep separate (was a wrong merge before the fix) |
| Hugging Face H4 (HF org) | Hugging Face | distinct token "h4" | HF team org | **Uncertain**: a team inside Hugging Face. Merge only if teams should roll up to parents |
| bytedance-research (HF org) | ByteDance | fuzzy 90 / semantic 0.67 | HF org handle | **Uncertain**: likely a ByteDance research unit; same roll-up policy question |
| Sweep AI (HF org `sweepai`) | Sweep (YC) | fuzzy 90 / semantic 0.72 | no shared domain | **Uncertain**: needs a website check |
| Miso Labs (HF org) | Miso (YC) | fuzzy 90 / semantic 0.62 | no shared domain | **Uncertain**: needs a website check |

## "Manual-check" merges (51 rows = 3 distinct pairs + 2 fuzzy)

The audit lists merges whose raw and canonical names share no text. All are curated seed aliases:

| Raw → canonical | Rows | Verdict |
|---|---:|---|
| Z.ai → Zhipu AI | 40 | Correct: Z.ai is Zhipu AI's current brand (HF org `zai-org`) |
| Ai2 → Allen Institute for AI | 7 | Correct: official short name |
| PaddlePaddle → Baidu | 4 | Correct: Baidu's framework organisation |

Fuzzy merges in the live log: `Unsloth → Unsloth AI` (same company, generic suffix) and `TraceRoot.AI → traceroot` (same company). Both correct.

**Incorrect merges currently in the data: 0 known.** The 2 historical wrong merges (Conntour, Datasite) were reversed.
To apply a decision for an uncertain case, add the alias to `src/entity_resolution/seed.py` (or keep separate), then run
`python -m src.main rebuild-entities`.
