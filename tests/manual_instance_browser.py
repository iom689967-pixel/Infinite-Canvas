"""Temporary real-browser fixture. No passwords in argv, files, output, or logs.

stdin commands: copy-password (temporary clipboard), finish (restore clipboard/cleanup).
Never use against production. This imports fixture mechanics, not test auth bypasses.
"""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_instance_isolation import TwoProcessIsolationTests


def main():
    controlled = '--controlled-models' in sys.argv
    if controlled:
        from test_instance_models import ControlledModelTests
        fixture = ControlledModelTests()
    else:
        fixture = TwoProcessIsolationTests
    clipboard = None
    try:
        if controlled:
            fixture.setUp()
        else:
            fixture.setUpClass()
        picture = fixture.root / "browser-upload.png"
        from PIL import Image
        Image.new("RGB", (64, 48), "#76a9d0").save(picture)
        print(json.dumps({"A":f"http://127.0.0.1:{fixture.ports['A']}",
                          "B":f"http://127.0.0.1:{fixture.ports['B']}",
                          "root":str(fixture.root), "upload":str(picture)}, ensure_ascii=False), flush=True)
        for command in sys.stdin:
            if command.strip() == "copy-password":
                if clipboard is None:
                    clipboard = subprocess.check_output(["pbpaste"])
                subprocess.run(["pbcopy"], input=fixture.passwords["A"].encode(), check=True)
                print("Temporary password copied; no credential output.", flush=True)
            elif command.strip() == "finish":
                break
            elif command.strip() == 'evidence' and controlled:
                calls = fixture.mock.calls
                print(json.dumps({'llm':sum(k=='llm' for k,_,_ in calls),
                                  'reference_uploads':sum(k=='upload' for k,_,_ in calls),
                                  'image_submissions':sum(k=='create' for k,_,_ in calls),
                                  'result_downloads':sum(k=='media' for k,_,_ in calls),
                                  'canvases':len(list((fixture.roots['A']/'data/canvases').glob('*.json')))}),flush=True)
    finally:
        if clipboard is not None:
            subprocess.run(["pbcopy"], input=clipboard, check=True)
        if controlled:
            fixture.doCleanups()
        else:
            fixture.tearDownClass()
        print("Temporary processes/data cleaned; original clipboard restored.", flush=True)


if __name__ == "__main__":
    main()
