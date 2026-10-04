"""Build on the target OS; assemble a portable ZIP with source/runtime notices."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from shelter_humanizer import __version__

    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build")
    args = parser.parse_args()
    system = platform.system().lower()
    if system not in ("windows", "linux"):
        parser.error("Builds currently support Windows and Linux")
    target = ROOT / "dist" / system
    target.mkdir(parents=True, exist_ok=True)
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
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
        "--distpath",
        str(target),
        "--workpath",
        str(work / "pyinstaller"),
        "--specpath",
        str(work),
        str(ROOT / "launcher.py"),
    ]
    if system == "windows":
        command.insert(4, "--windowed")
        command[4:4] = [
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
        bundle.writestr("SELFTEST.json", receipt.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (target / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(
        f"Built {archive.name} ({archive.stat().st_size / 1024**2:.1f} MiB); frozen GUI test passed"
    )


if __name__ == "__main__":
    main()
