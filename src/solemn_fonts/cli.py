from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SARASA_ROOT = PROJECT_ROOT / "vendor" / "Sarasa-Gothic"
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "solemn-fonts"
DEFAULT_SOURCES_SUBDIR = "sources"
SARASA_PATCH_PATH = PROJECT_ROOT / "patches" / "sarasa-minimal-mono-sc.patch"

IOSEVKA_STYLES = (
    "ExtraLight",
    "ExtraLightItalic",
    "Light",
    "LightItalic",
    "Regular",
    "Italic",
    "SemiBold",
    "SemiBoldItalic",
    "Bold",
    "BoldItalic",
)

SOURCE_HAN_SANS_WEIGHTS = {
    "ExtraLight": "ExtraLight",
    "Light": "Light",
    "Regular": "Regular",
    "SemiBold": "Medium",
    "Bold": "Bold",
}

DEFAULT_BUILD_STYLES = ("Regular", "Italic", "Bold", "BoldItalic")
SARASA_DIRECT_SHS_PATCH_MARKER = "SOLEMN: direct Source Han Sans OTF input"
TTFAUTOHINT_VERSION = "1.8.4"
TTFAUTOHINT_URLS = {
    "darwin": (
        "https://sourceforge.net/projects/freetype/files/ttfautohint/1.8.4/"
        "ttfautohint-1.8.4-tty-osx.tar.gz/download"
    ),
    "win32": (
        "https://sourceforge.net/projects/freetype/files/ttfautohint/1.8.4/"
        "ttfautohint-1.8.4-win32.7z/download"
    ),
}


@dataclass(frozen=True)
class Asset:
    repo: str
    tag: str
    name: str
    url: str
    size: int | None
    digest: str | None


@dataclass(frozen=True)
class IosevkaTarget:
    upstream_family: str
    sarasa_group: str


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.environ.get("GITHUB_TOKEN")

    def release(self, repo: str, tag: str | None) -> dict[str, Any]:
        suffix = "latest" if tag is None else f"tags/{urllib.parse.quote(tag, safe='')}"
        url = f"https://api.github.com/repos/{repo}/releases/{suffix}"
        return self._json(url)

    def _json(self, url: str) -> dict[str, Any]:
        req = urllib.request.Request(url, headers=self._headers("application/vnd.github+json"))
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"GitHub API request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GitHub API request failed: {exc.reason}") from exc

    def download(self, url: str, dest: Path, expected_size: int | None, force: bool) -> None:
        if dest.exists() and not force:
            if expected_size is None or dest.stat().st_size == expected_size:
                print(f"cache hit: {dest}")
                return

        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, part_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".part", dir=dest.parent)
        os.close(fd)
        part = Path(part_name)
        req = urllib.request.Request(url, headers=self._headers("application/octet-stream"))

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or expected_size or 0)
                downloaded = 0
                next_notice = 32 * 1024 * 1024
                with part.open("wb") as out:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        if downloaded >= next_notice:
                            if total:
                                pct = downloaded / total * 100
                                print(f"  downloaded {downloaded // 1024 // 1024} MiB ({pct:.1f}%)")
                            else:
                                print(f"  downloaded {downloaded // 1024 // 1024} MiB")
                            next_notice += 32 * 1024 * 1024
            part.replace(dest)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"download failed for {url}: {exc.reason}") from exc
        finally:
            part.unlink(missing_ok=True)

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": "solemn-fonts/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="solemn-fonts")
    subparsers = parser.add_subparsers(required=True)

    download = subparsers.add_parser("download", help="download minimal Sarasa build inputs")
    download.add_argument("--sarasa-root", type=Path, default=DEFAULT_SARASA_ROOT)
    download.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    download.add_argument("--sources-dir", type=Path, help="where extracted font sources are materialized")
    download.add_argument("--iosevka-tag", help="pin an Iosevka release tag instead of latest")
    download.add_argument("--source-han-tag", help="pin a Source Han Sans release tag instead of latest")
    download.add_argument("--style-set", default="SS17", help="Iosevka style set, default: SS17")
    download.add_argument("--source-han-locale", default="SC", choices=("SC",))
    download.add_argument("--with-term", action="store_true", help="also fetch Iosevka Term sources")
    download.add_argument("--no-materialize", action="store_true", help="download archives only")
    download.add_argument("--force", action="store_true", help="redownload archives even if cached")
    download.add_argument("--dry-run", action="store_true", help="show selected release assets only")
    download.set_defaults(func=cmd_download)

    build = subparsers.add_parser("build", help="download inputs and build minimal Sarasa Mono SC TTFs")
    build.add_argument("--sarasa-root", type=Path, default=DEFAULT_SARASA_ROOT)
    build.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    build.add_argument("--sources-dir", type=Path, help="where extracted font sources are materialized")
    build.add_argument("--style-set", default="SS17", help="Iosevka style set to fetch, default: SS17")
    build.add_argument("--styles", nargs="+", default=list(DEFAULT_BUILD_STYLES), choices=IOSEVKA_STYLES)
    build.add_argument("--all-styles", action="store_true", help="build every Sarasa Mono style")
    build.add_argument("--hinted", action="store_true", help="build hinted TTFs; this is much heavier")
    build.add_argument("--skip-download", action="store_true", help="do not fetch missing source archives")
    build.add_argument("--skip-npm-install", action="store_true", help="do not run npm install")
    build.add_argument("--skip-tool-checks", action="store_true", help="skip external tool checks")
    build.add_argument("--dry-run", action="store_true", help="print actions without running them")
    build.set_defaults(func=cmd_build)
    return parser


