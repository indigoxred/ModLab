from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from modlab.validation.mo2_containment_store import ContainmentStore
from modlab.validation.mo2_preparation_recovery import record_to_bytes
from modlab.validation.windows_integrity import set_low_integrity_tree
from modlab.workspace import initialize_workspace
from tests.support.mo2_containment import import_curated_mo2_archive


class _FileTime(ctypes.Structure):
    _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))


def _filetime(value: _FileTime) -> int:
    return (int(value.high) << 32) | int(value.low)


def _process_times(process: subprocess.Popen[bytes]) -> tuple[int, float]:
    creation = _FileTime()
    exit_time = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    handle = wintypes.HANDLE(int(process._handle))
    if not ctypes.windll.kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise ctypes.WinError()
    return _filetime(creation), (_filetime(kernel) + _filetime(user)) / 10_000_000


def main() -> int:
    repo = Path(sys.argv[1])
    evidence_prefix = Path(sys.argv[2])
    archive_input = Path(sys.argv[3])
    steam_input = Path(sys.argv[4])
    event_path = evidence_prefix.with_suffix(".events.jsonl")
    result_path = evidence_prefix.with_suffix(".result.json")
    stdout_path = evidence_prefix.with_suffix(".stdout.log")
    stderr_path = evidence_prefix.with_suffix(".stderr.log")
    failure_path = evidence_prefix.with_suffix(".failure-record.json")
    event_path.open("x", encoding="utf-8").close()

    def emit(phase: str, **values: object) -> None:
        row = {
            "phase": phase,
            "utc": datetime.now(timezone.utc).isoformat(),
            **values,
        }
        line = json.dumps(row, sort_keys=True)
        with event_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    workspace: Path | None = None
    child: subprocess.Popen[bytes] | None = None
    try:
        emit("setup-start", parent_pid=os.getpid())
        for _ in range(256):
            workspace = repo / ("e" + secrets.token_hex(3))
            try:
                workspace.mkdir()
            except FileExistsError:
                continue
            break
        else:
            raise RuntimeError("could not allocate fresh short diagnostic workspace")
        metadata = workspace.stat()
        emit(
            "workspace-created",
            workspace=str(workspace),
            st_dev=int(metadata.st_dev),
            st_ino=int(metadata.st_ino),
        )
        native_temp = workspace / "native-temp"
        (native_temp / "Low").mkdir(parents=True)
        set_low_integrity_tree(native_temp / "Low")
        emit("native-temp-low-labeled", native_temp=str(native_temp))
        layout = initialize_workspace(workspace)
        validation = layout.mo2_containment_validation
        emit("workspace-initialized", validation=str(validation))
        artifact = import_curated_mo2_archive(
            archive_input,
            workspace,
            workspace / "curated-input",
        )
        emit("archive-imported", artifact_id=artifact.artifact_id)

        child_code = (
            "from tests.test_mo2_preparation_recovery import _native_child_failure; "
            "import sys; _native_child_failure(*sys.argv[1:])"
        )
        command = (
            sys.executable,
            "-B",
            "-c",
            child_code,
            str(workspace),
            artifact.artifact_id,
            str(steam_input),
            str(validation),
        )
        native_environment = {
            key: os.environ[key]
            for key in (
                "COMSPEC",
                "PATH",
                "SYSTEMROOT",
                "TEMP",
                "TMP",
                "USERNAME",
                "WINDIR",
            )
            if key in os.environ
        }
        native_environment["TEMP"] = str(native_temp)
        native_environment["TMP"] = str(native_temp)
        emit("child-launch", command=list(command), environment=sorted(native_environment))
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            child = subprocess.Popen(
                command,
                cwd=repo,
                env=native_environment,
                stdout=stdout,
                stderr=stderr,
            )
            started = time.monotonic()
            creation_time, cpu_seconds = _process_times(child)
            emit(
                "child-started",
                child_pid=child.pid,
                child_creation_time=creation_time,
                child_cpu_seconds=cpu_seconds,
            )
            next_sample = started + 30
            timed_out = False
            while child.poll() is None:
                now = time.monotonic()
                if now - started >= 480:
                    timed_out = True
                    break
                if now >= next_sample:
                    creation_time, cpu_seconds = _process_times(child)
                    emit(
                        "child-sample",
                        child_pid=child.pid,
                        child_creation_time=creation_time,
                        child_cpu_seconds=round(cpu_seconds, 3),
                        elapsed_seconds=round(now - started, 3),
                    )
                    next_sample += 30
                time.sleep(1)
        duration = time.monotonic() - started
        result = {
            "status": "timeout" if timed_out else "completed",
            "workspace": str(workspace),
            "native_temp": str(native_temp),
            "validation": str(validation),
            "artifact_id": artifact.artifact_id,
            "child_pid": child.pid,
            "child_creation_time": creation_time,
            "child_exit_code": child.poll(),
            "duration_seconds": round(duration, 3),
        }
        if not timed_out:
            output_lines = stdout_path.read_text(encoding="utf-8").splitlines()
            payload = json.loads(output_lines[-1])
            run_id = payload["runId"]
            failure = ContainmentStore.open_readonly(validation).load_preparation_failure(
                run_id
            )
            failure_path.write_bytes(record_to_bytes(failure))
            result["run_id"] = run_id
            result["failure_record"] = str(failure_path)
        result_path.write_text(
            json.dumps(result, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        emit("child-finished", **result)
        return 124 if timed_out else int(child.returncode != 0)
    except BaseException as error:
        result = {
            "status": "harness-error",
            "error_type": type(error).__name__,
            "error": str(error),
            "workspace": None if workspace is None else str(workspace),
            "child_pid": None if child is None else child.pid,
            "child_exit_code": None if child is None else child.poll(),
        }
        result_path.write_text(
            json.dumps(result, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        emit("harness-error", **result)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
