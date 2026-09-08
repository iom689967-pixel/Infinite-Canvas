"""Local-only account administration. Passwords never appear in argv or output."""
import argparse
import getpass
import os
import re
import sys
import tempfile
import warnings

from instance_auth import AuthStore


def main():
    parser = argparse.ArgumentParser(description="本机实例账号管理；不得指向生产数据测试")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("action", choices=("init", "create", "reset-password", "disable", "set-model-credential"))
    parser.add_argument("--username")
    parser.add_argument("--password-stdin", action="store_true", help="自动化使用私有 stdin；不要通过 shell 参数或日志传密码")
    parser.add_argument("--credential-name", help="本实例 credentials 下的文件名，不是密钥值")
    parser.add_argument("--credential-stdin", action="store_true", help="仅自动化：通过私有 stdin 输入模型凭证")
    args = parser.parse_args()
    try:
        if args.action == "init":
            # Reuse Phase 1's empty-root validation and exclusive initialization, not a migration.
            os.environ.update(INSTANCE_ID=args.instance_id, INSTANCE_DATA_ROOT=args.data_root,
                              INSTANCE_HOST="127.0.0.1", INSTANCE_PORT="43101")
            import instance_paths
            AuthStore(instance_paths.INSTANCE_DATA_ROOT, args.instance_id, initialize=True)
        elif args.action == "set-model-credential":
            store = AuthStore(args.data_root, args.instance_id)
            if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}', args.credential_name or ''):
                raise ValueError('无效凭证文件名')
            if args.credential_stdin:
                credential = sys.stdin.readline().rstrip('\r\n')
            else:
                if not sys.stdin.isatty():
                    raise ValueError('凭证需要可隐藏输入的终端')
                with warnings.catch_warnings():
                    warnings.simplefilter('error', getpass.GetPassWarning)
                    credential = getpass.getpass('助理专用调用凭证（隐藏输入）：')
            if not credential or len(credential) > 4096 or any(c in credential for c in '\r\n'):
                raise ValueError('无效凭证格式')
            directory = store.root / '.auth/credentials'
            directory.mkdir(mode=0o700, exist_ok=True)
            target = directory / args.credential_name
            # Atomic rotation: an in-flight request retains its current key; later calls reload.
            with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as handle:
                temporary = handle.name
                os.chmod(temporary, 0o600)
                handle.write(credential)
            os.replace(temporary, target)
        else:
            if not args.username:
                raise ValueError("必须提供 --username")
            store = AuthStore(args.data_root, args.instance_id)
            if args.action == "disable":
                store.change_account(args.username, disable=True)
            else:
                if args.password_stdin:
                    password = sys.stdin.readline().rstrip("\r\n")
                else:
                    if not sys.stdin.isatty():
                        raise ValueError("需要可隐藏输入的终端；自动化请使用私有 stdin 和 --password-stdin")
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", getpass.GetPassWarning)
                        password = getpass.getpass("新密码（隐藏输入）：")
                        if password != getpass.getpass("再次输入："):
                            raise ValueError("两次输入不一致")
                if args.action == "create":
                    store.create_account(args.username, password)
                else:
                    store.change_account(args.username, password=password)
        print("本机实例操作完成；未输出密码、凭证或会话数据。")
        return 0
    except (ValueError, RuntimeError, OSError, getpass.GetPassWarning) as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
