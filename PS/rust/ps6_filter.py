#!/usr/bin/env python3
"""
ps6_filter.py  (Rust)

Input : ps5/ps5_filtered.csv
Check : cargo-tarpaulin (Singularity コンテナ) でテスト実行 & line coverage >= 70%
        - git clone --depth=1
        - singularity exec rust-tarpaulin.sif cargo tarpaulin --out Json
        - tarpaulin-report.json の coverage (0.0-1.0) * 100 >= 70 を確認
Output: ps6/ps6_filtered.csv  (通過分のみ、cov_lines カラム付き)
        ps6/progress.log      (再開用)
"""

import csv
import json
import shutil
import subprocess
from pathlib import Path

BASE_DIR   = Path(__file__).parent
INPUT_CSV  = BASE_DIR / "ps5" / "ps5_filtered.csv"
OUTPUT_DIR = BASE_DIR / "ps6"
OUTPUT_CSV = OUTPUT_DIR / "ps6_filtered.csv"
PROGRESS   = OUTPUT_DIR / "progress.log"
REPOS_TMP  = BASE_DIR / "repos_tmp"

SIF_PATH = Path("/work/rintaro-k/research/containers/rust-tarpaulin.sif")
SINGULARITY = "/opt/singularity/3.9.6/bin/singularity"

CLONE_TIMEOUT     = 120
TARPAULIN_TIMEOUT = 600   # コンパイル込みで最大 10 分
LINE_COV_MIN      = 70.0


def _run(cmd, cwd=None, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )


def singularity_exec(cmd: list, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    full_cmd = [
        SINGULARITY, "exec",
        "--bind", "/work/rintaro-k:/work/rintaro-k",
        str(SIF_PATH),
    ] + cmd
    return subprocess.run(
        full_cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )


def run_ps6(dest: Path) -> tuple[bool, dict | None, str]:
    if not (dest / "Cargo.toml").exists():
        return False, None, "no_cargo_toml"

    cov_dir = dest / "coverage"
    cov_dir.mkdir(exist_ok=True)

    print("    cargo tarpaulin (singularity) ...", end=" ", flush=True)
    try:
        r = singularity_exec(
            ["cargo", "tarpaulin",
             "--out", "Json",
             "--output-dir", str(cov_dir),
             "--timeout", "300",
             "--skip-clean"],
            cwd=dest,
            timeout=TARPAULIN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        return False, None, "timeout"

    if r.returncode != 0:
        print(f"FAIL (exit={r.returncode})")
        return False, None, f"tarpaulin_failed(exit={r.returncode})"

    report_path = cov_dir / "tarpaulin-report.json"
    if not report_path.exists():
        print("FAIL (no report)")
        return False, None, "no_tarpaulin_report"

    try:
        with open(report_path) as f:
            data = json.load(f)
        pct = float(data.get("coverage", 0))  # tarpaulin は既にパーセント値 (0-100)
    except Exception as e:
        print(f"FAIL (parse: {e})")
        return False, None, "report_parse_error"

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
    print("PS6 (Rust): cargo-tarpaulin チェック (line coverage >= 70%)")
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
            ok, cov, reason = run_ps6(dest)
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
    print(f"\n=== PS6 (Rust) 完了 ===")
    print(f"Total: {len(rows)}  Pass: {passed}  Fail: {failed}  Skip: {skipped}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
