# Preparing graphics after new installations

The supported workflow must combine the user's chosen parts of several mods. It is not a contest between whole graphics mods. Roads and bridges can use Blended Roads and its matching SMIM patch while other objects retain SMIM or High Poly Project. Future category choices must preserve the necessary mesh/material/texture relationships and applicable patches; a generic whole-mod priority selector is insufficient.

## Reproduced and corrected

Installing new mesh sources above an existing ModLab Graphics output caused preparation to treat their expected file replacements as unexplained manual changes. The real SMIM/HPP/Blended Roads batch reached this refusal before regeneration.

Preparation now recognizes exact current mesh winners from a completed, verified installation batch awaiting setup. It checks profile, active source, receipt location, installation identity, output timestamp, path ownership, file size and SHA-256 before allowing the existing graphics workflow to regenerate from those inputs. The withdrawal record retains the installation receipts. Modified generated output, disabled generated plugins, altered source files and unrelated manual overrides remain protected. This evidence permits regeneration; it does not certify compatibility or discard a user's saved component preferences.

Reopening ModLab also reconnects a completed batch to its pending preparation result, without reinstalling any archive. Malformed historical queue files cannot prevent finding the valid pending batch and are left intact.

## Native installation evidence

Batch `cc6de4939e14`, profile `ModLab 1170 Test`, installed through ModLab's native installer flow:

| Archive | Selected installation | Verified files |
| --- | --- | ---: |
| SMIM 2.08 | Skyrim Special Edition: Everything | 1,427 |
| High Poly Project 5.3 | Everything; unrelated optional patches unselected | 273 |
| Blended Roads 1.7 | Blended Roads plus its SMIM compatibility patch | 109 |

The three archive SHA-256 values and all 1,809 installed receipt files were independently rechecked. HPP is above SMIM in the active profile. Five installed bridge meshes match the selected Blended Roads SMIM patch's archive entries byte for byte. Original archives remain intact.

After the fix, the native workflow recognized 13 newly installed mesh inputs, proceeded through preparation and applied graphics job `1b9a73d8e699`, with 643 generated meshes checked. This is installation/configuration/output evidence, not an in-game visual verification.

After deploying the queue reconnect change and reopening MO2, an unchanged setup recheck completed in 26 seconds. Batch `cc6de4939e14` now points to setup result `a28f60560563` and reports `Installations completed; setup needs attention`, retaining all three completed installation receipts. The remaining findings are not concealed behind an unconditional ready message.

## Regression coverage and remaining scope

514 hub tests pass. New cases cover verified new-source regeneration after restart, changed files, other profiles, old or completed queues, external receipts, wrong targets, disabled sources, edited generated output, unrelated overrides, reconnecting completed batches and malformed history. The original refusal and malformed-history crash were reproduced before their fixes.

The applied source changes were deployed with the test instance closed and previous modules backed up. The broader category-based graphics interface, TexGen/DynDOLOD preparation and the remaining folder installations are unfinished. Majestic Mountains has not yet been installed. Existing Apocalypse and Ars Metallica record findings remain unresolved; this checkpoint does not claim full setup readiness.
