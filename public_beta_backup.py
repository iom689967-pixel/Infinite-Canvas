"""Consistent SQLite plus incremental file snapshots for Public Beta backups."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3

from public_beta_store import BetaConfig, GatewayStore


SNAPSHOT = re.compile(r'^\d{8}T\d{6}Z-[0-9a-f]{6}$')


def sqlite_backup(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise RuntimeError('Unsafe SQLite source')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as current, sqlite3.connect(destination) as saved:
        current.backup(saved)
    os.chmod(destination, 0o600)


def safe_snapshots(root):
    return sorted(path for path in root.iterdir()
                  if path.is_dir() and SNAPSHOT.fullmatch(path.name) and not path.is_symlink())


def previous_snapshot(root):
    values = safe_snapshots(root)
    return values[-1] if values else None


def copy_instance(source, destination, previous=None):
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError('Unsafe instance root')
    files = bytes_written = skipped = 0
    for base, dirs, names in os.walk(source, followlinks=False):
        base = Path(base)
        relative = base.relative_to(source)
        if relative == Path('.'):
            dirs[:] = [name for name in dirs
                       if name not in {'.runtime', 'runtime'} and not (base / name).is_symlink()]
        else:
            dirs[:] = [name for name in dirs if not (base / name).is_symlink()]
        target_base = destination / relative
        target_base.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in names:
            current = base / name
            relative_file = current.relative_to(source)
            target = destination / relative_file
            if current.is_symlink() or not current.is_file():
                skipped += 1
                continue
            before = current.stat()
            if current.suffix == '.sqlite3':
                sqlite_backup(current, target)
            else:
                old = previous / relative_file if previous else None
                if old and old.is_file() and not old.is_symlink():
                    old_stat = old.stat()
                    if (old_stat.st_size, old_stat.st_mtime_ns) == (before.st_size, before.st_mtime_ns):
                        os.link(old, target)
                    else:
                        shutil.copy2(current, target)
                else:
                    shutil.copy2(current, target)
                after = current.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    target.unlink(missing_ok=True)
                    skipped += 1
                    continue
            files += 1
            bytes_written += before.st_size
    return files, bytes_written, skipped


def snapshot(config):
    if shutil.disk_usage(config.backup_root).free < config.min_free_disk:
        raise RuntimeError('Backup disk floor reached')
    if not (config.root/'gateway.sqlite3').is_file() or not (config.root/'handoff.key').is_file():
        raise RuntimeError('Gateway data is not initialized')
    store = GatewayStore(config)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(3)
    partial, final = config.backup_root / ('.partial-' + stamp), config.backup_root / stamp
    partial.mkdir(mode=0o700)
    previous = previous_snapshot(config.backup_root)
    files = bytes_written = skipped = 0
    try:
        with open(config.root / 'supervisor.lock', 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            sqlite_backup(store.path, partial / 'gateway.sqlite3')
            shutil.copy2(store.key_path, partial / 'handoff.key')
            os.chmod(partial / 'handoff.key', 0o600)
            files += 2
            bytes_written += store.path.stat().st_size + store.key_path.stat().st_size
            with store.db() as db:
                instances = [dict(row) for row in db.execute(
                    'SELECT instance_id,data_root FROM instances ORDER BY instance_id')]
            for item in instances:
                source = config.instances_root / item['instance_id']
                if str(source) != item['data_root'] or source.parent != config.instances_root:
                    raise RuntimeError('Instance registry mismatch')
                prior = previous / 'instances' / item['instance_id'] if previous else None
                count, size, omitted = copy_instance(
                    source, partial / 'instances' / item['instance_id'], prior)
                files += count
                bytes_written += size
                skipped += omitted
        manifest = {'created_at': datetime.now(timezone.utc).isoformat(),
                    'instance_count': len(instances), 'file_count': files,
                    'logical_bytes': bytes_written, 'changed_files_skipped': skipped}
        path = partial / 'manifest.json'
        path.write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
        os.chmod(path, 0o600)
        os.replace(partial, final)
        snapshots = safe_snapshots(config.backup_root)
        for old in snapshots[:-config.backup_retention]:
            shutil.rmtree(old)
        return {'snapshot': final.name, **manifest}
    except BaseException:
        if partial.is_dir() and not partial.is_symlink():
            shutil.rmtree(partial)
        raise


def main():
    try:
        result = snapshot(BetaConfig.from_env())
        keys = ('snapshot', 'instance_count', 'file_count', 'logical_bytes', 'changed_files_skipped')
        print(json.dumps({key: result[key] for key in keys}))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        print('Public Beta 备份未完成；请检查本机存储与服务配置。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
