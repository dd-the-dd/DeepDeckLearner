# Local Insights storage and selective telemetry

Insights now separates basic match results from optional advanced measurements.
Choose an agent, deck or human player, enable metric groups, and save. Rules
apply to the next match: there is no invented backfill from old win/loss rows.
If an agent rule and a deck rule both match a seat, their enabled groups are
combined. Changing targets does not discard unsaved choices for another target.

Available groups: resources, board/life, combat, mulligans, SDK decision activity,
and match length. The collector consumes observed Engine/SDK states online;
it keeps at most 512 last-observed turn snapshots per tracked seat, numeric
event counters and confirmed decision counts. It does not retain card names,
hands, rules, replay frames or model credentials. Events are deduplicated by
round and sequence. Unknown card types do not become nonland permanents.
Damage is credited to its source controller, not the damaged player. A pending
poll is not a confirmed choice. Unsupported/missing measurements are absent,
not fabricated zeroes. Automatic mana payments may not emit mana-production
events; "observed mana produced" must not be interpreted as complete mana use.

Turn curves average the last observed state for each numbered game turn across
games. They show sample counts. Longer games contribute more late-turn samples;
the curve is descriptive, not a forecast or an equal-weighted per-match estimate.
Match-turn totals sum observed maxima per game, rather than counting UI frames.
Data is saved only for completed matches; a failed/interrupted match is not a
draw or loss. Decision feedback limitations are displayed with the measurements.

## Storage

The default maximum is **128 MiB**, adjustable from 1 MiB to 10 GiB in Insights.
This is a ceiling, not physical disk preallocation. Basic results and advanced
profiles share a compact, atomic file per match under ignored
`.deepdeck/statistics/matches/`. Both/all seats of a completed match are written
together. A cross-process OS lock serializes recording, settings and retention.
The oldest entire match is removed first when space is needed. Replays,
favorite games and competition brackets are independent and are not deleted.
Evicting a statistics summary does not erase competition results or its replay.

Settings and a small last-error marker use fixed-overhead files outside the
data ceiling. A too-large match or insufficient physical disk space is reported
in Insights without failing the played match or fabricating its statistics.
The reducer streams per-match files and only retains numeric aggregate cells,
not every advanced profile in RAM. Basic summary readers discard profiles.

Existing `agents/*/local-results.jsonl` and `players/*/local-results.jsonl` remain
read-only and count toward the quota. They are still included in Insights and
legacy deck comparisons. New local matches no longer append to those files.
A limit below their total size is rejected rather than silently deleting history.
Existing Engine aggregates in `runs/deck-analytics.json`,
`external/deepdeck-engine/runs/deck-analytics.json`, and an explicitly configured
`MTG_DECK_ANALYTICS_PATH` are also counted, deduplicated by resolved path, and
preserved read-only. Their bytes are reported separately in Insights.

The quota does **not** include replays (their own quota), GPU/training artifacts
or competition metadata; it is not a limit on the entire installation.
New local matches request `recordAnalytics:false` so the Engine does not keep a
second permanent aggregate. The Engine response must acknowledge
`analyticsRecording:false`; a missing/true value produces an update warning in
Insights rather than pretending the budget covers that growing duplicate.
The Engine advertises `session-analytics-opt-out/v1` in `/health`. Other clients
keep historical analytics by default; old advanced training remains available.
Disabled Engine analytics still forwards every game decision/observation to its
inner provider, but retains no legacy decision/turn telemetry.

The opt-out implementation requires the updated Engine binary. It was verified
on a completed real Legacy SDK match (seed 417, Izzet Delver vs Dimir Tempo,
result 0–2): the session acknowledged disabled analytics, both local seat results
and the replay were retained, and the historical aggregate's SHA-256 remained
unchanged. Other clients retain their default analytics behavior.

## Validation

`test_statistics_store.py` covers protocol shape differences, missing fields,
round resets, stale polls, deduplicated cumulative events, source damage,
bounded sampling, confirmed-vs-pending choices, quota eviction, too-large writes,
legacy preservation, atomic multi-seat writes, real concurrent worker processes,
and protected API settings. AdvancedInsights tests cover opt-in defaults,
multi-target drafts, storage errors, numeric samples and accessible curves.

The real SDK batch using Izzet Delver vs Dimir Tempo, seed 417, completed 0–2.
Its numeric profile and curve were checked in the browser at desktop and mobile
sizes. The temporary validation tracking rule was removed afterwards; captured
evidence remains accessible while future advanced recording stays off by default.
