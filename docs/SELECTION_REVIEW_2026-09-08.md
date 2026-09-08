# Selection review and pending choices

Reviewed Pro's report against the worktree after 417734c. Both selection defects remained present, independently of the newer shared BodySlide customizer. The broader assessment that independent character face/body/skin/shape controls remain unfinished is still accurate.

Changes:

- Guided body choices now have a separate profile-scoped pending record. Closing or switching shared groups retains the chosen body, preset, part/outfit alternatives and morph setting, without making them verified defaults or starting preparation. A custom preset is recorded immediately after its publication. Pending choices are labelled not applied. Missing body/preset/project choices are kept for review rather than substituted. A concurrent pending-record change is not overwritten.
- Advanced preset changes retain checked projects and reassess applicability. Reloading the selection from a previous build is now explicit. The placeholder cannot select unsaved projects.
- Character choices distinguish keeping an applied saved provider from following the current source-mod setup. Removing the final override now disables only the managed appearance output after checking its manifest, hashes and active dependents. Original output and the prior choices are retained. Effective removal is verified after MO2 refresh; failure restores prior activation when the profile still matches. A reset receipt prevents an automatic empty rebuild and permits later new appearance choices.

Validation: 389 hub tests pass, including actual Advanced method probes, pending/applied separation, independent female/male drafts, missing choices, concurrent pending changes, preserving one character while changing another, last-override reset, active dependents, changed output, failed disable, stale effective files and reset-state interpretation. Python compilation covers all deployed hub modules. Native Qt checks follow deployment; this note does not claim the full character customization workflow is delivered.


Native MO2 check on d476ff1: selected the retained custom preset, closed, and reopened Bodies & outfits. The preset and four part selections returned with the pending/not-applied message; the verified default remained Athletic. In Advanced, selecting CBBE 3BBB Amazing NeverNude and then choosing Athletic retained that one checked project. In Characters, selecting Keep my saved appearance for Adrianne retained all three selected character overrides. No build was run during these checks. The final-override backend has host/file integration coverage, but has not yet been exercised through native MO2. All 390 hub tests pass after the final context-sensitive reset-label clarification.

User clarification: this is still an incomplete character screen. One detected complete appearance package is not a substitute for the agreed independent face/body/skin/shape and supported hair/physics controls. Current provider labels are informational. Selection/reset correctness is retained, but the next implementation must connect independent choices to actual scoped output; it must not count additional keep/reset menu entries as customization capabilities.
