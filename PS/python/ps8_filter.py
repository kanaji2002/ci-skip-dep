#!/usr/bin/env python3
"""
ps8_filter.py  (Python)

Input : ps7/ps7_filtered.csv
Check : pytest-cov でテスト実行 & line coverage >= 70%
        - git clone --depth=1
        - venv 作成 → pytest + pytest-cov インストール
        - プロジェクト依存のインストール
        - pytest --cov=. --cov-report=json --tb=no -q
        - coverage.json の totals.percent_covered >= 70 を確認
Output: ps8/ps8_filtered.csv  (通過分のみ、cov_lines カラム付き)
        ps8/progress.log      (再開用)
"""

import csv
import json
import shutil
import subprocess
import venv
from pathlib import Path

BASE_DIR   = Path(__file__).parent
INPUT_CSV  = BASE_DIR / "ps7" / "ps7_filtered.csv"
OUTPUT_DIR = BASE_DIR / "ps8-yobi-again"
OUTPUT_CSV = OUTPUT_DIR / "ps8_filtered-yobi-again.csv"
PROGRESS   = OUTPUT_DIR / "progress.log"
REPOS_TMP  = BASE_DIR / "repos_tmp"

CLONE_TIMEOUT   = 120
INSTALL_TIMEOUT = 300
PYTEST_TIMEOUT  = 300
LINE_COV_MIN    = 70.0


def _run(cmd, cwd=None, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )


def run_ps8(dest: Path) -> tuple[bool, dict | None, str]:
    venv_dir = dest / ".venv_ps8"

    try:
        venv.create(str(venv_dir), with_pip=True, clear=True)
    except Exception as e:
        return False, None, f"venv_failed({e})"

    pip    = str(venv_dir / "bin" / "pip")
    python = str(venv_dir / "bin" / "python")

    # pytest + pytest-cov インストール
    r = _run([pip, "install", "--quiet", "pytest", "pytest-cov"], timeout=INSTALL_TIMEOUT)
    if r.returncode != 0:
        return False, None, "pytest_install_failed"

    # プロジェクト依存インストール (複数パターンを順に試す)
    for extra in [[".[test,dev]"], [".[test]"], [".[dev]"], ["."]]:
        r = _run([pip, "install", "--quiet", "-e"] + extra, cwd=dest, timeout=INSTALL_TIMEOUT)
        if r.returncode == 0:
            break
    else:
        # pyproject.toml での install が全滅なら requirements*.txt を試す
        for req in ["requirements-test.txt", "requirements-dev.txt", "requirements.txt"]:
            if (dest / req).exists():
                _run([pip, "install", "--quiet", "-r", req], cwd=dest, timeout=INSTALL_TIMEOUT)
                break

    # pytest 実行
    print("    pytest --cov ...", end=" ", flush=True)
    try:
        r = _run(
            [python, "-m", "pytest",
             "--cov=.", "--cov-report=json", "--tb=no", "-q", "--no-header"],
            cwd=dest, timeout=PYTEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        return False, None, "timeout"

  
    if r.returncode != 0:
        print(f"FAIL (exit={r.returncode})")
        return False, None, f"pytest_error(exit={r.returncode})"

    cov_path = dest / "coverage.json"
    if not cov_path.exists():
        print("FAIL (no coverage.json)")
        return False, None, "no_coverage_json"

    try:
        with open(cov_path) as f:
            data = json.load(f)
        pct = float(data["totals"]["percent_covered"])
    except Exception as e:
        print(f"FAIL (parse: {e})")
        return False, None, "coverage_parse_error"

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
    OUTPUT_DIR.mkdir(exist_ok=True)
    REPOS_TMP.mkdir(exist_ok=True)

    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader     = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows       = list(reader)

    print("=" * 60)
    print("PS8 (Python): pytest-cov チェック (line coverage >= 70%)")
    print(f"Input : {INPUT_CSV}  ({len(rows)} 件)")
    print(f"Output: {OUTPUT_CSV}")
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
    print(f"\n=== PS8 (Python) 完了 ===")
    print(f"Total: {len(rows)}  Pass: {passed}  Fail: {failed}  Skip: {skipped}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
