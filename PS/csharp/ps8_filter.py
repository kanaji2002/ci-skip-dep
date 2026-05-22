#!/usr/bin/env python3
"""
ps8_filter.py  (C#)

Input : ps7/ps7_filtered.csv  (PS7 で xUnit 参照が確認済みのリポジトリ)
Check : (1) GitHub API で .csproj の TargetFramework を確認
            - net8.x を含まない → SKIP
            - net9.0+ を含む    → SKIP
        (2) ローカルでテストを実行して line coverage >= 70% を確認
            - git clone --depth=1
            - テスト .csproj を検出 → coverlet.collector を未参照なら動的に追加
            - singularity exec dotnet-sdk8.sif dotnet restore
            - singularity exec dotnet-sdk8.sif dotnet test --collect:"XPlat Code Coverage"
            - TestResults/**/coverage.cobertura.xml を解析
Output: ps8/ps8_filtered.csv  (通過分のみ、cov_lines カラム付き)
        ps8/progress.log      (再開用)
"""

import argparse
import base64
import csv
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from dotenv import load_dotenv

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

# TargetFramework(s) タグを抽出
TFM_TAG_RE = re.compile(
    r'<TargetFrameworks?>\s*([^<]+?)\s*</TargetFrameworks?>',
    re.IGNORECASE,
)
NET8_RE     = re.compile(r'\bnet8[\.\-]', re.IGNORECASE)   # net8.0, net8.0-windows 等
HIGH_TFM_RE = re.compile(r'\bnet(?:9|1[0-9])[\.\-]', re.IGNORECASE)  # net9+

# GitHub API
load_dotenv(BASE_DIR.parent.parent / ".env")
TOKENS = [
    t for key in ["GITHUB_TOKEN_1", "GITHUB_TOKEN_2", "GITHUB_TOKEN_3",
                  "GITHUB_TOKEN_4", "GITHUB_TOKEN_5"]
    if (t := os.environ.get(key))
]
_token_idx = 0
API_TIMEOUT = 20
API_RETRY   = 3


# ── GitHub API helpers ──────────────────────────────────────────────────────

def _next_token() -> str:
    global _token_idx
    t = TOKENS[_token_idx % len(TOKENS)]
    _token_idx += 1
    return t


def _gh_get(url: str) -> requests.Response | None:
    for attempt in range(API_RETRY):
        token = _next_token()
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
                timeout=API_TIMEOUT,
            )
            if resp.status_code == 403:
                reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait  = max(1, reset - int(time.time()) + 2)
                print(f"  rate limit, sleep {wait}s ...", flush=True)
                time.sleep(wait)
                continue
            return resp
        except Exception as e:
            print(f"  network error: {e}", flush=True)
            time.sleep(2 ** attempt)
    return None


def _csproj_paths(owner: str, repo: str, branch: str) -> list[str]:
    """Git Tree API でリポジトリ内の .csproj パス一覧を返す"""
    r = _gh_get(f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}")
    if r is None or r.status_code != 200:
        return []
    try:
        tree_sha = r.json()["commit"]["tree"]["sha"]
    except (KeyError, TypeError):
        return []
    r = _gh_get(
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1"
    )
    if r is None or r.status_code != 200:
        return []
    try:
        return [item["path"] for item in r.json().get("tree", [])
                if item.get("path", "").endswith(".csproj")]
    except Exception:
        return []


def _fetch_csproj(owner: str, repo: str, path: str) -> str | None:
    r = _gh_get(f"https://api.github.com/repos/{owner}/{repo}/contents/{path}")
    if r is None or r.status_code != 200:
        return None
    try:
        data = r.json()
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return data.get("content", "")
    except Exception:
        return None


def _tfms_in_content(content: str) -> list[str]:
    """csproj から TargetFramework 値のリストを返す"""
    result = []
    for m in TFM_TAG_RE.finditer(content):
        for tfm in re.split(r'[;,\s]+', m.group(1)):
            tfm = tfm.strip()
            if tfm:
                result.append(tfm)
    return result


def check_tfm_sdk8(owner: str, repo: str, branch: str) -> tuple[bool, str]:
    """
    GitHub API で .csproj の TargetFramework を確認。
    戻り値: (ok, reason)
      ok=True  → net8.x を含み net9+ を含まない（SDK 8 対象）
      ok=False → reason は "no_net8" / "has_net9plus" / "api_error"
      API 取得失敗時は ok=True で通過扱い（クローン後に判明）
    """
    if not TOKENS:
        return True, "no_token"

    paths = _csproj_paths(owner, repo, branch)
    if not paths:
        return True, "api_error"  # 取得失敗 → 通過

    has_net8    = False
    has_net9plus = False

    for path in paths:
        content = _fetch_csproj(owner, repo, path)
        if not content:
            continue
        for tfm in _tfms_in_content(content):
            if NET8_RE.search(tfm):
                has_net8 = True
            if HIGH_TFM_RE.search(tfm):
                has_net9plus = True

    if has_net9plus:
        return False, "has_net9plus"
    if not has_net8:
        return False, "no_net8"
    return True, "ok"


# ── dotnet / singularity helpers ───────────────────────────────────────────

