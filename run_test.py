import os
from pathlib import Path
from agents.core.host_function_dispatcher import _acfs_path, _get_base_dir

print("BASE_DIR env:", os.environ.get("BASE_DIR"))
print("_get_base_dir:", _get_base_dir())
print("_acfs_path:", _acfs_path("output.txt"))
