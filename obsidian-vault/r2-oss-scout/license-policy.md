# R2 License Policy

Last updated: 2026-06-03
Maintained by: R2 OSS Scout (Agent #12)

## Approved Licenses (component / full_module / pattern safe)

| License | SPDX ID | Notes |
|---------|---------|-------|
| MIT | MIT | Preferred. No attribution requirement at runtime. |
| Apache 2.0 | Apache-2.0 | Preferred for enterprise libs. Patent clause is protective. |
| BSD 2-Clause | BSD-2-Clause | Equivalent to MIT for practical purposes. |
| BSD 3-Clause | BSD-3-Clause | Adds non-endorsement clause. Fully safe. |
| ISC | ISC | MIT-equivalent. Common in Node ecosystem. |
| Mozilla Public License 2.0 | MPL-2.0 | File-level copyleft only. Safe for dependencies. |
| CC0 / Unlicense | CC0-1.0 / Unlicense | Public domain. No restrictions whatsoever. |
| Python Software Foundation | PSF-2.0 | Safe for stdlib-derived code. |

## Risky Licenses (reference_only only)

| License | SPDX ID | Why Risky | Forced Integration Type |
|---------|---------|-----------|------------------------|
| GNU GPL v2 | GPL-2.0 | Strong copyleft — any linked binary must be GPL. | `reference_only` |
| GNU GPL v3 | GPL-3.0 | Same as v2 plus anti-tivoization. | `reference_only` |
| GNU AGPL v3 | AGPL-3.0 | Network copyleft — SaaS use triggers share-alike. CRITICAL for Omerion. | `reference_only` |
| Server Side Public License | SSPL-1.0 | MongoDB's AGPL variant; entire stack must be SSPL if used as service. | `reference_only` |
| Commons Clause | varies | Additional commercial restriction layered on OSS license. | `reference_only` |
| Business Source License | BUSL-1.1 | Commercial use restricted until conversion date. Time-bomb risk. | `reference_only` |

## Edge Cases

| Scenario | Rule |
|----------|------|
| License field is `null` or `NOASSERTION` | Treat as `risk >= 0.7`. Require Sonnet escalation. Mark `integration_type = reference_only`. |
| Dual-licensed (e.g. MIT + GPL) | Use the permissive option (MIT). Document in recommendation field. |
| CC-BY-4.0 (content license applied to code) | Safe for reference_only. Flag in recommendation: "content license, not code license." |
| Custom / proprietary | `risk = 1.0`. Always `reference_only`. Never `component` or `full_module`. |

## R2 Enforcement Rules

1. Haiku sets `risk >= 0.8` automatically for AGPL-3.0, GPL-2.0, GPL-3.0, SSPL-1.0 regardless of analysis.
2. If `risk >= 0.8` AND `fit >= 0.7` → repo IS written to Supabase with `integration_type = reference_only`. Let R3 decide on architectural fit.
3. If `risk >= 0.8` AND `fit < 0.7` → repo is DROPPED before persist. Not worth indexing.
4. `full_module` integration type is PROHIBITED for any risky license, period.

## Audit Trail

License field is read from the GitHub API `license.spdx_id` field. If the GitHub API returns `null`, R2 reads the first 200 characters of the LICENSE file from the README excerpt as a fallback heuristic. If still ambiguous, the repo is flagged with `risk = 0.7` and `integration_type = reference_only`.
