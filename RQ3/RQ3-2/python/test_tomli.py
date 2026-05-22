import sys, os
print("Python:", sys.executable)
print("Version:", sys.version)
print("CWD:", os.getcwd())
try:
    import tomli
    print("tomli OK:", tomli.__version__)
except ImportError as e:
    print("tomli FAIL:", e)
try:
    import toml
    print("toml OK:", toml.__version__)
except ImportError as e:
    print("toml FAIL:", e)
print("sys.path:", sys.path[:3])
