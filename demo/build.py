"""
Builds the downloadable demo: one zip per operating system, each with its own Python, so
testers only unzip it and double-click "Start Demo". Used by .github/workflows/demo-release.yml.

    python demo/build.py                  all systems, into dist/
    python demo/build.py linux windows    only these

Run it from the project folder after `pip install -r requirements.txt`. Packages for the other
systems are downloaded as ready-made wheels, so one Linux machine builds all four zips.
"""

import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
FILES = ROOT / "demo" / "files"

# Standalone Python builds, https://github.com/astral-sh/python-build-standalone
PYTHON = "3.12.16"
PBS_RELEASE = "20261026"
PBS_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
           "{release}/cpython-{python}+{release}-{triple}-install_only_stripped.tar.gz")

TARGETS = {
    # name: (Python build, wheel platforms, packages folder inside python/, start script)
    "windows": ("x86_64-pc-windows-msvc", ["win_amd64"], "Lib/site-packages", "Start Demo.bat"),
    "macos-apple-silicon": ("aarch64-apple-darwin", ["macosx_11_0_arm64"],
                            "lib/python3.12/site-packages", "Start Demo.command"),
    "macos-intel": ("x86_64-apple-darwin", ["macosx_10_13_x86_64"],
                    "lib/python3.12/site-packages", "Start Demo.command"),
    "linux": ("x86_64-unknown-linux-gnu", ["manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"],
              "lib/python3.12/site-packages", "start-demo.sh"),
}
# Parts of Python the shop never uses, left out to keep the download small.
UNUSED = tuple(f"python/{part}/" for part in [
    "include", "share", "tcl", "Lib/test", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo",
    "lib/python3.12/test", "lib/python3.12/idlelib", "lib/python3.12/tkinter", "lib/python3.12/turtledemo"])
# Languages the shop is in; Django's other 100+ translations are left out.
LANGUAGES = {"el", "en"}
# Project files the demo does not need.
SKIP = (".github/", "tests/", "demo/build.py", "demo/files/", "demo/RELEASE_NOTES.md", "Dockerfile", "docker-compose.yml", "Caddyfile",
        ".dockerignore", ".gitignore", ".gitattributes", ".env.example", "requirements-demo.txt")


def project_files():
    """The files in git, so stray files lying in the project folder never end up in the demo."""
    listed = subprocess.run(["git", "ls-files"],
                            cwd=ROOT, check=True, capture_output=True, text=True).stdout.splitlines()
    return [f for f in listed if not f.startswith(SKIP) and (ROOT / f).is_file()]


def collect_static():
    """Collect the CSS, fonts and admin files once; every zip gets a copy."""
    env = {**os.environ, "DEBUG": "0", "DEMO_MODE": "1", "SECRET_KEY": "build-only",
           "SQLITE_PATH": str(DIST / "build-db" / "db.sqlite3")}
    subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput", "--clear", "--verbosity", "0"],
                   cwd=ROOT, env=env, check=True)
    return ROOT / "staticfiles"


def download_python(triple):
    url = PBS_URL.format(release=PBS_RELEASE, python=PYTHON, triple=triple)
    archive = DIST / "cache" / url.rsplit("/", 1)[1]
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        print(f"  downloading {archive.name}")
        urlretrieve(url, archive)
    return archive


def prune(site_packages):
    """Leave out what the demo never uses: Django's other languages and pip."""
    for locale in site_packages.glob("django/**/locale"):
        for language in locale.iterdir():
            if language.is_dir() and language.name not in LANGUAGES:
                shutil.rmtree(language)
    for unused in [site_packages / "pip", *site_packages.glob("pip-*.dist-info")]:
        shutil.rmtree(unused)


def make_zip(folder):
    """Zip the folder, keeping file permissions and storing links as links (not copies of big files)."""
    archive = folder.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(folder.rglob("*")):
            name = path.relative_to(DIST).as_posix()
            if path.is_symlink():
                info = zipfile.ZipInfo(name)
                info.create_system = 3  # Unix, so unzip tools restore the link
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(info, os.readlink(path))
            else:
                zf.write(path, name)
    return archive


def build(name, files, static):
    triple, platforms, site_packages, start_script = TARGETS[name]
    folder = DIST / f"athos-icons-demo-{name}"
    print(f"Building {folder.name}")
    shutil.rmtree(folder, ignore_errors=True)

    def keep(member, path):
        return None if f"{member.name}/".startswith(UNUSED) else tarfile.data_filter(member, path)

    with tarfile.open(download_python(triple)) as tar:
        tar.extractall(folder, filter=keep)  # creates folder/python

    subprocess.run([
        sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-compile",
        "--target", str(folder / "python" / site_packages), "--python-version", "3.12", "--implementation", "cp",
        "--only-binary=:all:", *[f"--platform={p}" for p in platforms], "-r", str(ROOT / "requirements-demo.txt"),
    ], check=True)
    prune(folder / "python" / site_packages)

    for relative in files:
        target = folder / "app" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    shutil.copytree(static, folder / "app" / "staticfiles")
    shutil.copy2(FILES / start_script, folder / start_script)
    (folder / start_script).chmod(0o755)
    shutil.copy2(FILES / "README.txt", folder / "README.txt")

    archive = make_zip(folder)
    print(f"  {archive.name}: {archive.stat().st_size / 1e6:.0f} MB")


def main():
    names = sys.argv[1:] or list(TARGETS)
    unknown = set(names) - set(TARGETS)
    if unknown:
        sys.exit(f"Unknown target(s): {', '.join(unknown)}. Choose from: {', '.join(TARGETS)}")
    DIST.mkdir(exist_ok=True)
    files, static = project_files(), collect_static()
    for name in names:
        build(name, files, static)


if __name__ == "__main__":
    main()
