"""Disposable exact-tar path characterization; never touches retained attempts."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[3]
ARCHIVE = Path(r"C:\Users\red\Desktop\Modlab\workspace\inbox\Mod.Organizer-2.5.2.7z")
TAR = r"C:\Windows\System32\tar.exe"

def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    return {"args": args, "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

print(json.dumps({"archive": str(ARCHIVE), "size": ARCHIVE.stat().st_size,
                  "sha256": hashlib.file_digest(ARCHIVE.open("rb"), "sha256").hexdigest(),
                  "python": sys.executable, "pythonVersion": sys.version,
                  "pythonSha256": hashlib.file_digest(open(sys.executable, "rb"), "sha256").hexdigest(),
                  "tarSha256": hashlib.file_digest(open(TAR, "rb"), "sha256").hexdigest(),
                  "tar": run([TAR, "--version"])}), flush=True)
listing = run([TAR, "-tf", str(ARCHIVE)])
assert listing["exit"] == 0, listing
member = max((line for line in listing["stdout"].splitlines() if not line.endswith("/")), key=len)
print(json.dumps({"longestMember": member, "units": len(member.encode("utf-16-le")) // 2}), flush=True)
with tempfile.TemporaryDirectory(prefix="p7a-", dir=ROOT) as scratch:
    for size in (180, 247, 253, 258, 259, 260):
        base = Path(scratch) / str(size)
        directory = base / ("d" * (size - len(str(base)) - 1))
        directory.mkdir(parents=True)
        result = run([TAR, "-xf", str(ARCHIVE), "-C", str(directory), member])
        extracted = directory / member
        result.update(cwdUnits=len(str(directory)), destinationUnits=len(str(extracted)),
                      exists=extracted.is_file(), pythonRead=None)
        if extracted.is_file():
            result["pythonRead"] = len(extracted.read_bytes())
            relocated = directory.parent / (directory.name + "-relocated")
            directory.rename(relocated)
            result["relocatedRead"] = len((relocated / member).read_bytes())
        print(json.dumps(result), flush=True)