def cmd_download(args: argparse.Namespace) -> int:
    client = GitHubClient()
    style_set = normalize_style_set(args.style_set)
    sources_dir = resolve_sources_dir(args.cache_dir, args.sources_dir)
    iosevka_targets = [IosevkaTarget(f"Iosevka{style_set}", "IosevkaN")]
    if args.with_term:
        iosevka_targets.append(IosevkaTarget(f"IosevkaTerm{style_set}", "IosevkaNTerm"))

    assets = collect_assets(
        client=client,
        iosevka_targets=iosevka_targets,
        iosevka_tag=args.iosevka_tag,
        source_han_tag=args.source_han_tag,
        source_han_locale=args.source_han_locale,
    )

    print_plan(assets, iosevka_targets, args.source_han_locale)
    if args.dry_run:
        return 0

    archives: dict[str, Path] = {}
    for key, asset in assets.items():
        archive = args.cache_dir / asset.repo.replace("/", "__") / asset.tag / asset.name
        print(f"downloading {asset.name}")
        client.download(asset.url, archive, asset.size, args.force)
        verify_digest(archive, asset.digest)
        archives[key] = archive

    materialized: list[str] = []
    if not args.no_materialize:
        for target in iosevka_targets:
            archive = archives[target.upstream_family]
            materialized.extend(materialize_iosevka(archive, target, sources_dir))
        materialized.extend(materialize_source_han_sans(archives["source-han-sans"], sources_dir))

    write_manifest(args.cache_dir, assets, materialized)
    if materialized:
        print(f"materialized {len(materialized)} files into {sources_dir}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    sarasa_root = args.sarasa_root.resolve()
    cache_dir = args.cache_dir.resolve()
    sources_dir = resolve_sources_dir(cache_dir, args.sources_dir)
    styles = list(IOSEVKA_STYLES if args.all_styles else args.styles)
    targets = build_targets(styles, hinted=args.hinted)
    build_env = os.environ.copy()
    build_env["SOLEMN_BUILD_STYLES"] = ",".join(styles)
    build_env["SOLEMN_HINTED"] = "1" if args.hinted else "0"
    build_env["SARASA_SOURCES_DIR"] = str(sources_dir)

    ensure_sarasa_checkout(sarasa_root)
    print("build plan:")
    print(f"  Sarasa root: {sarasa_root}")
    print(f"  styles: {', '.join(styles)}")
    print(f"  output: {'hinted TTF' if args.hinted else 'unhinted TTF'}")
    for target in targets:
        print(f"  target: {target}")

    if not args.skip_tool_checks:
        tool_path = ensure_ttfautohint(cache_dir, dry_run=args.dry_run)
        assumed_tools = {"ttfautohint"} if args.dry_run and tool_path is not None else set()
        if tool_path is not None:
            prepend_path(build_env, tool_path.parent)
        check_external_tools(
            hinted=args.hinted,
            env=build_env,
            dry_run=args.dry_run,
            assumed_tools=assumed_tools,
        )

    missing = missing_source_inputs(sources_dir, styles)
    if missing:
        print("missing source inputs:")
        for path in missing:
            print(f"  {path}")
        if args.skip_download:
            raise RuntimeError("source inputs are missing and --skip-download was passed")
        if args.dry_run:
            print("would run download to materialize missing source inputs")
        else:
            download_args = argparse.Namespace(
                sarasa_root=sarasa_root,
                cache_dir=cache_dir,
                sources_dir=sources_dir,
                iosevka_tag=None,
                source_han_tag=None,
                style_set=args.style_set,
                source_han_locale="SC",
                with_term=False,
                no_materialize=False,
                force=False,
                dry_run=False,
            )
            rc = cmd_download(download_args)
            if rc != 0:
                return rc

    ensure_sarasa_patch(sarasa_root, SARASA_PATCH_PATH, dry_run=args.dry_run)

    if args.skip_npm_install:
        print("skipping npm install")
    else:
        run_command(["npm", "install"], cwd=sarasa_root, env=build_env, dry_run=args.dry_run)

    run_command(["npm", "run", "build", "--", "solemn-mono-sc"], cwd=sarasa_root, env=build_env, dry_run=args.dry_run)
    return 0


def build_targets(styles: Iterable[str], hinted: bool) -> list[str]:
    infix = "TTF" if hinted else "TTF-Unhinted"
    return [f"out/{infix}/SarasaMonoSC-{style}.ttf" for style in styles]


def resolve_sources_dir(cache_dir: Path, requested: Path | None) -> Path:
    return (requested or cache_dir / DEFAULT_SOURCES_SUBDIR).resolve()


def ensure_sarasa_checkout(sarasa_root: Path) -> None:
    required = [sarasa_root / "package.json", sarasa_root / "verdafile.mjs", sarasa_root / "config.json"]
    missing = [path for path in required if not path.exists()]
    if missing:
        raise RuntimeError(
            "Sarasa-Gothic checkout is incomplete; missing " + ", ".join(str(p) for p in missing)
        )


def check_external_tools(
    hinted: bool,
    env: dict[str, str],
    dry_run: bool,
    assumed_tools: set[str] | None = None,
) -> None:
    required = ["node", "npm", "otf2ttf", "ttfautohint"]
    if hinted:
        required.append("otc2otf")

    assumed_tools = assumed_tools or set()
    missing: list[str] = []
    for tool in required:
        if tool in assumed_tools:
            continue
        if shutil.which(tool, path=env.get("PATH")):
            continue
        missing.append(tool)
    if missing and dry_run:
        print("missing external tools: " + ", ".join(missing))
        return
    if missing:
        raise RuntimeError(f"external dependency <{missing[0]}> not found on PATH")

    if dry_run:
        print("external tools are present")
        return

    proc = subprocess.run(["node", "--version"], check=False, capture_output=True, text=True, env=env)
    version = proc.stdout.strip()
    if proc.returncode != 0 or not version.startswith("v"):
        raise RuntimeError("could not determine Node.js version")
    major = int(version[1:].split(".", 1)[0])
    if major < 20:
        raise RuntimeError(f"Sarasa-Gothic requires Node.js >=20, found {version}")
    print(f"external tools are present; Node.js {version}")


def ensure_ttfautohint(cache_dir: Path, dry_run: bool) -> Path | None:
    existing = shutil.which("ttfautohint")
    if existing:
        return None

    platform_key = "win32" if sys.platform.startswith("win") else sys.platform
    url = TTFAUTOHINT_URLS.get(platform_key)
    if not url:
        raise RuntimeError(
            f"ttfautohint is not on PATH and no bundled download is configured for {sys.platform}"
        )

    tools_dir = cache_dir / "tools" / f"ttfautohint-{TTFAUTOHINT_VERSION}"
    executable = find_executable(tools_dir, "ttfautohint")
    if executable:
        print(f"using cached ttfautohint: {executable}")
        return executable

    archive_suffix = ".tar.gz" if platform_key == "darwin" else ".7z"
    archive = cache_dir / "tools" / f"ttfautohint-{TTFAUTOHINT_VERSION}-{platform_key}{archive_suffix}"
    if dry_run:
        print(f"would download ttfautohint from {url}")
        print(f"would extract ttfautohint into {tools_dir}")
        return tools_dir / ("ttfautohint.exe" if platform_key == "win32" else "ttfautohint")

    download_plain_url(url, archive)
    extract_ttfautohint_archive(archive, tools_dir, platform_key)
    executable = find_executable(tools_dir, "ttfautohint")
    if not executable:
        raise RuntimeError(f"ttfautohint executable was not found after extracting {archive}")
    print(f"downloaded ttfautohint: {executable}")
    return executable


def download_plain_url(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"cache hit: {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, part_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".part", dir=dest.parent)
    os.close(fd)
    part = Path(part_name)
    req = urllib.request.Request(url, headers={"User-Agent": "solemn-fonts/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response, part.open("wb") as out:
            shutil.copyfileobj(response, out)
        part.replace(dest)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"download failed for {url}: {exc.reason}") from exc
    finally:
        part.unlink(missing_ok=True)


def extract_ttfautohint_archive(archive: Path, dest: Path, platform_key: str) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    if platform_key == "darwin":
        with tarfile.open(archive, "r:gz") as tf:
            safe_extract_tar(tf, dest)
        return

    if platform_key == "win32":
        seven_zip = shutil.which("7z") or shutil.which("7za")
        if not seven_zip:
            raise RuntimeError("ttfautohint win32 archive is .7z; install 7z/7za or put ttfautohint on PATH")
        run_command([seven_zip, "x", f"-o{dest}", str(archive)], cwd=PROJECT_ROOT, env=os.environ.copy(), dry_run=False)
        return

    raise RuntimeError(f"unsupported ttfautohint archive platform: {platform_key}")


def safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if dest_resolved not in target.parents and target != dest_resolved:
            raise RuntimeError(f"refusing to extract unsafe path from {tf.name}: {member.name}")
    tf.extractall(dest)


def find_executable(root: Path, name: str) -> Path | None:
    if not root.exists():
        return None
    names = {name, f"{name}.exe"}
    for path in root.rglob("*"):
        if path.is_file() and path.name in names:
            if sys.platform.startswith("win") or os.access(path, os.X_OK):
                return path
            path.chmod(path.stat().st_mode | 0o755)
            return path
    return None


def prepend_path(env: dict[str, str], path: Path) -> None:
    existing = env.get("PATH", "")
    env["PATH"] = str(path) if not existing else f"{path}{os.pathsep}{existing}"


def missing_source_inputs(sources: Path, styles: Iterable[str]) -> list[Path]:
    required: list[Path] = []
    for style in styles:
        required.append(sources / "IosevkaN" / f"IosevkaN-{style}.ttf")

    shs_weights = sorted({SOURCE_HAN_SANS_WEIGHTS[upright_style(style)] for style in styles})
    for weight in shs_weights:
        required.append(sources / "shs" / f"SourceHanSansSC-{weight}.otf")
    return [path for path in required if not path.exists()]


def upright_style(style: str) -> str:
    if style == "Italic":
        return "Regular"
    if style.endswith("Italic"):
        return style.removesuffix("Italic")
    return style


def ensure_sarasa_patch(sarasa_root: Path, patch_path: Path, dry_run: bool) -> None:
    if is_sarasa_patch_applied(sarasa_root):
        print("Sarasa minimal Mono SC patch is already applied")
        return
    if not patch_path.exists():
        raise RuntimeError(f"Sarasa patch file is missing: {patch_path}")
    if dry_run:
        print(f"would apply Sarasa patch: {patch_path}")
        return

    check = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=sarasa_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        detail = (check.stderr or check.stdout).strip()
        raise RuntimeError(f"could not apply Sarasa patch cleanly: {detail}")
    run_command(["git", "apply", str(patch_path)], cwd=sarasa_root, env=os.environ.copy(), dry_run=False)


def is_sarasa_patch_applied(sarasa_root: Path) -> bool:
    config_path = sarasa_root / "config.json"
    verdafile = sarasa_root / "verdafile.mjs"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        verdafile_text = verdafile.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        return False
    return (
        config.get("familyOrder") == ["Mono"]
        and config.get("subfamilyOrder") == ["SC"]
        and config.get("styleOrder") == list(DEFAULT_BUILD_STYLES)
        and "SARASA_SOURCES_DIR" in verdafile_text
        and SARASA_DIRECT_SHS_PATCH_MARKER in verdafile_text
        and "solemn-mono-sc" in verdafile_text
    )


def run_command(command: list[str], cwd: Path, env: dict[str, str], dry_run: bool) -> None:
    printable = " ".join(command)
    if dry_run:
        print(f"would run in {cwd}: {printable}")
        return
    print(f"running in {cwd}: {printable}")
    try:
        subprocess.run(command, cwd=cwd, env=env, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"command failed with exit code {exc.returncode}: {printable}") from exc


def collect_assets(
    client: GitHubClient,
    iosevka_targets: Iterable[IosevkaTarget],
    iosevka_tag: str | None,
    source_han_tag: str | None,
    source_han_locale: str,
) -> dict[str, Asset]:
    iosevka_release = client.release("be5invis/Iosevka", iosevka_tag)
    source_han_release = client.release("adobe-fonts/source-han-sans", source_han_tag)

    assets: dict[str, Asset] = {}
    for target in iosevka_targets:
        assets[target.upstream_family] = find_asset(
            "be5invis/Iosevka",
            iosevka_release,
            lambda name, family=target.upstream_family: (
                name.startswith(f"PkgTTF-{family}-")
                and name.endswith(".zip")
                and "Unhinted" not in name
            ),
            f"PkgTTF-{target.upstream_family}-*.zip",
        )

    locale_asset = f"SourceHanSans{source_han_locale}"
    assets["source-han-sans"] = find_asset(
        "adobe-fonts/source-han-sans",
        source_han_release,
        lambda name: name.endswith(f"_{locale_asset}.zip") and "HW" not in name,
        f"*_{locale_asset}.zip",
    )
    return assets


def find_asset(
    repo: str,
    release: dict[str, Any],
    predicate: Any,
    description: str,
) -> Asset:
    matches = [a for a in release.get("assets", []) if predicate(a["name"])]
    if len(matches) != 1:
        names = ", ".join(a.get("name", "<unnamed>") for a in matches) or "none"
        raise RuntimeError(
            f"expected exactly one asset matching {description} in {repo} "
            f"{release.get('tag_name')}; got {names}"
        )
    item = matches[0]
    return Asset(
        repo=repo,
        tag=release["tag_name"],
        name=item["name"],
        url=item["browser_download_url"],
        size=item.get("size"),
        digest=item.get("digest"),
    )


def normalize_style_set(value: str) -> str:
    style = value.upper()
    if style.startswith("IOSEVKA"):
        raise RuntimeError("pass only the style-set suffix, for example SS17")
    if not style.startswith("SS"):
        raise RuntimeError("only Iosevka SS style sets are supported here, for example SS17")
    return style


def print_plan(
    assets: dict[str, Asset],
    iosevka_targets: Iterable[IosevkaTarget],
    source_han_locale: str,
) -> None:
    print("selected assets:")
    for target in iosevka_targets:
        asset = assets[target.upstream_family]
        size = format_size(asset.size)
        print(f"  Iosevka {target.upstream_family} -> {target.sarasa_group}: {asset.name} ({size})")
    shs = assets["source-han-sans"]
    print(f"  Source Han Sans {source_han_locale}: {shs.name} ({format_size(shs.size)})")


def format_size(size: int | None) -> str:
    if size is None:
        return "unknown size"
    return f"{size / 1024 / 1024:.1f} MiB"


def verify_digest(path: Path, digest: str | None) -> None:
    if not digest:
        return
    algorithm, _, expected = digest.partition(":")
    if algorithm.lower() != "sha256" or not expected:
        return
    actual = sha256(path)
    if actual.lower() != expected.lower():
        raise RuntimeError(f"sha256 mismatch for {path}: expected {expected}, got {actual}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def materialize_iosevka(archive: Path, target: IosevkaTarget, sources_dir: Path) -> list[str]:
    outputs: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        for style in IOSEVKA_STYLES:
            source_name = f"{target.upstream_family}-{style}.ttf"
            dest = sources_dir / target.sarasa_group / f"{target.sarasa_group}-{style}.ttf"
            copy_zip_member(zf, source_name, dest)
            outputs.append(str(dest))
    return outputs


def materialize_source_han_sans(archive: Path, sources_dir: Path) -> list[str]:
    outputs: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        for sarasa_weight, source_weight in SOURCE_HAN_SANS_WEIGHTS.items():
            source_name = f"SourceHanSansSC-{source_weight}.otf"
            dest = sources_dir / "shs" / source_name
            copy_zip_member(zf, source_name, dest)
            outputs.append(str(dest))
            if sarasa_weight != source_weight:
                alias = sources_dir / "shs" / f"SourceHanSansSC-{sarasa_weight}.otf"
                shutil.copyfile(dest, alias)
                outputs.append(str(alias))
    return outputs


def copy_zip_member(zf: zipfile.ZipFile, basename: str, dest: Path) -> None:
    info = find_zip_member(zf, basename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with zf.open(info) as src, tmp.open("wb") as out:
        shutil.copyfileobj(src, out)
    tmp.replace(dest)


def find_zip_member(zf: zipfile.ZipFile, basename: str) -> zipfile.ZipInfo:
    matches = [
        info
        for info in zf.infolist()
        if not info.is_dir() and Path(info.filename).name == basename
    ]
    if len(matches) == 1:
        return matches[0]
    available = sorted({Path(info.filename).name for info in zf.infolist() if not info.is_dir()})
    hint = ", ".join(name for name in available[:12])
    raise RuntimeError(f"could not find {basename} in {zf.filename}; first entries: {hint}")


def write_manifest(cache_dir: Path, assets: dict[str, Asset], materialized: list[str]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "assets": {key: asset.__dict__ for key, asset in sorted(assets.items())},
        "materialized": materialized,
    }
    path = cache_dir / "manifest.json"
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
