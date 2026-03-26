#!/usr/bin/env python3
"""
run_act_tests.py

results_coverage_75plus.csv の各リポジトリを:
1. git clone --depth=1
2. .github/workflows/ からtest関連のjobを検出
3. nektos/act で当該jobのみ実行
4. 全jobが成功したものだけ results_act_test_passed.csv に保存

中断後も act_progress.log を見て再開可能。
"""

import csv
import os
import sys
import glob
import shutil
import subprocess

INPUT_CSV = "results_coverage_75plus.csv"
OUTPUT_CSV = "results_act_test_passed.csv"
WORK_DIR = "repos"
LOG_DIR = "act_logs"
PROGRESS_FILE = "act_progress.log"

# テストjobと判定するキーワード（job_id / job name に含まれる場合）
TEST_KEYWORDS = ["test", "spec", "jest", "pytest", "mocha", "vitest", "karma", "jasmine", "cypress"]

CLONE_TIMEOUT = 120   # seconds
ACT_TIMEOUT = 600     # seconds per job


def check_prerequisites():
    for cmd in ["act", "docker"]:
        if shutil.which(cmd) is None:
            print(f"ERROR: '{cmd}' が見つかりません。")
            if cmd == "act":
                print("  インストール: https://github.com/nektos/act")
            elif cmd == "docker":
                print("  Dockerをインストールして起動してください。")
            sys.exit(1)
    print("act / docker: OK")


def load_progress():
    processed = {}
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if "," in line:
                    repo, status = line.split(",", 1)
                    processed[repo] = status
    return processed


def append_progress(repo_name, status):
    with open(PROGRESS_FILE, "a") as f:
        f.write(f"{repo_name},{status}\n")


def find_test_jobs(workflow_dir):
    """
    .github/workflows/ 以下のYAMLを解析してtest関連のjobを返す。
    戻り値: list of (workflow_filename, job_id)
    """
    try:
        import yaml
    except ImportError:
        print("  WARN: PyYAML がありません。pip install pyyaml")
        return []

    test_jobs = []
    for wf_path in sorted(
        glob.glob(os.path.join(workflow_dir, "*.yml")) +
        glob.glob(os.path.join(workflow_dir, "*.yaml"))
    ):
        try:
            with open(wf_path, "r", encoding="utf-8", errors="ignore") as f:
                wf = yaml.safe_load(f)
            if not wf or not isinstance(wf, dict):
                continue
            jobs = wf.get("jobs") or {}
            for job_id, job_def in jobs.items():
                if not isinstance(job_def, dict):
                    continue
                job_name = str(job_def.get("name", "")).lower()
                job_id_l = str(job_id).lower()
                if any(kw in job_id_l or kw in job_name for kw in TEST_KEYWORDS):
                    test_jobs.append((os.path.basename(wf_path), job_id))
        except Exception as e:
            print(f"  WARN: {wf_path} parse error: {e}")
    return test_jobs


def run_act_job(repo_dir, workflow_dir, wf_file, job_id, log_fh):
    """actで1つのjobを実行。(returncode, stdout+stderr) を返す。"""
    wf_path = os.path.join(workflow_dir, wf_file)
    cmd = [
        "act",
        "-j", job_id,
        "-W", wf_path,
        "--no-cache-server",
        "--rm",
        "-q",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=ACT_TIMEOUT,
            cwd=repo_dir,
        )
        log_fh.write(f"=== Job: {job_id} | exit: {result.returncode} ===\n")
        log_fh.write(result.stdout)
        log_fh.write(result.stderr)
        return result.returncode
    except subprocess.TimeoutExpired:
        log_fh.write(f"=== Job: {job_id} | TIMEOUT ===\n")
        return -1


def main():
    check_prerequisites()
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    processed = load_progress()

    # CSVの読み込み
    with open(INPUT_CSV, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    # 出力ファイルの準備
    output_exists = os.path.exists(OUTPUT_CSV) and os.path.getsize(OUTPUT_CSV) > 0
    outfile = open(OUTPUT_CSV, "a", newline="")
    writer = csv.DictWriter(outfile, fieldnames=fieldnames)
    if not output_exists:
        writer.writeheader()

    total = len(rows)
    passed = failed = skipped = 0

    print(f"\n=== 処理開始: {total} リポジトリ ===\n")

    for i, row in enumerate(rows, 1):
        repo_name = row["name"]
        print(f"[{i}/{total}] {repo_name}", flush=True)

        # 既処理ならスキップ
        if repo_name in processed:
            status = processed[repo_name]
            print(f"  -> スキップ (既処理: {status})")
            skipped += 1
            if status == "pass":
                passed += 1
            else:
                failed += 1
            continue

        owner, repo = repo_name.split("/", 1)
        repo_dir = os.path.join(WORK_DIR, owner, repo)
        log_file = os.path.join(LOG_DIR, f"{owner}__{repo}.log")

        # --- Clone ---
        if not os.path.exists(repo_dir):
            print(f"  Cloning {repo_name}...", flush=True)
            r = subprocess.run(
                ["git", "clone", "--depth=1",
                 f"https://github.com/{repo_name}.git", repo_dir],
                capture_output=True, text=True, timeout=CLONE_TIMEOUT,
            )
            if r.returncode != 0:
                print(f"  -> FAIL (clone error): {r.stderr[:200]}")
                append_progress(repo_name, "fail_clone")
                failed += 1
                continue

        # --- ワークフロー検出 ---
        workflow_dir = os.path.join(repo_dir, ".github", "workflows")
        if not os.path.exists(workflow_dir):
            print(f"  -> SKIP (no .github/workflows)")
            append_progress(repo_name, "skip_no_workflow")
            failed += 1
            shutil.rmtree(repo_dir, ignore_errors=True)
            continue

        test_jobs = find_test_jobs(workflow_dir)
        if not test_jobs:
            print(f"  -> SKIP (no test jobs found)")
            append_progress(repo_name, "skip_no_test_jobs")
            failed += 1
            shutil.rmtree(repo_dir, ignore_errors=True)
            continue

        print(f"  Test jobs: {[j[1] for j in test_jobs]}")

        # --- act 実行 ---
        all_passed = True
        with open(log_file, "w") as lf:
            for wf_file, job_id in test_jobs:
                print(f"  Running act -j {job_id} ({wf_file})...", end=" ", flush=True)
                rc = run_act_job(repo_dir, workflow_dir, wf_file, job_id, lf)
                if rc == 0:
                    print("OK")
                elif rc == -1:
                    print("TIMEOUT")
                    all_passed = False
                    break
                else:
                    print(f"FAIL (exit={rc})")
                    all_passed = False
                    break

        if all_passed:
            writer.writerow(row)
            outfile.flush()
            passed += 1
            print(f"  => PASSED -> saved to {OUTPUT_CSV}")
            append_progress(repo_name, "pass")
        else:
            failed += 1
            append_progress(repo_name, "fail")

        # ディスク節約のため削除
        shutil.rmtree(repo_dir, ignore_errors=True)

    outfile.close()

    print(f"\n=== 完了 ===")
    print(f"Total:   {total}")
    print(f"Passed:  {passed}")
    print(f"Failed:  {failed}  (clone失敗・ワークフロー不在・テストjob不在・テスト失敗 含む)")
    print(f"Skipped: {skipped} (既処理)")
    print(f"Output:  {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
