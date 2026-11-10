"""Application-level content quota: cached accounting, reservations, bounded ingress.

Private auth/runtime files are operational overhead, not workspace content. Recount is
explicit/startup only; request accounting stats just paths written or deleted by that request.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import shutil
import sqlite3
import tempfile
from threading import RLock
from fastapi import HTTPException

FULL='当前工作区存储空间已满。'
SERVER_FULL='服务器存储资源不足，暂时停止新的内容写入。'


class StorageQuota:
    def __init__(self, root, limit, max_upload, min_free_disk=0):
        self.root=Path(root).resolve();self.limit=limit;self.max_upload=max_upload
        self.min_free_disk=min_free_disk
        self.database=self.root/'.auth/storage.sqlite3'
        self.lock=RLock();self.reserved=0;self.dirty=set();self.moves=[]
        with self.db() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER); CREATE TABLE IF NOT EXISTS total (id INTEGER PRIMARY KEY, used INTEGER);')
            empty=db.execute('SELECT 1 FROM total').fetchone() is None
        os.chmod(self.database,0o600)
        if empty: self.recount()

    @classmethod
    def for_paths(cls,paths):
        if not getattr(paths,'public_beta',False): return None
        config=json.loads((paths.data_root/'.auth/public-beta.json').read_text())
        return cls(paths.data_root,config['storage_quota'],config['max_upload'],config.get('min_free_disk_bytes',0))

    @contextmanager
    def db(self):
        if self.database.is_symlink(): raise RuntimeError('Storage ledger cannot be linked')
        db=sqlite3.connect(self.database,timeout=10)
        try:
            with db: yield db
        finally: db.close()

    def tracked(self,path):
        if isinstance(path,int) or path is None: return None
        path=Path(os.fsdecode(path))
        if not path.is_absolute(): path=self.root/path
        # Audit callbacks cannot call resolve/stat recursively.
        path=Path(os.path.realpath(path))
        if not path.is_relative_to(self.root): return None
        rel=path.relative_to(self.root).as_posix()
        if rel.split('/')[0] in {'.auth','.runtime','runtime','API'} or rel.startswith('.instance'): return None
        return rel

    def changed(self,path):
        rel=self.tracked(path)
        if rel:
            with self.lock: self.dirty.add(rel)

    def moved(self, source, destination):
        source,destination=self.tracked(source),self.tracked(destination)
        if source and destination:
            with self.lock: self.moves.append((source,destination))

    def sync(self):
        with self.lock:
            dirty,self.dirty=self.dirty,set()
            moves,self.moves=self.moves,[]
            if not dirty and not moves: return
            with self.db() as db:
                for source,destination in moves:
                    if not (self.root/source).exists() and (self.root/destination).is_dir():
                        rows=db.execute('SELECT path,size FROM files WHERE substr(path,1,?)=?',(len(source)+1,source+'/')).fetchall()
                        for name,size in rows:
                            target=destination+name[len(source):]
                            old=db.execute('SELECT size FROM files WHERE path=?',(target,)).fetchone()
                            if old: db.execute('UPDATE total SET used=used-?',(old[0],))
                            db.execute('DELETE FROM files WHERE path=?',(name,))
                            db.execute('INSERT OR REPLACE INTO files VALUES (?,?)',(target,size))
                for rel in dirty:
                    path=self.root/rel
                    # Deleting a directory removes its known entries without rescanning the tree.
                    if not path.exists():
                        rows=db.execute('SELECT path,size FROM files WHERE path=? OR substr(path,1,?)=?',(rel,len(rel)+1,rel+'/')).fetchall()
                        for name,size in rows:
                            db.execute('DELETE FROM files WHERE path=?',(name,));db.execute('UPDATE total SET used=used-?',(size,))
                    elif path.is_file() and not path.is_symlink():
                        size=path.stat().st_size
                        old=db.execute('SELECT size FROM files WHERE path=?',(rel,)).fetchone()
                        db.execute('INSERT INTO files VALUES (?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size',(rel,size))
                        db.execute('UPDATE total SET used=used+?',(size-(old[0] if old else 0),))

    def recount(self):
        with self.lock:
            files=[]
            for base,dirs,names in os.walk(self.root,followlinks=False):
                dirs[:]=[d for d in dirs if self.tracked(Path(base)/d) and not (Path(base)/d).is_symlink()]
                for name in names:
                    p=Path(base)/name;rel=self.tracked(p)
                    if rel and p.is_file() and not p.is_symlink(): files.append((rel,p.stat().st_size))
            with self.db() as db:
                db.execute('DELETE FROM files');db.executemany('INSERT INTO files VALUES (?,?)',files)
                db.execute('INSERT OR REPLACE INTO total VALUES (1,?)',(sum(v for _,v in files),))
            self.dirty.clear()
            return self.usage()

    def usage(self):
        self.sync()
        with self.db() as db: used=db.execute('SELECT used FROM total WHERE id=1').fetchone()[0]
        return {'used_bytes':used,'quota_bytes':self.limit,'max_upload_bytes':self.max_upload,
                'server_write_available':shutil.disk_usage(self.root).free >= self.min_free_disk}

    @contextmanager
    def reserve(self,size):
        with self.lock:
            used=self.usage()['used_bytes']
            if size<0 or used+self.reserved+size>self.limit: raise HTTPException(413,FULL)
            if size and shutil.disk_usage(self.root).free-size < self.min_free_disk:
                raise HTTPException(503,SERVER_FULL)
            self.reserved+=size
        try: yield
        finally:
            with self.lock:
                self.sync();self.reserved-=size

    def write(self,path,content):
        if len(content)>self.max_upload: raise HTTPException(413,'单文件超过上传大小限制')
        with self.reserve(len(content)):
            # All new media names are server-selected. Roll back a failed partial new file.
            created=False
            try:
                with open(path,'xb') as f:
                    created=True;f.write(content)
            except BaseException:
                if created and os.path.isfile(path): os.unlink(path)
                raise
            finally: self.changed(path)

    async def read_upload(self,file,existing_limit=None):
        limit=min(self.max_upload,existing_limit or self.max_upload)
        value=bytearray()
        while True:
            chunk=await file.read(min(65536,limit+1-len(value)))
            if not chunk: return bytes(value)
            value.extend(chunk)
            if len(value)>limit: raise HTTPException(413,'单文件超过上传大小限制')

    def replace_json(self, path, value):
        content=json.dumps(value,ensure_ascii=False,indent=2).encode()
        if len(content)>self.max_upload: raise HTTPException(413,'文件超过存储大小限制')
        old=os.path.getsize(path) if os.path.isfile(path) else 0
        temporary=None
        with self.reserve(max(0,len(content)-old)):
            try:
                with tempfile.NamedTemporaryFile(dir=self.root/'.runtime/tmp',delete=False) as f:
                    temporary=f.name;f.write(content);f.flush();os.fsync(f.fileno())
                os.replace(temporary,path)
            finally:
                if temporary and os.path.exists(temporary): os.unlink(temporary)
                self.changed(path)
