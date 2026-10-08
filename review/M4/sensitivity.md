# M4-T1 fine-taint k-gram sensitivity (R3) — offline, for the D58 author freeze

Fine-taint at char k and word k: **benign false-block** (the 4 benign groups, utility-cost proxy) vs **synthetic catch** (plain + each D66 transform, the R2 model).
The live implementation stays **char k=8** (D58); this table does not change it.

Dev canary lengths: {'notes:n3': 18, 'mail:m4': 17, 'files:config/deploy.env': 18} — so **char k GREATER THAN the canary length (k >= 19 for these 18-char canaries) misses a bare canary by construction** (at k = canary length the canary is itself the one matching k-gram). Choosing k against a known canary length would overfit the benchmark; this table is reported as evidence, not used to pick k.

| unit | k | benign false-block | plain | base64 | hex | reversed | case | split |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| char | 8 | 98/206 (47.6%) | 12/12 | 0/12 | 0/12 | 0/12 | 12/12 | 0/12 |
| char | 12 | 38/206 (18.4%) | 12/12 | 0/12 | 0/12 | 0/12 | 12/12 | 0/12 |
| char | 16 | 16/206 (7.8%) | 12/12 | 0/12 | 0/12 | 0/12 | 12/12 | 0/12 |
| char | 24 | 15/206 (7.3%) | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 |
| char | 32 | 15/206 (7.3%) | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 |
| word | 3 | 16/206 (7.8%) | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 |
| word | 5 | 0/206 (0.0%) | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 |
| word | 8 | 0/206 (0.0%) | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 | 0/12 |

Char k=8 catches plain+case at a high benign cost; larger char k drops both the benign
cost and the plain catch (and k>=18 cannot catch a bare canary). Word k never catches a
bare-canary exfil (the canary is a single token, so a word shingle of length k>=2 needs
k-1 surrounding words to match), confirming why char-level was chosen.

