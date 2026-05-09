#!/usr/bin/env python3
"""
ps8_filter.py

Input : ps7/ps7_filtered.csv
Check : nyc がある + npm install 成功 + nyc+test が全通過
Output: ps8/ps8_filtered.csv  (通過分のみ、coverageカラム付き)
        ps8/progress.log      (再開用)
"""

import csv
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

# ============================================================
# 設定
# ============================================================
BASE_DIR   = Path(__file__).parent
INPUT_CSV  = BASE_DIR / "ps7" / "ps7_filtered.csv"
OUTPUT_DIR = BASE_DIR / "ps8"
OUTPUT_CSV = OUTPUT_DIR / "ps8_filtered.csv"
PROGRESS   = OUTPUT_DIR / "progress.log"
REPOS_TMP  = BASE_DIR / "repos_tmp"

CLONE_TIMEOUT       = 120   # sec
NPM_INSTALL_TIMEOUT = 300   # sec
NYC_TIMEOUT         = 300   # sec


# ============================================================
# nyc 検出・実行
# ============================================================
def _pkg_has_nyc(pkg: dict) -> bool:
    deps = {}
    deps.update(pkg.get("dependencies") or {})
    deps.update(pkg.get("devDependencies") or {})
    scripts_str = " ".join(str(v) for v in (pkg.get("scripts") or {}).values())
    return "nyc" in deps or "nyc" in scripts_str


def detect_nyc_cmd(pkg: dict) -> str | None:
    scripts = pkg.get("scripts") or {}

    # coverage 系スクリプトが nyc を呼んでいる
    for key in ["coverage", "test:coverage", "test-coverage", "cov"]:
        if "nyc" in scripts.get(key, ""):
            return f"npm run {key}"

    # test スクリプト自体が nyc を呼んでいる
    if "nyc" in scripts.get("test", ""):
        return "npm test"

    # devDep に nyc → npx で wrap
    deps = {}
    deps.update(pkg.get("dependencies") or {})
    deps.update(pkg.get("devDependencies") or {})
    if "nyc" in deps:
        return "npx nyc --reporter=json-summary npm test"

    return None


def _parse_coverage_summary(path: Path) -> dict | None:
    try:
        with open(path) as f:
            data = json.load(f)
        total = data.get("total", {})
        return {
            "lines":      float(total.get("lines",      {}).get("pct", 0) or 0),
            "branches":   float(total.get("branches",   {}).get("pct", 0) or 0),
            "functions":  float(total.get("functions",  {}).get("pct", 0) or 0),
            "statements": float(total.get("statements", {}).get("pct", 0) or 0),
        }
    except Exception:
        return None


def _parse_nyc_stdout(text: str) -> dict | None:
    m = re.search(
        r"All files\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)",
        text,
    )
    if m:
        return {
            "statements": float(m.group(1)),
            "branches":   float(m.group(2)),
            "functions":  float(m.group(3)),
            "lines":      float(m.group(4)),
        }
    return None


def run_ps8(dest: Path) -> tuple[bool, dict | None, str]:
    """
    PS8 実行。Returns: (passed, coverage | None, reason)
    """
    pkg_path = dest / "package.json"
    if not pkg_path.exists():
        return False, None, "no_package_json"
    try:
        with open(pkg_path, encoding="utf-8", errors="replace") as f:
            pkg = json.load(f)
    except Exception:
        return False, None, "invalid_package_json"

    if not _pkg_has_nyc(pkg):
        return False, None, "no_nyc"

    nyc_cmd = detect_nyc_cmd(pkg)
    if not nyc_cmd:
        return False, None, "no_nyc_cmd"

    # npm install
    print(f"    npm install ...", end=" ", flush=True)
    r = subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund"],
        cwd=dest, capture_output=True, text=True,
        timeout=NPM_INSTALL_TIMEOUT,
    )
    if r.returncode != 0:
        print(f"FAIL (exit={r.returncode})")
        return False, None, f"npm_install_failed(exit={r.returncode})"
    print("OK")

    # nyc 実行
    print(f"    {nyc_cmd} ...", end=" ", flush=True)
    try:
        r2 = subprocess.run(
            nyc_cmd, shell=True,
            cwd=dest, capture_output=True, text=True,
            timeout=NYC_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        return False, None, "timeout"

    if r2.returncode != 0:
        print(f"FAIL (exit={r2.returncode})")
        return False, None, f"test_failed(exit={r2.returncode})"
    print("OK (all tests passed)")

    # json-summary 生成（なければ追加実行）
    summary_path = dest / "coverage" / "coverage-summary.json"
    if not summary_path.exists():
        subprocess.run(
            ["npx", "nyc", "report", "--reporter=json-summary"],
            cwd=dest, capture_output=True, text=True, timeout=60,
        )

    cov = _parse_coverage_summary(summary_path)
    if cov is None:
        cov = _parse_nyc_stdout(r2.stdout + r2.stderr)
    if cov is None:
        return False, None, "no_coverage_data"

    return True, cov, "ok"


# ============================================================
# 進捗管理
# ============================================================
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


# ============================================================
# メイン
# ============================================================
def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    REPOS_TMP.mkdir(exist_ok=True)

    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader     = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows       = list(reader)

    print("=" * 60)
    print("PS8: nyc 実行チェック (nyc検出 + build成功 + test全通過)")
    print(f"Input : {INPUT_CSV}  ({len(rows)} 件)")
    print(f"Output: {OUTPUT_CSV}")
    print("=" * 60 + "\n")

    done = load_progress()

    out_fields = fieldnames + ["nyc_lines", "nyc_branches", "nyc_functions", "nyc_statements"]
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

        # clone
        print(f"  clone ...", end=" ", flush=True)
        try:
            r = subprocess.run(
                ["git", "clone", "--depth=1",
                 f"https://github.com/{repo}.git", str(dest)],
                capture_output=True, text=True, timeout=CLONE_TIMEOUT,
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
        except subprocess.TimeoutExpired:
            ok, cov, reason = False, None, "timeout"
        except Exception as e:
            ok, cov, reason = False, None, f"error({e})"
        finally:
            shutil.rmtree(dest, ignore_errors=True)

        if not ok:
            print(f"  => SKIP ({reason})")
            save_progress(repo, f"fail_{reason}")
            failed += 1
            continue

        if cov["lines"] < 70:
            print(f"  => SKIP (nyc_lines={cov['lines']}% < 70)")
            save_progress(repo, f"fail_low_coverage(lines={cov['lines']}%)")
            failed += 1
            continue

        row_out = dict(row)
        row_out["nyc_lines"]      = cov["lines"]
        row_out["nyc_branches"]   = cov["branches"]
        row_out["nyc_functions"]  = cov["functions"]
        row_out["nyc_statements"] = cov["statements"]
        for col in out_fields:
            row_out.setdefault(col, "")
        writer.writerow(row_out)
        outfile.flush()

        save_progress(repo, f"pass(lines={cov['lines']}%,branches={cov['branches']}%)")
        passed += 1
        print(f"  => SAVED  lines={cov['lines']}%  branches={cov['branches']}%  "
              f"funcs={cov['functions']}%  stmts={cov['statements']}%")

    outfile.close()

    print(f"\n=== PS8 完了 ===")
    print(f"Total: {len(rows)}  Pass: {passed}  Fail: {failed}  Skip: {skipped}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
