"""prerelease用の2種類の実行ファイルを構築し、限定した配布物だけを梱包する。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import subprocess
import tarfile

REPOSITORY = Path(__file__).resolve().parent.parent
TARGETS = {"macos-arm64": "aarch64-macos", "linux-x86_64": "x86_64-linux-musl"}
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")
DEPENDENCY_LICENSES = {"LICENSE-utf8proc.md": "vendor/utf8proc/LICENSE.md", "LICENSE-libyaml.txt": "vendor/libyaml/License"}
TAG_PATTERN = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)-(?:alpha|beta|rc)\.(?:0|[1-9][0-9]*)", re.ASCII)
TARGET_FILES = {"zigurui", "target.json"}
PRIVATE_MARKERS = (b"/Users/", b"/home/", b"/root/", b"/private/var/folders/", b".debug_info", b"__DWARF")


def target_matrix() -> dict:
    """正式なtarget定義から、GitHub Actionsのmatrixと一意なartifact名を作る。"""
    return {"include": [{"target": target, "artifact": f"release-target-{target}"} for target in TARGETS]}


def validate_tag(tag: str) -> str:
    """archive名に使えるalpha・beta・rcのSemVerタグだけを受け付ける。"""
    if len(tag) > 64 or TAG_PATTERN.fullmatch(tag) is None:
        raise ValueError("tag must be vX.Y.Z-alpha.N, vX.Y.Z-beta.N or vX.Y.Z-rc.N (at most 64 characters)")
    return tag


def archive_stem(tag: str, target: str) -> str:
    validate_tag(tag)
    if target not in TARGETS:
        raise ValueError(f"unsupported target: {target}")
    return f"zigurui-{tag}-{target}"


def validate_binary(target: str, data: bytes) -> None:
    """CPUと実行形式を検査する。対象OS上での動作確認の代わりにはならない。"""
    if target == "macos-arm64":
        if len(data) < 32 or data[:4] != b"\xcf\xfa\xed\xfe" or struct.unpack_from("<I", data, 4)[0] != 0x0100000C:
            raise ValueError("expected Mach-O arm64 binary")
        if struct.unpack_from("<I", data, 12)[0] != 2:
            raise ValueError("expected Mach-O executable")
    elif target == "linux-x86_64":
        if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 62:
            raise ValueError("expected ELF64 little-endian x86_64 binary")
        if struct.unpack_from("<H", data, 16)[0] not in (2, 3):
            raise ValueError("expected ELF executable")
        offset = struct.unpack_from("<Q", data, 32)[0]
        size, count = struct.unpack_from("<HH", data, 54)
        if size < 56 or offset > len(data) or count > (len(data) - offset) // size:
            raise ValueError("invalid ELF program headers")
        if any(struct.unpack_from("<I", data, offset + i * size)[0] == 3 for i in range(count)):
            raise ValueError("Linux binary must not require a dynamic interpreter")
    else:
        raise ValueError(f"unsupported target: {target}")


def validate_payload(target: str, data: bytes, repository: Path = REPOSITORY) -> None:
    """producer固有パスと、別runnerでも識別できる私的パス・デバッグ情報を拒否する。"""
    validate_binary(target, data)
    markers = (*PRIVATE_MARKERS, str(repository.resolve()).encode(), str(Path.home()).encode())
    if any(len(marker) > 5 and marker in data for marker in markers):
        raise ValueError("binary contains a private path or debug information; build with -Dstrip=true")


def target_metadata(tag: str, target: str, digest: str) -> dict:
    """runnerの個人情報を含まない有限のartifact契約を返す。"""
    validate_tag(tag)
    if not isinstance(target, str) or target not in TARGETS:
        raise ValueError("unsupported target in artifact metadata")
    return {"schema_version": 1, "tag": tag, "target": target, "zig_target": TARGETS[target], "binary_sha256": digest}


def stage_target(tag: str, target: str, binary: Path, output: Path, repository: Path = REPOSITORY) -> Path:
    """検証した単一binaryとmetadataだけを、新規artifactディレクトリへ書き出す。"""
    metadata = target_metadata(tag, target, "")
    if not binary.is_file() or binary.is_symlink():
        raise ValueError("target binary must be a regular file")
    data = binary.read_bytes()
    validate_payload(target, data, repository)
    metadata["binary_sha256"] = hashlib.sha256(data).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    (output / "zigurui").write_bytes(data)
    (output / "zigurui").chmod(0o755)
    (output / "target.json").write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return output


def read_target(tag: str, directory: Path, repository: Path = REPOSITORY) -> tuple[str, Path]:
    """downloadしたartifactの許可ファイル・target・checksumを検査する。"""
    if directory.is_symlink() or not directory.is_dir() or {path.name for path in directory.iterdir()} != TARGET_FILES:
        raise ValueError("target artifact must contain exactly zigurui and target.json")
    for name in TARGET_FILES:
        if not (directory / name).is_file() or (directory / name).is_symlink():
            raise ValueError("target artifact must contain only regular files")
    metadata = json.loads((directory / "target.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or not isinstance(metadata.get("binary_sha256"), str):
        raise ValueError("invalid target metadata")
    expected = target_metadata(tag, metadata.get("target"), metadata["binary_sha256"])
    if type(metadata.get("schema_version")) is not int or metadata != expected:
        raise ValueError("target metadata does not match expected schema, tag or triple")
    binary = directory / "zigurui"
    data = binary.read_bytes()
    if hashlib.sha256(data).hexdigest() != metadata["binary_sha256"]:
        raise ValueError("target binary checksum mismatch")
    validate_payload(metadata["target"], data, repository)
    return metadata["target"], binary


def aggregate(tag: str, inputs: Path, output: Path, repository: Path = REPOSITORY) -> Path:
    """全targetが1件ずつ揃った場合だけ梱包する。欠落・重複・未知targetは拒否する。"""
    validate_tag(tag)
    if inputs.is_symlink() or not inputs.is_dir():
        raise ValueError("target artifact input must be a directory")
    binaries = {}
    for directory in sorted(inputs.iterdir()):
        target, binary = read_target(tag, directory, repository)
        if target in binaries:
            raise ValueError(f"duplicate target artifact: {target}")
        binaries[target] = binary
    if set(binaries) != set(TARGETS):
        raise ValueError("all release targets must be present before packaging")
    return package(tag, binaries, output, repository)


def package(tag: str, binaries: dict[str, Path], output: Path, repository: Path = REPOSITORY) -> Path:
    """新規outputにarchiveとSHA256SUMSを作る。既存の出力先や任意ファイルは上書きしない。"""
    validate_tag(tag)
    if set(binaries) != set(TARGETS):
        raise ValueError("both release targets are required")
    documents = {"README.md": (repository / "README.md").read_bytes()}
    for name in LICENSE_NAMES:
        if (repository / name).is_file():
            documents[name] = (repository / name).read_bytes()
    for name, source in DEPENDENCY_LICENSES.items():
        documents[name] = (repository / source).read_bytes()
    payloads = {}
    for target, binary in binaries.items():
        data = binary.read_bytes()
        validate_payload(target, data, repository)
        payloads[target] = data
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    checksums = []
    for target in TARGETS:
        stem = archive_stem(tag, target)
        archive = output / f"{stem}.tar.gz"
        # 所有者名、生成時刻、gzipの元ファイル名を保存せず、同じ入力から同じarchiveを作る。
        with archive.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as tar:
                for name, data in {"zigurui": payloads[target], **documents}.items():
                    info = tarfile.TarInfo(f"{stem}/{name}")
                    info.size = len(data)
                    info.mode = 0o755 if name == "zigurui" else 0o644
                    info.mtime = info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    tar.addfile(info, io.BytesIO(data))
        checksums.append(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    (output / "SHA256SUMS").write_text("".join(checksums), encoding="ascii")
    verify(tag, output)
    return output


def verify(tag: str, directory: Path) -> None:
    """配布物の名前・checksum・archive構造・実行形式を検査し、不一致は拒否する。"""
    validate_tag(tag)
    names = {f"{archive_stem(tag, target)}.tar.gz": target for target in TARGETS}
    if {path.name for path in directory.iterdir()} != set(names) | {"SHA256SUMS"}:
        raise ValueError("release directory must contain exactly two archives and SHA256SUMS")
    sums = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if match is None or match[2] in sums:
            raise ValueError("invalid or duplicate checksum line")
        sums[match[2]] = match[1]
    if set(sums) != set(names):
        raise ValueError("checksum file does not match expected assets")
    for name, target in names.items():
        archive = directory / name
        if archive.is_symlink() or hashlib.sha256(archive.read_bytes()).hexdigest() != sums[name]:
            raise ValueError("archive checksum mismatch")
        stem = archive_stem(tag, target)
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            expected_required = {f"{stem}/zigurui", f"{stem}/README.md"} | {f"{stem}/{name}" for name in DEPENDENCY_LICENSES}
            allowed = expected_required | {f"{stem}/{license_name}" for license_name in LICENSE_NAMES}
            actual = [member.name for member in members]
            if not expected_required <= set(actual) <= allowed or len(actual) != len(set(actual)):
                raise ValueError("unexpected archive contents")
            for member in members:
                if not member.isfile() or member.uid != 0 or member.gid != 0 or member.uname or member.gname:
                    raise ValueError("archive must contain only regular files without local owner metadata")
            binary = tar.getmember(f"{stem}/zigurui")
            if binary.mode != 0o755:
                raise ValueError("archive binary must be executable")
            validate_payload(target, tar.extractfile(binary).read())


def compile_target(target: str, tag: str) -> Path:
    """配布用strip binaryを構築する。中間出力は既存の検証用領域へ集約して再利用する。"""
    if target not in TARGETS:
        raise ValueError(f"unsupported target: {target}")
    prefix = REPOSITORY / ".local" / "release-feasibility" / "target-builds" / target
    subprocess.run(["mise", "exec", "--", "zig", "build", "-Doptimize=ReleaseFast", "-Dstrip=true", f"-Dversion={validate_tag(tag)}", f"-Dtarget={TARGETS[target]}", "--prefix", str(prefix)], cwd=REPOSITORY, check=True)
    return prefix / "bin" / "zigurui"


def build_target(tag: str, target: str, output: Path) -> Path:
    """1targetだけをビルドしてartifact化する。公開や他targetのビルドは行わない。"""
    target_metadata(tag, target, "")
    if output.exists():
        raise FileExistsError(output)
    return stage_target(tag, target, compile_target(target, tag), output)


def build(tag: str, output: Path) -> Path:
    """ローカル用途で両targetを順にビルド・梱包する。CIはbuild-targetを並列実行する。"""
    validate_tag(tag)
    if output.exists():
        raise FileExistsError(output)
    return package(tag, {target: compile_target(target, tag) for target in TARGETS}, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-tag")
    validate.add_argument("tag")
    targets = commands.add_parser("targets")
    targets.add_argument("--github-output", type=Path)
    target_parser = commands.add_parser("build-target")
    target_parser.add_argument("--tag", required=True)
    target_parser.add_argument("--target", choices=list(TARGETS), required=True)
    target_parser.add_argument("--output", type=Path, required=True)
    aggregate_parser = commands.add_parser("aggregate")
    aggregate_parser.add_argument("--tag", required=True)
    aggregate_parser.add_argument("--inputs", type=Path, required=True)
    aggregate_parser.add_argument("--output", type=Path, required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--tag", required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--tag", required=True)
    verify_parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate-tag":
            validate_tag(args.tag)
        elif args.command == "targets":
            matrix = json.dumps(target_matrix(), separators=(",", ":"))
            if args.github_output:
                with args.github_output.open("a", encoding="utf-8") as output:
                    output.write(f"matrix={matrix}\n")
            print(matrix)
        elif args.command == "build-target":
            print(build_target(args.tag, args.target, args.output))
        elif args.command == "aggregate":
            print(aggregate(args.tag, args.inputs, args.output))
        elif args.command == "build":
            print(build(args.tag, args.output))
        else:
            verify(args.tag, args.directory)
    except (ValueError, OSError, subprocess.CalledProcessError, tarfile.TarError) as error:
        parser.exit(1, f"release packaging: {error}\n")


if __name__ == "__main__":
    main()
