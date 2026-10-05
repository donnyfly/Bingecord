# WeTrakr API review — 2026-10-05

## Evidence and access limits

The last directly reviewed API documentation baseline in this repository is 1.0.3 beta, dated 2026-09-27. The request header remains `wetrakr-api-version: 1`; documentation release numbers are not assumed to be new API header versions.

Official changelog: https://api.wetrakr.com/#/changelog

The documentation could not be read in this review: web retrieval reported an inaccessible URL, a direct request to the official documentation root returned HTTP 403, and the candidate documentation Markdown resources timed out. No unofficial mirror was accepted as authoritative. The latest release number and detailed breaking changelog entries therefore remain **unverified**. Paste/export the recent official changelog and the affected endpoint documentation to complete that part of the review.

Developer announcements reviewed:

- September 27: https://www.reddit.com/r/WeTrakr/comments/1wrgiwg/wetrakr_api_first_days_after_launch/ — developer says beta endpoints are changing frequently and specifically asks integrators to check breaking changelog entries.
- October 1: https://www.reddit.com/r/WeTrakr/comments/1wuugw6/server_upgrade/ — developer reports an API server upgrade and explicitly says no client action is required. This announcement is not evidence of changed payloads or pagination.

## Changes made from code validation

These are defensive fixes discovered in the bot, **not claimed adaptations to an unread changelog**:

- Unknown history/list envelopes and non-object rows raise `INVALID_RESPONSE`; they cannot masquerade as empty histories or silently wipe imported watches during a full reconciliation.
- Full baseline imports reject entries without recognized stable play identities before updating saved watches or sync checkpoints.
- Repeated cursor cycles are rejected, including cycles longer than one page.
- Journal page counts are validated; missing journal envelopes cannot silently advance sync.
- Transient 500/502/503/504 responses retry before JSON decoding, covering HTML gateway errors. Quota responses remain surfaced without automatic quota retries.
- The read-only live validator can cap journal/history/list reads and produce a credential-free account report. It does not rotate tokens, publish Discord activity or apply data changes.

## Still to verify against the official changelog

Authentication and refresh-token field names; journal field names, ordering, retention and lag guarantees; full-history/list envelope and cursor headers; compact play IDs and unknown watch dates; title/episode metadata, external IDs and calendar-season numbering; rate-limit/quota behavior. Do not change these contracts based on guesses. Run the host-side report in `docs/testing-multi-tracker.md` and the interactive watch/switch/removal sequence before marking live parity complete.
