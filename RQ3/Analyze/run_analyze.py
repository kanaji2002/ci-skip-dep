#!/work/rintaro-k/.pyenv/versions/3.10.19/envs/py3/bin/python
import subprocess, sys
result = subprocess.run(
    [sys.executable, '/work/rintaro-k/research/RQ3/Analyze/analyze_job_python.py'],
    capture_output=True, text=True
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:500])
