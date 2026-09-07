# Synthesis preparation helper

ModLab requires its locally built Synthesis 0.36.6 CLI with the `--BuildOnly`
option. The unmodified upstream CLI does not provide that option. Keep this
label distinct from an official Synthesis binary.

The adapter prepares the selected patchers in a direct child process of MO2,
outside its virtual filesystem. It then runs the prepared patchers through
MO2 with `--BlockBuildingWithinMo2`. Both stages retain logs. A failed
preparation stops before patch execution or output publication. The normal
context, settings, output and record checks still apply.

To reproduce the helper from the pinned 0.36.6 source:

1. Apply `tools/patch_synthesis_preparation.py <source-root>`. It adds one CLI
   option and returns after upstream preparation when that option is selected.
2. Retain the existing direct System.Security.Cryptography.Xml 10.0.11
   dependency and build with the private .NET SDK 10.0.400.
3. Publish `Synthesis.Bethesda.CLI/Synthesis.Bethesda.CLI.csproj` in Release
   with `AppTargetFramework=net10.0`, `TargetFrameworks=net10.0`,
   `DisableGitVersionTask=true`, `UseSharedCompilation=false`, `Version=0.36.6`
   and `InformationalVersion=0.36.6-ModLab-prep`. Restore dependencies first
   when the source has no matching restored assets.
4. Keep the complete publish directory together; select its executable as
   `synthesis-cli` in ModLab's helper configuration. Preserve the previous
   helper for recovery.

The live 1.6.1170 test generated Speed and Reach Fixes Updated from its pinned
commit, checked the output with xEdit and applied it to the test profile.
Preparation-only verification left patch output, settings and persistence
unchanged. This verifies that selected pipeline, not arbitrary patchers or
in-game weapon behavior. General helper acquisition/packaging is still pending.