def _run(cmd, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def singularity_exec(cmd: list, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    full_cmd = [
        SINGULARITY, "exec",
        "--bind", "/work/rintaro-k:/work/rintaro-k",
        "--pwd", str(cwd),
        "--env", "MSBUILDDISABLENODEREUSE=1",
        str(SIF_PATH),
    ] + cmd
    return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)


def find_test_csproj(dest: Path) -> list[Path]:
    result = []
    for csproj in dest.rglob("*.csproj"):
        try:
            if TEST_PKG_RE.search(csproj.read_text(errors="replace")):
                result.append(csproj)
        except Exception:
            pass
    return result


def find_solution_file(dest: Path) -> Path | None:
    slns = list(dest.rglob("*.sln"))
    if not slns:
        return None
    return min(slns, key=lambda p: len(p.relative_to(dest).parts))


def ensure_coverlet(csproj: Path, dest: Path):
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
    try:
        root = ET.parse(xml_path).getroot()
        covered = int(root.attrib.get("lines-covered", 0))
        valid   = int(root.attrib.get("lines-valid",   0))
        return covered, valid
    except Exception:
        return 0, 0


def run_ps8(dest: Path) -> tuple[bool, dict | None, str]:
    test_projs = find_test_csproj(dest)
    if not test_projs:
        return False, None, "no_test_project"

    for csproj in test_projs:
        ensure_coverlet(csproj, dest)

    sln            = find_solution_file(dest)
    restore_target = [str(sln)] if sln else []

    print("    dotnet restore ...", end=" ", flush=True)
    try:
        r = singularity_exec(
            ["dotnet", "restore", "--nologo", "-v", "q",
             "--ignore-failed-sources", "-p:NuGetAudit=false",
             "-maxcpucount:1"] + restore_target,
            cwd=dest, timeout=RESTORE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        return False, None, "restore_timeout"
    if r.returncode != 0:
        print(f"FAIL (exit={r.returncode})")
        err_snippet = (r.stderr or r.stdout or "")[:300].replace("\n", " ")
        print(f"      {err_snippet}", flush=True)
        return False, None, f"restore_failed(exit={r.returncode})"
    print("OK")

    results_dir = dest / "TestResults"
    test_target = [str(sln)] if sln else []
    print("    dotnet test --collect ...", end=" ", flush=True)
    try:
        r = singularity_exec(
            ["dotnet", "test"] + test_target + [
             "--no-restore", "--nologo", "-v", "q",
             "-maxcpucount:1",
             "--collect", "XPlat Code Coverage",
             "--results-directory", str(results_dir)],
            cwd=dest, timeout=TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        return False, None, "test_timeout"
    if r.returncode != 0:
        print(f"FAIL (exit={r.returncode})")
        err_snippet = (r.stderr or r.stdout or "")[:300].replace("\n", " ")
        print(f"      {err_snippet}", flush=True)
        return False, None, f"test_failed(exit={r.returncode})"

    xml_files = list(results_dir.rglob("coverage.cobertura.xml"))
    if not xml_files:
        print("FAIL (no coverage xml)")
        return False, None, "no_coverage_xml"

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


# ── progress helpers ───────────────────────────────────────────────────────

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


# ── main ───────────────────────────────────────────────────────────────────

def main():
    global INPUT_CSV, OUTPUT_CSV, PROGRESS, OUTPUT_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",    default=str(INPUT_CSV))
    parser.add_argument("--output",   default=str(OUTPUT_CSV))
    parser.add_argument("--progress", default=str(PROGRESS))
    args = parser.parse_args()
    INPUT_CSV  = Path(args.input)
    OUTPUT_CSV = Path(args.output)
    PROGRESS   = Path(args.progress)
    OUTPUT_DIR = OUTPUT_CSV.parent

    if not SIF_PATH.exists():
        print(f"ERROR: コンテナが見つかりません: {SIF_PATH}")
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
    print(f"Tokens: {len(TOKENS)} 本  (net8 pre-check {'有効' if TOKENS else '無効'})")
    print("=" * 60 + "\n")

    done = load_progress()

    out_fields = fieldnames + ["cov_lines"]
    out_exists = OUTPUT_CSV.exists() and OUTPUT_CSV.stat().st_size > 0
    outfile    = open(OUTPUT_CSV, "a", newline="", encoding="utf-8")
    writer     = csv.DictWriter(outfile, fieldnames=out_fields)
    if not out_exists:
        writer.writeheader()

    passed = failed = skipped = skipped_tfm = 0

    for i, row in enumerate(rows, 1):
        repo = row["name"]
        print(f"[{i}/{len(rows)}] {repo}", flush=True)

        if repo in done:
            status = done[repo]
            print(f"  skip ({status})")
            skipped += 1
            if status.startswith("pass"):
                passed += 1
            continue

        owner, repo_name = repo.split("/", 1)
        branch = row.get("default_branch", "main")

        # ── net8 チェック（GitHub API、クローン前） ──
        ok_tfm, tfm_reason = check_tfm_sdk8(owner, repo_name, branch)
        if not ok_tfm:
            print(f"  => SKIP TFM ({tfm_reason})")
            save_progress(repo, f"skip_tfm_{tfm_reason}")
            skipped_tfm += 1
            continue

        # ── clone ──
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

        # ── dotnet test + coverage ──
        try:
            ok, cov, reason = run_ps8(dest)
        except Exception as e:
            ok, cov, reason = False, None, f"error({e})"
        finally:
            shutil.rmtree(dest, ignore_errors=True)

        if not ok:
            print(f"  => FAIL ({reason})")
            save_progress(repo, f"fail_{reason}")
            failed += 1
            continue

        if cov["lines"] < LINE_COV_MIN:
            print(f"  => FAIL (lines={cov['lines']:.1f}% < {LINE_COV_MIN}%)")
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
    print(f"Total: {len(rows)}  Pass: {passed}  SkipTFM: {skipped_tfm}"
          f"  Fail: {failed}  Skipped(resume): {skipped}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
