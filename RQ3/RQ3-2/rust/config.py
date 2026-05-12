# rust/config.py
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".env"))

LANGUAGE = "rust"

MAX_PROJECTS = None

GITHUB_TOKENS = [
    t for key in ["GITHUB_TOKEN_1", "GITHUB_TOKEN_2", "GITHUB_TOKEN_3", "GITHUB_TOKEN_4", "GITHUB_TOKEN_5"]
    if (t := os.environ.get(key))
]

ROOT_DIR = "data_dependency_waste_project/"
BATCH_INDEX = None

PATHS = {
    "latest_clones": os.path.join(ROOT_DIR, "latest_clones"),
    "commits": os.path.join(ROOT_DIR, "commits"),
    "one_dependency_version_change_commits": os.path.join(ROOT_DIR, "one_dependency_version_change_commits"),
    "dependency_data": os.path.join(ROOT_DIR, "dependency_data"),
    "ci_data": os.path.join(ROOT_DIR, "ci_data"),
    "ci_data_missing_files": os.path.join(ROOT_DIR, "ci_data_missing_files"),
    "filtered": os.path.join(ROOT_DIR, "filtered"),
    "datasets": os.path.join(ROOT_DIR, "datasets"),
}

PROJECT_LIST_PATH = os.environ.get("REPO_LIST_PATH", None)

DATE_SINCE = "2024-03-01"
DATE_UNTIL = "2026-03-01"

NOT_PACKAGES = [""]

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL_LLAMA = "llama3.1:8b"
OLLAMA_MODEL_QWEN = "qwen3.5:4b"
OLLAMA_MODEL_DEEPSEEK = "deepseek-coder:6.7b-instruct"

API_RETRY_MAX = 5
API_RETRY_DELAY = 1
API_TIMEOUT = 30

SKIP_REPOS = []


def set_output_dir(csv_path: str, batch_index: int = None):
    global ROOT_DIR, BATCH_INDEX
    stem = os.path.splitext(os.path.basename(csv_path))[0]
    BATCH_INDEX = batch_index

    if batch_index is not None:
        base = os.path.join("data_dependency_waste_project", stem)
        ROOT_DIR = os.path.join(base, f"batch_{batch_index}", "")
        datasets_dir = os.path.join(base, "datasets")
    else:
        ROOT_DIR = os.path.join("data_dependency_waste_project", stem, "")
        datasets_dir = os.path.join(ROOT_DIR, "datasets")

    PATHS.update({
        "latest_clones": os.path.join(ROOT_DIR, "latest_clones"),
        "commits": os.path.join(ROOT_DIR, "commits"),
        "one_dependency_version_change_commits": os.path.join(ROOT_DIR, "one_dependency_version_change_commits"),
        "dependency_data": os.path.join(ROOT_DIR, "dependency_data"),
        "ci_data": os.path.join(ROOT_DIR, "ci_data"),
        "ci_data_missing_files": os.path.join(ROOT_DIR, "ci_data_missing_files"),
        "filtered": os.path.join(ROOT_DIR, "filtered"),
        "datasets": datasets_dir,
    })


def ensure_directories():
    for path in PATHS.values():
        os.makedirs(path, exist_ok=True)


def get_filtered_project_list_path(repo_list_path: str) -> str:
    return None


def get_clone_path(owner: str, repo: str) -> str:
    return os.path.join(PATHS["latest_clones"], f"{owner}-{repo}")


def get_commits_dump_path(owner: str, repo: str) -> str:
    return os.path.join(PATHS["commits"], f"commits_{owner}_{repo}.txt")


def get_one_dep_change_commits_path(owner: str, repo: str) -> str:
    return os.path.join(PATHS["one_dependency_version_change_commits"], f"commits_{owner}_{repo}.json")


def get_dependency_data_path(owner: str, repo: str) -> str:
    return os.path.join(PATHS["dependency_data"], f"{owner}_{repo}_dependency_data.json")


def get_ci_data_path(owner: str, repo: str) -> str:
    return os.path.join(PATHS["ci_data"], f"{owner}_{repo}_ci_data.json")


def get_final_dataset_path() -> str:
    if BATCH_INDEX is not None:
        return os.path.join(PATHS["datasets"], f"final_dataset_{BATCH_INDEX}.csv")
    return os.path.join(PATHS["datasets"], "final_dataset.csv")
