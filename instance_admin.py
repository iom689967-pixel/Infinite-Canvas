"""Local-only account administration. Passwords never appear in argv or output."""
import argparse
import getpass
import os
import sys
import warnings

from instance_auth import AuthStore


def main():
    parser = argparse.ArgumentParser(description="本机实例账号管理；不得指向生产数据测试")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("action", choices=("init", "create", "reset-password", "disable"))
    parser.add_argument("--username")
    parser.add_argument("--password-stdin", action="store_true", help="自动化使用私有 stdin；不要通过 shell 参数或日志传密码")
    args = parser.parse_args()
    try:
        if args.action == "init":
            # Reuse Phase 1's empty-root validation and exclusive initialization, not a migration.
            os.environ.update(INSTANCE_ID=args.instance_id, INSTANCE_DATA_ROOT=args.data_root,
                              INSTANCE_HOST="127.0.0.1", INSTANCE_PORT="43101")
            import instance_paths
            AuthStore(instance_paths.INSTANCE_DATA_ROOT, args.instance_id, initialize=True)
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
        print("本机账号操作完成；未输出密码或会话数据。")
        return 0
    except (ValueError, RuntimeError, OSError, getpass.GetPassWarning) as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
