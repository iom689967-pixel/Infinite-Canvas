"""Replay actual owner/candidate code with fake inputs and network transports only.

No live installation is imported. Source copies and runtime data stay in a fresh
workspace. Historical before JSON is immutable in tests/fixtures/stability-before.
"""
import argparse, os, pathlib, shutil, subprocess, sys, tarfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--owner-archive',type=pathlib.Path,required=True);p.add_argument('--cases',nargs='*',default=['llm','llm_protocols','workflows','stream','stream_disconnect','kie','frontend']);args=p.parse_args()
    out=args.output.resolve()
    if out.exists():raise SystemExit('Use a new output directory; before evidence is never overwritten')
    (out/'snapshots/owner').mkdir(parents=True);(out/'evidence').mkdir();(out/'runtime').mkdir()
    with tarfile.open(args.owner_archive) as archive:archive.extractall(out/'snapshots/owner',filter='data')
    target=out/'snapshots/public';target.mkdir()
    # Tracked files plus new source files; exclude all installation/runtime data.
    names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    for name in names:
        src=ROOT/name
        if src.is_file() and not src.is_symlink():
            dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
    env=dict(os.environ,STABILITY_REPLAY_ROOT=str(out),PYTHONDONTWRITEBYTECODE='1')
    for case in args.cases:
        script=ROOT/'tools/stability'/('differential_'+case+'.py')
        for side in ([''] if case=='frontend' else ['owner','public']):
            subprocess.run([sys.executable,str(script)]+([side] if side else []),env=env,check=True,timeout=240)
if __name__=='__main__':main()
