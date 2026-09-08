# Keeping an existing verified installation

The guided Add mods/queue workflow now checks installation receipts before
opening MO2's native installer. When a receipt from this profile and mod store
matches the selected archive path, its archive SHA-256 and full current file
inventory must also match before ModLab offers:

- **Keep my current choices**: retain installed bytes and installer selections.
- **Run installer again**: continue to MO2's normal installer, including FOMOD
  options and the protected merge/replace backup flow.
- **Cancel**: retain files and leave the queued request unfinished.

The hashes are checked again after the choice dialog closes. Edits made while
the question is open are preserved and stop reuse. A disabled mod is enabled
only after the user chooses to use the existing installation; the dialog
explains this. Plugin state and mod priority are not silently reset.

Legacy receipts without full inventories, changed/missing/added files, another
profile/store, and mismatched archive bytes do not establish reuse. They leave
the normal installer available. The initial lookup currently uses the archive
path; moved or renamed downloads can therefore take the normal installer route
even when their bytes happen to match. This is not an incompatibility verdict.

## Verification

482 hub tests passed. The repeat-install tests cover retained choices, native
fallback, cancellation, changed files during the dialog, disabled mods, record
write failure, closed parent window, old/failed receipts, and profile isolation.
Independent review found no actionable correctness issue in this slice.

Real MO2 test: adding UIExtensions again displayed the new choice dialog.
Choosing Keep created receipt `1bcb68f8672d458a98364c35a1520a1a`, linked to
`7531dda78b0c47bc8cc772ec5f812e7c`, and completed queue `d7aca6b51dee`.
Both the ESP and BSA bytes and modification timestamps remained unchanged.
No additional UIExtensions mod or backup folder was created. The existing
native backup was retained. Preparation continues through the normal workflow;
reuse does not certify dependencies, effective winners, or gameplay.

Independent evidence remains in the task workspace:
`outputs/ui-rework/verify_repeat_install.py` and `.json`.
