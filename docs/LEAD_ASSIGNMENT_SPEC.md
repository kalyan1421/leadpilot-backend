# LeadPilot — Lead Assignment & Role-Scoped Visibility Spec

**Status:** Approved — all open questions resolved, implementation in progress
**Owner:** AK
**Source files touched:** `app/api/calls.py` (`get_inbox`, `get_lead_detail`), `app/models.py` (`Lead`, `User`, `Organization`), `leadpilot-web/apps/founder`, `lead_pilot_flutter`

---

## Problem Statement

`GET /api/inbox` and `GET /api/leads/{contact_key}` (`app/api/calls.py:1274`, `app/api/calls.py:1550`) currently scope leads by `org_id` only. Every telecaller who logs into an org sees **every lead in that org**, not just their own — even though `Lead.assigned_to` (`app/models.py:220`) already exists in the schema and is unused by these endpoints. This was surfaced while debugging a report of "leads showing in a different telecaller's account": part of that report was a frontend cache bug (fixed separately — logout wasn't clearing local Flutter state), but the deeper issue is that the backend never enforced per-telecaller ownership in the first place.

For a sales floor, this means telecallers can see (and potentially call) leads that were never assigned to them — duplicate outreach, confused ownership, no accountability for who's working what, and no way for a founder to load-balance leads across a team.

## Goals

1. Every lead is assigned to exactly one telecaller automatically, at the moment it's created — no manual triage step required.
2. A telecaller's inbox (`GET /api/inbox`) and lead detail view (`GET /api/leads/{contact_key}`) show **only leads assigned to them**.
3. Founder (and other non-telecaller roles, pending the open question below) continue to see **all leads in the org**, unfiltered — today's behavior, preserved for those roles.
4. Existing leads (currently `assigned_to = NULL` for almost all of them) are backfilled with an owner **before** the scoping filter goes live, so no telecaller's inbox goes from "everything" to "empty" on rollout day.
5. Rollout is per-org and reversible — one client's launch shouldn't be able to break another client's org.

## Non-Goals (v1)

- **Telecaller self-claim of unassigned leads.** Confirmed with the client: assignment is automatic/random at creation time, not a claimable pool. If a "claim" model is wanted later, it's a v2 addition, not part of this spec.
- **Weighted/skill-based assignment** (by lead source, language, telecaller performance, territory, etc.). v1 is uniform random/round-robin among active telecallers. Weighting is a natural fast-follow but adds a config surface this spec doesn't define.
- **Founder-initiated bulk reassignment UI/tooling** (e.g. drag-and-drop rebalancing across a whole team). A single-lead reassign action is in scope (P1, below); bulk tooling is not.
- **Campaigns.** Explicitly excluded from the follow-up audit (client decision) — not touched by this spec at all, even at the planning level.
- **Implementing** fixes for insights/team-status/kanban in this pass. They're in scope for the *audit + plan* (see the new "Follow-Up Audit" section below), but actual code changes for those endpoints are a separate, later effort.
- **Cross-org lead pooling or transfer.** A lead is always scoped to the org it was created in; assignment only ever picks among that org's telecallers.

## User Stories

- As a **telecaller**, I want my inbox to show only leads assigned to me, so I'm not confused about which leads are actually mine to work and don't duplicate outreach with a teammate.
- As a **telecaller**, I want every new lead that comes in for my org to automatically land with someone on the team (possibly me) the moment it arrives, so no lead sits untouched waiting for a founder to triage it.
- As a **founder**, I want to see every lead across my whole org regardless of who it's assigned to, so I retain full oversight and can spot a lead that isn't being worked.
- As a **founder**, I want to manually reassign a lead from one telecaller to another (e.g. someone's on leave, or a lead needs a specific rep), so ownership doesn't get stuck when circumstances change.
- As a **founder rolling out this change**, I want existing leads to already have a sensible owner before my telecallers' inboxes get filtered, so the change doesn't look like data loss on day one.

## Requirements

### Must-Have (P0)

**P0-1 — Auto-assignment on lead creation**
- When a new lead is created (contact_key first seen for an org), assign it to a telecaller immediately via **round-robin** selection among that org's **active telecallers** (`User.role == 'telecaller' AND User.is_active == True`, `User.org_id == lead.org_id`). Implemented as *least-loaded selection* — pick the active telecaller with the fewest currently-assigned leads, tie-broken by `User.id` ascending — rather than a persisted "next up" cursor. This gives round-robin's even-distribution guarantee without a cursor that can drift out of sync if telecallers are added/removed/deactivated.
- **Important implementation detail found while scoping this:** the two existing lead-creation paths (`create_lead` at `app/api/calls.py:1492` and `upload_call` at `app/api/calls.py:1950`) already stamp `Lead.assigned_to = current_user.id` — but unconditionally, regardless of the creator's role. Today that's harmless (org-wide visibility means it doesn't matter who "owns" a lead), but once P0-2 scoping is live, a founder or ad_manager creating/uploading a lead on behalf of the team would incorrectly become that lead's *telecaller* owner — making it invisible to every actual telecaller. Both call sites need a role branch: `current_user.role == 'telecaller'` → keep stamping `current_user.id` (self-assign, as today); any other role → call the new round-robin picker instead. `AudioCall.telecaller_id` (a different field — "who placed/uploaded this specific call") is unaffected and keeps stamping `current_user.id` unconditionally.
- Acceptance criteria:
  - [ ] Given an org with ≥1 active telecaller, when a new lead arrives, then `Lead.assigned_to` is set to one of those telecallers' `id` before the lead is ever returned by any read endpoint.
  - [ ] Given a telecaller creates/uploads a lead themselves, they remain the owner (self-assignment unchanged from today).
  - [ ] Given a founder or ad_manager creates/uploads a lead, it is round-robin assigned to an active telecaller — never to the founder/ad_manager themselves.
  - [ ] Given an org with multiple active telecallers, assignment distribution stays roughly even over time (least-loaded selection).
  - [ ] Given an org with **zero** active telecallers, the lead is created with `assigned_to = NULL` and is visible only to founder/ad_manager until a telecaller becomes active.

**P0-2 — Backend scoping filter**
- `get_inbox` and `get_lead_detail` (`app/api/calls.py:1274`, `:1550`) add a role check: telecallers get `Lead.assigned_to == current_user.id` added to the existing `Lead.org_id == current_user.org_id` filter; founder and ad_manager keep the org-wide filter unchanged (see RBAC table below).
- **Call-only entries** (get_inbox's fallback path #2 — calls that were analyzed but never got a `Lead` row, e.g. legacy data) have no `assigned_to` to filter on. `_all_analyses_by_contact` (`app/api/calls.py:1202`) already accepts a `telecaller_id` param (currently only used by the mobile Score tab) — when scoping is active, pass `current_user.id` through it so these entries get scoped by `AudioCall.telecaller_id` instead. Same applies inside `get_lead_detail`'s history lookup.
- Acceptance criteria:
  - [ ] Given a telecaller with 5 assigned leads in an org of 50 total leads, `GET /api/inbox` returns exactly those 5 (plus any call-only entries attributed to their `telecaller_id`).
  - [ ] Given a founder or ad_manager in the same org, `GET /api/inbox` returns all 50.
  - [ ] Given a telecaller requests `GET /api/leads/{contact_key}` for a lead assigned to a different telecaller, the request is rejected with **404** (not 403 — avoid confirming the lead exists to someone who shouldn't see it).

**P0-3 — Backfill migration**
- One-off script: for every existing lead with `assigned_to IS NULL`, assign it to the telecaller with the most (or most recent) `audio_calls.telecaller_id` history against that `contact_key`. Leads with no call history remain unassigned (founder-visible only) rather than guessed at.
- Acceptance criteria:
  - [ ] Script is idempotent — safe to re-run without reassigning already-assigned leads.
  - [ ] Script output logs a per-org summary (leads backfilled, leads left unassigned) so it can be verified before the scoping filter is turned on for that org.
  - [ ] Script does not touch leads in orgs where the scoping filter hasn't been enabled yet (no need to backfill an org that isn't rolling out).

**P0-4 — Per-org rollout flag**
- Add `Organization.strict_lead_scoping: bool` (default `False`). `get_inbox`/`get_lead_detail` only apply the telecaller-scoping filter when this flag is `True` for the lead's org; otherwise fall back to today's org-wide behavior.
- Acceptance criteria:
  - [ ] Flag defaults to `False` for all existing orgs — no behavior change until explicitly enabled.
  - [ ] Enabling the flag for one org has no effect on any other org.

### Nice-to-Have (P1)

**P1-1 — Founder manual reassignment**
- `PATCH /api/leads/{contact_key}/assign { telecaller_id }` — founder-only, moves a lead to a different telecaller. Fast-follow to auto-assignment for the "someone's on leave" case.

**P1-2 — Reassignment on telecaller deactivation**
- When a telecaller is deactivated (`is_active` flips to `False`), their currently-assigned leads are auto-reassigned via the same round-robin pool (excluding the deactivated telecaller), rather than being silently orphaned.

**P1-3 — Founder-facing "assigned to" indicator**
- Show `assigned_to` on the founder web app's lead/kanban card, so the founder can see ownership at a glance without opening each lead. (`leadpilot-web/apps/founder` already renders the kanban from real data per earlier build notes — this is additive.)

### Future Considerations (P2)

- Skill/source/language-weighted assignment instead of uniform round-robin.
- Telecaller self-claim of unassigned leads (if the client's process changes).
- Bulk rebalancing tooling for founders.
- Extending the same `assigned_to`-based scoping to campaigns/insights/team-status once those move off mock data.

## Role-Based Access Control Rules

| Role | Sees on `GET /api/inbox` / `GET /api/leads/{id}` |
|---|---|
| `founder` | All leads in the org, regardless of `assigned_to`. |
| `ad_manager` | All leads in the org, regardless of `assigned_to` — same as founder. Confirmed by client: "founder and other roles can see all details," and `ad_manager` is the only other role in the system. |
| `telecaller` | Only leads where `assigned_to == self.id` (plus call-only entries where `AudioCall.telecaller_id == self.id`). Unassigned leads (zero-active-telecaller edge case) are **not** visible to telecallers — founder/ad_manager only, until assigned. |

## Rollout & Migration Risk

The core risk is a telecaller's inbox going from "everything" to "empty" the moment scoping is enabled, which reads as a data-loss bug even though it's the intended fix. Mitigated by, in order:

1. **Backfill before flip** (P0-3) — run the backfill script for a target org and manually verify the "leads left unassigned" count is small/explainable before proceeding.
2. **Per-org flag** (P0-4) — enable `strict_lead_scoping` for one pilot org first, watch for support/confusion signals, then roll to the rest of the org base.
3. **No silent org-wide flip** — every other org keeps today's (admittedly wrong) org-wide visibility until explicitly opted in. This is a known tradeoff: the bug stays live for un-migrated orgs while the fix rolls out gradually. Acceptable because the alternative (flipping everyone at once) risks a worse incident than the bug itself.

## Success Metrics

- **Leading:** % of orgs with `strict_lead_scoping = True` where post-rollout `assigned_to IS NULL` lead count is <5% of total leads within 48 hours (measures backfill quality).
- **Leading:** Zero support escalations of the form "my leads disappeared" in the 7 days following a rollout for a given org.
- **Lagging:** Reduction in duplicate-outreach complaints (two telecallers calling the same lead) — track via founder-reported incidents pre/post rollout per org, if the client can quantify a baseline.

## Resolved Decisions (formerly Open Questions)

All decisions below are final; nothing in this spec is still blocking implementation.

1. **`ad_manager` visibility** — org-wide, same as founder. *(Client decision.)*
2. **Random vs. round-robin** for P0-1 — round-robin, implemented as least-loaded selection (see P0-1). *(Client: "round-robin for predictability unless the client has a reason to prefer random" — no such reason given.)*
3. **Zero-active-telecaller edge case** — lead stays unassigned, visible only to founder/ad_manager until a telecaller becomes active. *(Engineering recommendation, accepted.)*
4. **`get_lead_detail` 404-vs-403** — 404. *(Engineering recommendation, accepted.)*
5. **Timing of the follow-up audit** — insights/team-status/kanban are audited and planned *now*, alongside P0 implementation (see below); campaigns is explicitly excluded from all of this. *(Client decision.)*

## Follow-Up Audit Scope: insights / team-status / kanban

Per client direction, these three areas got the same "does it leak leads across telecallers" audit as `get_inbox`/`get_lead_detail`. **Campaigns is explicitly out of scope, not just deferred.**

### Findings

| Area | Endpoint | Scoping | Consumer | Verdict |
|---|---|---|---|---|
| Kanban | `GET /api/leads/board` (`app/api/dashboard.py:58-92`) | `org_id` only; response includes `telecaller_name` per card | Founder web only (`leadpilot-web/apps/founder`) | **By design, not a bug.** Founder-facing board meant to show every telecaller's leads side by side — that's the entire feature. |
| Team Status | `GET /api/telecallers/status` (`app/api/dashboard.py:390-466`) | `org_id` only; one row per telecaller in the org | Founder web only | **By design, not a bug.** "Team Health board" — enumerating every telecaller is the point; there's no single-telecaller "my status" use case. |
| Insights | `GET /api/insights` (`app/api/dashboard.py:1295-1300`, aggregation at `:1224-1292`) | Org-wide aggregates, including a per-telecaller quality-gap comparison that intentionally names underperforming telecallers to the founder | Founder web only | **By design, not a bug.** Founder oversight feed — naming a specific telecaller's underperformance *to the founder* is the intended behavior, not a leak. |

All three are wired to real data (no mock stubs found for these paths — the one remaining mock-data marker in `dashboard.py:798-801` is the campaigns/ad-platform feed, out of scope here).

### Two things surfaced that aren't in this audit's original scope but are worth tracking

1. **RBAC hardening gap (`dashboard.py`).** None of `dashboard.py`'s ~19 GET endpoints call `require_role(...)` — they only require *some* authenticated user (`get_current_user`), unlike `app/api/attendance.py:133` which gates with `require_role("founder", "admin")`. Since the Flutter app never calls these routes, this isn't an active leak today, but a telecaller with a valid token could hit `/api/leads/board`, `/api/telecallers/status`, or `/api/insights` directly and see org-wide data intended for founders only. **Recommended follow-up:** add `require_role("founder", "ad_manager")` to these endpoints — low priority (no known exploit path via the shipped app), but cheap defense-in-depth.
2. **`calls.py` has more org_id-only endpoints beyond the two just fixed.** `GET /count`, `GET /{call_id}`, the list endpoint, `/audio`, `/transcript`, `/lead-analysis`, `/score`, `/memory`, `/processing-status`, `/transcript/translate` all filter `AudioCall`/`Lead` by `org_id` only — same pattern as the original bug, just not yet scoped by `assigned_to`/`telecaller_id`. **This means the P0-2 fix (get_inbox + get_lead_detail) does not fully close the leak** — if a telecaller directly requests e.g. `GET /api/calls/{call_id}` for a call that belongs to a teammate's lead, they can still see it, even with `strict_lead_scoping` enabled. Recommended as a **P0.5 follow-up**, before general rollout of `strict_lead_scoping` to any org beyond the pilot — enabling the flag today closes the two loudest doors (inbox, lead detail) but leaves several side doors open.

## Timeline Considerations

Suggested phasing (see prior planning discussion for full sequencing):

- **Phase 0:** Backfill script (P0-3) — can start immediately, no user-facing risk since it doesn't touch the scoping filter.
- **Phase 1:** Backend scoping + rollout flag (P0-2, P0-4) — depends on Open Question 1 (`ad_manager`) being resolved.
- **Phase 2:** Auto-assignment on creation (P0-1) — can be built in parallel with Phase 1, but shouldn't go live until Phase 1's flag exists, otherwise every org gets auto-assignment before it can see the effect.
- **Phase 3:** P1 items (manual reassignment, deactivation handling, founder UI indicator).
- **Phase 4:** Pilot rollout on one org, then general rollout.
