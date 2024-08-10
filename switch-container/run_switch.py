from pathlib import Path
import subprocess, os

def run_switch_model(switch_path: str) -> None:
    os.chdir(switch_path)
    print(os.getcwd())
    subprocess.run(["switch","solve"])
    return None
