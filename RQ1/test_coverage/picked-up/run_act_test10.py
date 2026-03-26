#!/usr/bin/env python3
"""
run_act_test10.py - 最初の10件でact動作確認用
"""

import csv
import os
import shutil
import subprocess

INPUT_CSV   = "results_has_test_jobs_jobid.csv"
OUTPUT_CSV  = "results_act_test10_passed.csv"
WORK_DIR    = "repos_test10"
LOG_DIR     = "act_logs_test10"
LIMIT       = 10
ACT_TIMEOUT = 600  # 10分

SECRETS_PATTERNS = ["secret", "Input required and not supplied", "Could not find secret"]


def parse_test_jobs(s):
    seen, result = set(), []
    for entry in s.split("|"):
        if ":" not in entry:
            continue
        wf, jid = entry.strip().split(":", 1)
        if (wf, jid) not in seen:
            seen.add((wf, jid))
            result.append((wf, jid))
    return result


def is_secrets_failure(log):
    low = log.lower()
    return any(p.lower() in low for p in SECRETS_PATTERNS)


def run_act_job(repo_dir, wf_file, job_id, log_fh):
    wf_path = os.path.join(repo_dir, ".github", "workflows", wf_file)
    cmd = ["act", "-j", job_id, "-W", wf_path, "--no-cache-server", "--rm", "-q"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=ACT_TIMEOUT, cwd=repo_dir)
        combined = r.stdout + r.stderr
        log_fh.write(f"=== {job_id} | {wf_file} | exit:{r.returncode} ===\n{combined}\n")
        if r.returncode == 0:
            return "pass"
        return "skip_secrets" if is_secrets_failure(combined) else "fail"
    except subprocess.TimeoutExpired:
        log_fh.write(f"=== {job_id} | {wf_file} | TIMEOUT ===\n")
        return "timeout"


def main():
    for cmd in ["act", "docker"]:
        if not shutil.which(cmd):
            print(f"ERROR: {cmd} が見つかりません")
            return

    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    with open(INPUT_CSV, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)[:LIMIT]
        fieldnames = reader.fieldnames

    with open(OUTPUT_CSV, "w", newline="") as outf:
        writer = csv.DictWriter(outf, fieldnames=fieldnames)
        writer.writeheader()

        for i, row in enumerate(rows, 1):
            repo_name = row["name"]
            print(f"\n[{i}/{LIMIT}] {repo_name}")

            owner, repo = repo_name.split("/", 1)
            repo_dir = os.path.join(WORK_DIR, owner, repo)
            log_file = os.path.join(LOG_DIR, f"{owner}__{repo}.log")

            # Clone
            print(f"  Cloning...", end=" ", flush=True)
            r = subprocess.run(
                ["git", "clone", "--depth=1", f"https://github.com/{repo_name}.git", repo_dir],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                print(f"FAIL: {r.stderr[:100]}")
                continue
            print("OK")

            test_jobs = parse_test_jobs(row.get("test_jobs", ""))
            print(f"  Jobs: {[f'{w}:{j}' for w, j in test_jobs]}")

            repo_status = "pass"
            with open(log_file, "w") as lf:
                for wf_file, job_id in test_jobs:
                    print(f"  act -j {job_id} ({wf_file})...", end=" ", flush=True)
                    status = run_act_job(repo_dir, wf_file, job_id, lf)
                    print(status.upper())
                    if status != "pass":
                        repo_status = status
                        break

            if repo_status == "pass":
                writer.writerow(row)
                outf.flush()
                print(f"  => PASSED")
            else:
                print(f"  => {repo_status.upper()}")

            shutil.rmtree(repo_dir, ignore_errors=True)

    print(f"\n完了 -> {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
