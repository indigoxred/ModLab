"""Add preparation-only CLI support to the pinned Synthesis 0.36.6 source tree.

Run with the extracted source root. Build/publish the CLI with the same SDK and
dependencies as the existing ModLab helper. Preparation invokes upstream Prep
only; it never invokes ExecuteRun. Keep the original helper until verified.
"""
import sys
from pathlib import Path


def patch(root):
    changes = (
        ('Synthesis.Bethesda.Execution/Commands/RunPatcherPipelineCommand.cs',
         '    public bool Shortcircuit { get; set; } = true;',
         '\n\n    [Option("BuildOnly", Required = false, HelpText = "Prepare selected patchers without running them or writing game patches", Default = false)]\n'
         '    public bool BuildOnly { get; set; } = false;'),
        ('Synthesis.Bethesda.CLI/RunPipeline/RunPatcherPipeline.cs',
         '        await prep.Prep(cancel);', '\n\n        if (_command.BuildOnly) return;'),
    )
    for relative, anchor, addition in changes:
        path = root / relative
        source = path.read_text(encoding='utf-8-sig')
        if addition.strip() in source:
            continue
        if source.count(anchor) != 1:
            raise ValueError('Unexpected Synthesis source: ' + relative)
        path.write_text(source.replace(anchor, anchor + addition), encoding='utf-8')


if __name__ == '__main__':
    patch(Path(sys.argv[1]))
