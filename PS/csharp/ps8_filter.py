#!/usr/bin/env python3
"""
ps8_filter.py  (C#)

Input : ps7/ps7_filtered.csv
Check : dotnet test --collect:"XPlat Code Coverage" で line coverage >= 70%
        - git clone --depth=1
        - テスト .csproj を検出 → coverlet.collector を未参照なら動的に追加
        - singularity exec dotnet-sdk8.sif dotnet restore
        - singularity exec dotnet-sdk8.sif dotnet test --collect:"XPlat Code Coverage"
        - TestResults/**/coverage.cobertura.xml を解析
Output: ps8/ps8_filtered.csv  (通過分のみ、cov_lines カラム付き)
        ps8/progress.log      (再開用)
"""

import csv
import glob
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

BASE_DIR   = Path(__file__).parent
INPUT_CSV  = BASE_DIR / "ps7" / "ps7_filtered.csv"
OUTPUT_DIR = BASE_DIR / "ps8"
OUTPUT_CSV = OUTPUT_DIR / "ps8_filtered.csv"
PROGRESS   = OUTPUT_DIR / "progress.log"
REPOS_TMP  = BASE_DIR / "repos_tmp"

SIF_PATH    = Path("/work/rintaro-k/research/containers/dotnet-sdk8.sif")
SINGULARITY = "/opt/singularity/3.9.6/bin/singularity"

CLONE_TIMEOUT   = 120
RESTORE_TIMEOUT = 300
TEST_TIMEOUT    = 600
LINE_COV_MIN    = 70.0

TEST_PKG_RE = re.compile(
    r'xunit|nunit|mstest\.testframework|microsoft\.net\.test\.sdk',
    re.IGNORECASE,
)


