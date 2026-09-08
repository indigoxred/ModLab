# Native installation evidence

ModLab now records the source archive SHA-256 and an exact size/hash inventory
of the files returned by MO2's native installer. Root `meta.ini` is excluded
because MO2 owns and updates it. The receipt stays outside the installed mod,
in the existing installation history record.

Checksum reads yield in 1 MiB steps through Qt timers. The final metadata pass
does not yield before activation. Changed files, changed archive, unreadable
content, linked paths, an empty result, or a changed profile prevent a successful
queue result. Receipt writes use a temporary file and replacement; the refresh
callback is registered only after the pending record is saved. A failed receipt
save cannot leave an activation callback running behind a paused queue.

This evidence describes the native installer's selected output. It is not an
independent interpretation of every FOMOD instruction, a claim that every
dependency exists, or proof of gameplay compatibility. Effective file winners
and preparation remain separate checks. Existing historical installations do
not acquire fabricated receipts, and cancelled duplicates are not silently
marked successful because a similarly named folder exists.

## Real MO2 verification

Retried only the unfinished UIExtensions item in queue `495d4139a828` using
Continue setup → Review unfinished installs. Native Replace retained its backup;
the already completed OBody item was not reinstalled.

Installation record: `7531dda78b0c47bc8cc772ec5f812e7c`.

- `UIExtensions.bsa`: 477,909 bytes.
- `UIExtensions.esp`: 1,143 bytes.
- Both installed hashes match every file member in the original archive.
- Both match the pre-test installation and retained native backup.
- The plugin remains enabled; the queue records both requests completed and
  starts normal setup preparation.

Independent evidence script and JSON are retained in the task workspace at
`outputs/ui-rework/verify_install_inventory.py` and `.json`.

472 hub tests passed after the final timing and callback fixes. Focused tests
cover archive/file mutation, added files, empty/outside output, profile changes,
unsaved verification, and pending-record write failure. Gameplay was not run.
