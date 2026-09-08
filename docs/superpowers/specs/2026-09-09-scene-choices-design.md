# Scene choices in the existing graphics workflow

This implements the user's approved component-based graphics workflow. Their repeated instructions authorize proceeding without another design approval cycle.

Users choose installed providers for parts of the scene: roads and bridges, mountains and rocks, trees, grass, buildings and ruins, and other supported surface groups. Choosing roads must not change the same mod's unrelated objects. Keep renderer preparation separately available; do not replace it with the new chooser.

Use active source-mod assets, not generated outputs, as selectable providers. A selection retains the provider's actual installed variants and patches. Files that provider does not supply continue to follow the existing setup. Include referenced textures supplied by the selected mesh provider so model and material choices remain together. Preserve external texture dependencies and verify availability. Different selected groups requiring different bytes at one shared path require an explicit resolution; do not silently pick whichever group is processed last.

Publish selected assets into a profile-owned Scene appearance output using the existing checked-output publication/recovery mechanism. Source mods remain intact. Keep saved requests distinct from applied results; preserve choices through reopening, recheck source ownership, and report changed/missing inputs without silently undoing manual MO2 changes. The graphics material helper consumes this output afterward. No whole-mod reordering implements the user's component choice.

Normal UI uses group names, mod display names, coverage and the selected result. File paths and plugin names remain in advanced evidence. Retain a Follow installed setup option and a recoverable reset. FOMOD remains the place to change the mod's installed options. Do not label arbitrary combinations compatible merely because files exist. Unsupported packed/record-dependent selections remain visible with a precise limitation rather than declaring the entire mod incompatible.

Acceptance uses the actual SMIM, High Poly Project, Blended Roads and Majestic Mountains installation plus focused regression fixtures: choose roads from one and mountains from another; retain a compatibility patch's bytes; detect competing shared textures; reject changed source/output ownership; preserve unrelated files; reset/reopen; and pass selected sources to downstream preparation. Distant LOD and visual gameplay verification remain separate unfinished work.
