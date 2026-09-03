# Task 7 fix1 — exact publication boundary clarification

This root clarification corrects the overbroad phrase 'atomic marker promotion is
consumption' in the earlier root ruling and fix1 implementation report. It does not
change source, the shared publisher, historical evidence, or authorize live operation.

The reviewer identified and root directly checked the unchanged
`modlab/platform/windows_exact_fs.py:1064` publication contract through line 1146,
plus `_initialize_preparation_successor` and its enclosing command/old-run locks in
`modlab/validation/mo2_containment_service.py` (root native read c73142).

The shared publisher validates through the same retained candidate before and after
the exact no-replace rename. Before publication completes and both owners close, a
caught validation/close failure may delete ONLY that exact retained candidate, even
after its rename to the final marker path. Thus rename visibility alone is not
completed verified publication. This is the inherited failed-publication rollback
contract, not general stale-candidate recovery.

On the repair path, command and old-run exclusion remain held and successor startup
does not begin before consumption publication returns. Exact failed-publication
rollback at that boundary can leave neither a marker nor successor effects. That is
not refunding a completed consumed marker, deleting a failed successor, or granting
new authority to remove unknown state. Actual publication effects and retained-owner
failures must still be reported.

After successful publication, this repair must never roll back or replenish that
consumed authority. An abrupt interruption or failed cleanup may instead leave an
on-disk marker. A thrown error alone is not proof that publication did not happen:
the exact marker, its v2 reservation and current state must be reloaded/handled under
the existing refusal and startup-disposition rules. No partially written or unknown
candidate may be adopted as authority; no marker-bound failure is invented when no
marker exists. Unknown/substituted links remain blocked.

The independent scoped reviewer is including this distinction in its final verdict.
No source change or repeated native gate was requested for clarifying this existing
contract. The full native result remains bound to the same frozen source/test bytes;
the original report is preserved rather than rewritten to erase the earlier wording.
