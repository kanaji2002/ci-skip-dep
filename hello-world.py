import sys


def main(task_id):
    print(f"({task_id}) hello world!")

if __name__ == "__main__":
    try:
        if len(sys.argv) == 1:
            main(0)  # Test
        elif len(sys.argv) < 2:
            print("Usage: python hello-world.py <SLURM_ARRAY_TASK_ID>")
            sys.exit(1)
        else:
            main(int(sys.argv[1]))
    except Exception as e:
        print(f"Fatal Error: {e}")
        sys.exit(1)