def _run(cmd, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def singularity_exec(cmd: list, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    """--pwd でコンテナ内の作業ディレクトリを明示指定する"""
    full_cmd = [
        SINGULARITY, "exec",
        "--bind", "/work/rintaro-k:/work/rintaro-k",
        "--pwd", str(cwd),
        str(SIF_PATH),
    ] + cmd
    return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)


def find_test_csproj(dest: Path) -> list[Path]:
    """テストフレームワークを参照している .csproj を返す"""
    result = []
    for csproj in dest.rglob("*.csproj"):
        try:
            if TEST_PKG_RE.search(csproj.read_text(errors="replace")):
                result.append(csproj)
        except Exception:
            pass
    return result


def ensure_coverlet(csproj: Path, dest: Path):
    """coverlet.collector が未参照なら追加する"""
    try:
        content = csproj.read_text(errors="replace")
    except Exception:
        return
    if "coverlet.collector" in content.lower():
        return
    singularity_exec(
        ["dotnet", "add", str(csproj), "package", "coverlet.collector",
         "--no-restore"],
        cwd=dest, timeout=60,
    )


def parse_cobertura(xml_path: Path) -> tuple[int, int]:
    """(lines_covered, lines_valid) を返す"""
    try:
        root = ET.parse(xml_path).getroot()
        covered = int(root.attrib.get("lines-covered", 0))
        valid   = int(root.attrib.get("lines-valid",   0))
        return covered, valid
    except Exception:
        return 0, 0


def run_ps8(dest: Path) -> tuple[bool, dict | None, str]:
    # テストプロジェクトを検出
    test_projs = find_test_csproj(dest)
    if not test_projs:
        return False, None, "no_test_project"

    # coverlet.collector を各テストプロジェクトに追加
    for csproj in test_projs:
        ensure_coverlet(csproj, dest)

    # dotnet restore
    print("    dotnet restore ...", end=" ", flush=True)
    try:
        r = singularity_exec(
            ["dotnet", "restore", "--nologo", "-v", "q",
             "--ignore-failed-sources"],
            cwd=dest, timeout=RESTORE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        return False, None, "restore_timeout"
    if r.returncode != 0:
        print(f"FAIL (exit={r.returncode})")
        return False, None, f"restore_failed(exit={r.returncode})"
    print("OK")

    # dotnet test with XPlat Code Coverage
    results_dir = dest / "TestResults"
    print("    dotnet test --collect ...", end=" ", flush=True)
    try:
        r = singularity_exec(
            ["dotnet", "test",
             "--no-restore", "--nologo", "-v", "q",
             "--collect", "XPlat Code Coverage",
             "--results-directory", str(results_dir)],
            cwd=dest, timeout=TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        return False, None, "test_timeout"

    if r.returncode != 0:
        print(f"FAIL (exit={r.returncode})")
        return False, None, f"test_failed(exit={r.returncode})"

    # coverage.cobertura.xml を収集
    xml_files = list(results_dir.rglob("coverage.cobertura.xml"))
    if not xml_files:
        print("FAIL (no coverage xml)")
        return False, None, "no_coverage_xml"

    # 複数テストプロジェクトの場合は合算
    total_covered = total_valid = 0
    for xml_file in xml_files:
        covered, valid = parse_cobertura(xml_file)
        total_covered += covered
        total_valid   += valid

    if total_valid == 0:
        print("FAIL (0 lines measured)")
        return False, None, "zero_lines"

    pct = total_covered / total_valid * 100
    print(f"OK (lines={pct:.1f}%)")
    return True, {"lines": pct}, "ok"


def load_progress() -> dict[str, str]:
    done = {}
    if PROGRESS.exists():
        for line in PROGRESS.read_text().splitlines():
            if "," in line:
                repo, _, status = line.partition(",")
                done[repo.strip()] = status.strip()
    return done


def save_progress(repo: str, status: str):
    with open(PROGRESS, "a") as f:
        f.write(f"{repo},{status}\n")


def main():
    if not SIF_PATH.exists():
        print(f"ERROR: コンテナが見つかりません: {SIF_PATH}")
        print("  /work/rintaro-k/research/containers/pull-containers.sh を実行してください。")
        return

    OUTPUT_DIR.mkdir(exist_ok=True)
    REPOS_TMP.mkdir(exist_ok=True)

    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader     = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows       = list(reader)

    print("=" * 60)
    print("PS8 (C#): dotnet test XPlat Code Coverage (line >= 70%)")
    print(f"Input : {INPUT_CSV}  ({len(rows)} 件)")
    print(f"Output: {OUTPUT_CSV}")
    print(f"SIF   : {SIF_PATH}")
    print("=" * 60 + "\n")

    done = load_progress()

    out_fields = fieldnames + ["cov_lines"]
    out_exists = OUTPUT_CSV.exists() and OUTPUT_CSV.stat().st_size > 0
    outfile    = open(OUTPUT_CSV, "a", newline="", encoding="utf-8")
    writer     = csv.DictWriter(outfile, fieldnames=out_fields)
    if not out_exists:
        writer.writeheader()

    passed = failed = skipped = 0

    for i, row in enumerate(rows, 1):
        repo = row["name"]
        print(f"[{i}/{len(rows)}] {repo}", flush=True)

        if repo in done:
            print(f"  skip ({done[repo]})")
            skipped += 1
            if done[repo].startswith("pass"):
                passed += 1
            continue

        owner, repo_name = repo.split("/", 1)
        dest = REPOS_TMP / owner / repo_name
        shutil.rmtree(dest, ignore_errors=True)

        print("  clone ...", end=" ", flush=True)
        try:
            r = _run(
                ["git", "clone", "--depth=1",
                 f"https://github.com/{repo}.git", str(dest)],
                timeout=CLONE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            print("TIMEOUT")
            shutil.rmtree(dest, ignore_errors=True)
            save_progress(repo, "fail_clone_timeout")
            failed += 1
            continue
        if r.returncode != 0:
            print("FAIL")
            save_progress(repo, "fail_clone")
            failed += 1
            continue
        print("OK")

        try:
            ok, cov, reason = run_ps8(dest)
        except Exception as e:
            ok, cov, reason = False, None, f"error({e})"
        finally:
            shutil.rmtree(dest, ignore_errors=True)

        if not ok:
            print(f"  => SKIP ({reason})")
            save_progress(repo, f"fail_{reason}")
            failed += 1
            continue

        if cov["lines"] < LINE_COV_MIN:
            print(f"  => SKIP (lines={cov['lines']:.1f}% < {LINE_COV_MIN}%)")
            save_progress(repo, f"fail_low_coverage(lines={cov['lines']:.1f}%)")
            failed += 1
            continue

        row_out = dict(row)
        row_out["cov_lines"] = f"{cov['lines']:.2f}"
        for col in out_fields:
            row_out.setdefault(col, "")
        writer.writerow(row_out)
        outfile.flush()
        save_progress(repo, f"pass(lines={cov['lines']:.1f}%)")
        passed += 1
        print(f"  => SAVED  lines={cov['lines']:.1f}%")

    outfile.close()
    print(f"\n=== PS8 (C#) 完了 ===")
    print(f"Total: {len(rows)}  Pass: {passed}  Fail: {failed}  Skip: {skipped}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
