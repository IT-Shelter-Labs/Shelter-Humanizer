"""Package the independent editorial skill with its MIT license."""

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from shelter_humanizer import __version__

    name = "shelter-humanizer-ru"
    source = ROOT / "skills" / name
    target = ROOT / "dist" / "skill"
    target.mkdir(parents=True, exist_ok=True)
    archive = target / f"Shelter-Humanizer-{__version__}-skill.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, str(Path(name) / path.relative_to(source)))
        bundle.write(ROOT / "LICENSE", name + "/LICENSE")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (target / "SHA256SUMS-skill.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(archive.name)


if __name__ == "__main__":
    main()
