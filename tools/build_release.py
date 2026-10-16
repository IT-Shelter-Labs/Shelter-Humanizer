"""Build on the target OS; assemble a portable ZIP with source/runtime notices."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from shelter_humanizer import __version__

    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--output-dir", type=Path, help="Separate directory for a test build")
    args = parser.parse_args()
    system = platform.system().lower()
    if system not in ("windows", "linux"):
        parser.error("Builds currently support Windows and Linux")
    target = (args.output_dir or ROOT / "dist" / system).resolve()
    target.mkdir(parents=True, exist_ok=True)
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)

    def git_value(*arguments):
        result = subprocess.run(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = git_value("rev-parse", "HEAD")
    identity = {
        "version": __version__,
        "build_id": f"{__version__}-{built_at.replace(':', '').replace('-', '')}",
        "commit": commit,
        "source_dirty": bool(git_value("status", "--porcelain")),
        "built_at": built_at,
    }
    metadata = work / "build-info.json"
    metadata.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "Shelter-Humanizer",
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        str(ROOT / "src" / "shelter_humanizer" / "assets") + ":shelter_humanizer/assets",
        "--add-data",
        str(metadata) + ":shelter_humanizer",
        "--distpath",
        str(target),
        "--workpath",
        str(work / "pyinstaller"),
        "--specpath",
        str(work),
        str(ROOT / "launcher.py"),
    ]
    if system == "windows":
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:rc(\d+))?", __version__)
        if match is None:
            raise ValueError("Unsupported native version")
        version_tuple = tuple(int(value or 0) for value in match.groups())
        flags = 0x2 if match[4] else 0
        native_version = work / "version-info.txt"
        native_version.write_text(
            f"VSVersionInfo(ffi=FixedFileInfo(filevers={version_tuple!r}, prodvers={version_tuple!r}, "
            f"mask=0x3f, flags={flags}, OS=0x40004, fileType=1, subtype=0, date=(0,0)), "
            "kids=[StringFileInfo([StringTable('040904B0', ["
            "StringStruct('CompanyName', 'IT Shelter'), "
            "StringStruct('FileDescription', 'Shelter Humanizer'), "
            f"StringStruct('FileVersion', '{__version__}'), "
            "StringStruct('ProductName', 'Shelter Humanizer'), "
            f"StringStruct('ProductVersion', '{__version__}')])]), "
            "VarFileInfo([VarStruct('Translation', [1033, 1200])])])",
            encoding="utf-8",
        )
        command.insert(4, "--windowed")
        command[4:4] = [
            "--version-file",
            str(native_version),
            "--icon",
            str(ROOT / "src" / "shelter_humanizer" / "assets" / "it-shelter.ico"),
        ]
    build_environment = dict(os.environ)
    build_environment["PYINSTALLER_CONFIG_DIR"] = str(work / "cache")
    subprocess.run(command, cwd=ROOT, env=build_environment, check=True)
    binary = target / ("Shelter-Humanizer.exe" if system == "windows" else "Shelter-Humanizer")
    receipt = work / "frozen-selftest.json"
    # All tests use synthetic text and remain offline.
    subprocess.run([str(binary), "--self-test", str(receipt)], cwd=work, check=True, timeout=30)
    if not json.loads(receipt.read_text(encoding="utf-8")).get("ok"):
        raise RuntimeError("Frozen smoke test failed")
    distribution = importlib.metadata.distribution("pyinstaller")
    bootloader_license = next(
        (
            distribution.locate_file(path)
            for path in distribution.files or ()
            if path.name == "COPYING.txt"
        ),
        None,
    )
    if bootloader_license is None:
        raise RuntimeError("PyInstaller distribution license not found")
    runtime = {
        "PYTHON_LICENSE.txt": Path(sys.base_prefix) / "LICENSE.txt",
        "PYINSTALLER_LICENSE.txt": bootloader_license,
    }
    if system == "windows":
        # python.org Windows installers include the complete Tcl license in LICENSE.txt.
        tcl = Path(sys.base_prefix) / "tcl" / "tcl8.6" / "license.terms"
        runtime.update(
            {
                "TCL_LICENSE.txt": tcl if tcl.is_file() else runtime["PYTHON_LICENSE.txt"],
                "TK_LICENSE.txt": Path(sys.base_prefix) / "tcl" / "tk8.6" / "license.terms",
            }
        )
    if any(not path.is_file() for path in runtime.values()):
        raise RuntimeError("Runtime license files are missing; do not distribute this archive")
    arch = (
        "x64" if platform.machine().lower() in ("amd64", "x86_64") else platform.machine().lower()
    )
    archive = target / f"Shelter-Humanizer-{__version__}-{system}-{arch}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        bundle.write(binary, binary.name)
        for name in ("START_HERE.txt", "START_HERE_EN.txt", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            bundle.write(ROOT / name, name)
        for name, path in runtime.items():
            bundle.write(path, "licenses/" + name)
        for path in sorted((ROOT / "examples").glob("*.txt")):
            bundle.write(path, "examples/" + path.name)
        for name in (
            "ADVANCED_SETTINGS_RU.md",
            "TROUBLESHOOTING_RU.md",
            f"RELEASE_NOTES_{__version__}.md",
        ):
            bundle.write(ROOT / "docs" / name, "docs/" + name)
        bundle.writestr("BUILD_INFO.json", metadata.read_bytes())
        bundle.writestr("SELFTEST.json", receipt.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (target / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(
        f"Built {archive.name} ({archive.stat().st_size / 1024**2:.1f} MiB); frozen GUI test passed"
    )


if __name__ == "__main__":
    main()
