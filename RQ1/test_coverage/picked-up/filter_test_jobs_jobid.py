#!/usr/bin/env python3
"""
filter_test_jobs.py

results_coverage_75plus.csv の各リポジトリの .github/workflows/*.yml を確認し、
job_id または job name に test/Test/tests/Tests を含むjobがあるものだけを
results_has_test_jobs.csv に保存する。

git sparse-checkout を使ってワークフローファイルのみ取得（高速・軽量）。
"""

import csv
import os
import sys
import glob
import shutil
import subprocess
import yaml

INPUT_CSV  = "results_coverage_75plus.csv"
OUTPUT_CSV = "results_has_test_jobs_jobid.csv"
WORK_DIR   = "repos_sparse_jobid"
PROGRESS   = "filter_progress_jobid.log"

TEST_KEYWORDS = {"test", "tests", "Test", "Tests"}

CLONE_TIMEOUT = 60  # seconds


def load_progress():
    done = {}
    if os.path.exists(PROGRESS):
        with open(PROGRESS) as f:
            for line in f:
                line = line.strip()
                if "," in line:
                    repo, status = line.split(",", 1)
                    done[repo] = status
    return done


def save_progress(repo_name, status):
    with open(PROGRESS, "a") as f:
        f.write(f"{repo_name},{status}\n")


def sparse_clone(repo_name, dest):
    """
    .github/workflows/ だけを取得する sparse clone。
    """
    url = f"https://github.com/{repo_name}.git"
    # 既存ディレクトリは削除
    shutil.rmtree(dest, ignore_errors=True)

    r = subprocess.run(
        ["git", "clone", "--depth=1", "--filter=blob:none", "--sparse", url, dest],
        capture_output=True, text=True, timeout=CLONE_TIMEOUT,
    )
    if r.returncode != 0:
        return False, r.stderr[:300]

    r2 = subprocess.run(
        ["git", "sparse-checkout", "set", ".github/workflows"],
        capture_output=True, text=True, cwd=dest, timeout=30,
    )
    return r2.returncode == 0, r2.stderr[:300]


def has_test_job(workflow_dir):
    """
    .github/workflows/ 以下のYAMLにtest関連jobがあれば True を返す。
    """
    found_jobs = []
    for wf_path in sorted(
        glob.glob(os.path.join(workflow_dir, "*.yml")) +
        glob.glob(os.path.join(workflow_dir, "*.yaml"))
    ):
        try:
            with open(wf_path, encoding="utf-8", errors="ignore") as f:
                wf = yaml.safe_load(f)
            if not wf or not isinstance(wf, dict):
                continue
            jobs = wf.get("jobs") or {}
            for job_id, job_def in jobs.items():
                job_name = ""
                if isinstance(job_def, dict):
                    job_name = str(job_def.get("name", ""))
                # job_id のみでキーワードと一致するか含むか
                for kw in TEST_KEYWORDS:
                    if kw in str(job_id):
                        found_jobs.append((os.path.basename(wf_path), job_id, job_name))
        except Exception as e:
            pass
    return found_jobs


def main():
    os.makedirs(WORK_DIR, exist_ok=True)
    done = load_progress()

    with open(INPUT_CSV, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    # 出力ファイル準備
    out_exists = os.path.exists(OUTPUT_CSV) and os.path.getsize(OUTPUT_CSV) > 0
    outfile = open(OUTPUT_CSV, "a", newline="")
    writer = csv.DictWriter(outfile, fieldnames=fieldnames + ["test_jobs"])
    if not out_exists:
        writer.writeheader()

    total = len(rows)
    matched = skipped = failed_clone = no_wf = no_test = 0

    print(f"=== {total} リポジトリを処理します ===\n")

    for i, row in enumerate(rows, 1):
        repo_name = row["name"]
        print(f"[{i}/{total}] {repo_name}", end=" ... ", flush=True)

        if repo_name in done:
            st = done[repo_name]
            print(f"スキップ ({st})")
            skipped += 1
            if st == "match":
                matched += 1
            continue

        owner, repo = repo_name.split("/", 1)
        dest = os.path.join(WORK_DIR, owner, repo)

        # sparse clone
        ok, err = sparse_clone(repo_name, dest)
        if not ok:
            print(f"FAIL clone: {err[:100]}")
            save_progress(repo_name, "fail_clone")
            failed_clone += 1
            shutil.rmtree(dest, ignore_errors=True)
            continue

        wf_dir = os.path.join(dest, ".github", "workflows")
        if not os.path.exists(wf_dir):
            print("no workflows")
            save_progress(repo_name, "no_workflow")
            no_wf += 1
            shutil.rmtree(dest, ignore_errors=True)
            continue

        jobs = has_test_job(wf_dir)
        if jobs:
            job_str = "|".join(f"{wf}:{jid}" for wf, jid, _ in jobs)
            row_out = dict(row)
            row_out["test_jobs"] = job_str
            writer.writerow(row_out)
            outfile.flush()
            matched += 1
            print(f"MATCH ({len(jobs)} jobs: {[j[1] for j in jobs]})")
            save_progress(repo_name, "match")
        else:
            print("no test jobs")
            save_progress(repo_name, "no_test_jobs")
            no_test += 1

        shutil.rmtree(dest, ignore_errors=True)

    outfile.close()

    print(f"\n=== 完了 ===")
    print(f"Total:         {total}")
    print(f"Match:         {matched}  -> {OUTPUT_CSV}")
    print(f"No workflow:   {no_wf}")
    print(f"No test jobs:  {no_test}")
    print(f"Clone fail:    {failed_clone}")
    print(f"Skipped:       {skipped} (再開)")


if __name__ == "__main__":
    main()
