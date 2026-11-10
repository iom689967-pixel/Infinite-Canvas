#!/usr/bin/env python3
"""审计后端修复的复现/验证脚本（A7、A8、A1、C5、C6）。

只对隔离测试实例（tests/repro/run_test_instance.sh 启动）运行：脚本会新建测试画布、
写 provider 配置、发跨域请求。每项输出 PASS（已修复）/ FAIL（问题仍在）。

    python3 tests/repro/backend_checks.py --base http://127.0.0.1:3100 \
        --test-dir "$TMPDIR/ic-audit-test" [--only A7,C6] [--json out.json]
"""
import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse

DUMMY_MS_KEY = "ms-audit-dummy-key-0000-1234abcd"  # 与 run_test_instance.sh 保持一致
UNKNOWN_PROVIDER = "audit-deleted-provider"


class Client:
    def __init__(self, base):
        parsed = urllib.parse.urlparse(base)
        self.base = base.rstrip("/")
        self.host = parsed.hostname
        self.port = parsed.port or 80

    def request(self, method, path, body=None, headers=None, timeout=120):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        if "Host" in hdrs:
            conn.putrequest(method, path, skip_host=True)
            for k, v in hdrs.items():
                conn.putheader(k, v)
            if data is not None:
                conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data)
        else:
            conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            payload = None
        return res.status, {k.lower(): v for k, v in res.getheaders()}, payload, raw


def detail_text(payload):
    detail = (payload or {}).get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail or "")


class Checks:
    def __init__(self, args):
        self.c = Client(args.base)
        self.args = args
        self.results = []
        self.created = []

    def record(self, group, name, passed, info):
        self.results.append({"group": group, "check": name, "pass": bool(passed), "info": info})
        print(f"[{'PASS' if passed else 'FAIL'}] {group} {name}: {info}", flush=True)

    # ---------- helpers ----------
    def new_canvas(self, kind="smart", nodes=None, logs=None, settings=None):
        st, _, data, _ = self.c.request("POST", "/api/canvases", {"title": f"AUDIT-CHECK-{kind}", "icon": "sparkles", "kind": kind})
        assert st == 200, (st, data)
        cid = data["canvas"]["id"]
        self.created.append(cid)
        cur = self.get_canvas(cid)
        st, _, data, _ = self.c.request("PUT", f"/api/canvases/{cid}", {
            "title": cur["title"], "icon": cur["icon"], "nodes": nodes or [], "connections": [],
            "viewport": {"x": 0, "y": 0, "scale": 1}, "logs": logs or [], "settings": settings or {},
            "base_updated_at": cur["updated_at"], "client_id": "audit-check",
        })
        assert st == 200, (st, data)
        return cid

    def get_canvas(self, cid):
        st, _, data, _ = self.c.request("GET", f"/api/canvases/{cid}")
        assert st == 200, (st, data)
        return data["canvas"]

    def canvas_file(self, cid):
        return os.path.join(self.args.test_dir, "data", "canvases", f"{cid}.json")

    def run_unit(self, code, env=None):
        full_env = dict(os.environ)
        full_env.update(env or {})
        proc = subprocess.run(
            [self.args.python, "-c", "import sys; sys.path.insert(0, '.'); " + code],
            cwd=self.args.test_dir, capture_output=True, text=True, timeout=120, env=full_env,
        )
        out = proc.stdout.strip().splitlines()
        return proc.returncode, (out[-1] if out else ""), proc.stderr[-400:]

    def main_source(self):
        with open(os.path.join(self.args.test_dir, "main.py"), encoding="utf-8") as f:
            return f.read()

    # ---------- A7 ----------
    def check_A7(self):
        g = "A7"
        own_origin = self.c.base
        st, h, _, _ = self.c.request("OPTIONS", "/api/providers", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PUT"})
        acao = h.get("access-control-allow-origin")
        self.record(g, "cors_preflight_foreign_origin_denied", acao not in ("*", "https://evil.example"), f"status={st} allow-origin={acao!r}")

        for origin in ("http://localhost:5173", "chrome-extension://abcdefghijklmnop"):
            st, h, _, _ = self.c.request("OPTIONS", "/api/providers", headers={"Origin": origin, "Access-Control-Request-Method": "PUT"})
            acao = h.get("access-control-allow-origin")
            self.record(g, f"cors_preflight_allowed[{origin.split(':')[0]}]", acao in (origin, "*"), f"status={st} allow-origin={acao!r}")

        st, _, providers, _ = self.c.request("GET", "/api/providers")
        body = [{k: v for k, v in p.items() if k in (
            "id", "name", "base_url", "protocol", "image_request_mode", "enabled", "primary", "image_models",
            "chat_models", "video_models", "model_names", "model_protocols", "ms_loras", "ms_defaults_version",
            "rh_apps", "rh_workflows", "volcengine_project_name", "volcengine_region")} for p in (providers or {}).get("providers", [])]
        st, _, data, _ = self.c.request("PUT", "/api/providers", body, headers={"Origin": "https://evil.example"})
        self.record(g, "foreign_origin_put_providers_blocked", st == 403, f"status={st} {detail_text(data)[:80]}")

        st, _, data, _ = self.c.request("POST", "/api/providers/fetch-models",
                                        {"provider_id": "custom-api", "base_url": "http://127.0.0.1:9", "protocol": "openai"},
                                        headers={"Origin": "https://evil.example"})
        self.record(g, "foreign_origin_fetch_models_blocked", st == 403, f"status={st} {detail_text(data)[:80]}")

        st, _, data, _ = self.c.request("POST", "/api/canvases", {"title": "AUDIT-x"}, headers={"Origin": "null"})
        self.record(g, "null_origin_write_blocked", st == 403, f"status={st}")
        if st == 200 and isinstance(data, dict):
            self.created.append(data["canvas"]["id"])

        rebinding_host = f"evil.example:{self.c.port}"
        st, _, data, _ = self.c.request("GET", "/api/providers", headers={"Host": rebinding_host})
        self.record(g, "dns_rebinding_host_get_providers_blocked", st == 403, f"status={st}")
        st, _, data, _ = self.c.request("GET", "/api/config/token", headers={"Host": rebinding_host})
        self.record(g, "dns_rebinding_host_get_token_blocked", st == 403, f"status={st}")
        st, _, data, _ = self.c.request("POST", "/api/canvases", {"title": "AUDIT-x"},
                                        headers={"Host": rebinding_host, "Origin": f"http://{rebinding_host}"})
        self.record(g, "dns_rebinding_origin_write_blocked", st == 403, f"status={st}")
        if st == 200 and isinstance(data, dict):
            self.created.append(data["canvas"]["id"])

        st, _, data, _ = self.c.request("POST", "/api/canvases", {"title": "AUDIT-same-origin"}, headers={"Origin": own_origin})
        self.record(g, "same_origin_write_allowed", st == 200, f"status={st}")
        if st == 200:
            self.created.append(data["canvas"]["id"])
        st, _, data, _ = self.c.request("POST", "/api/canvases", {"title": "AUDIT-localhost"}, headers={"Origin": f"http://localhost:{self.c.port}", "Host": f"localhost:{self.c.port}"})
        self.record(g, "localhost_origin_write_allowed", st == 200, f"status={st}")
        if st == 200:
            self.created.append(data["canvas"]["id"])
        st, _, data, _ = self.c.request("POST", "/api/canvases", {"title": "AUDIT-no-origin"})
        self.record(g, "non_browser_client_without_origin_allowed", st == 200, f"status={st}")
        if st == 200:
            self.created.append(data["canvas"]["id"])
        st, _, data, _ = self.c.request("GET", "/api/providers", headers={"Origin": own_origin})
        self.record(g, "same_origin_get_providers_allowed", st == 200, f"status={st}")

        st, _, data, _ = self.c.request("GET", "/api/config/token")
        token = (data or {}).get("token", "")
        self.record(g, "config_token_masked", st == 200 and token != DUMMY_MS_KEY and DUMMY_MS_KEY not in json.dumps(data),
                    f"status={st} token_len={len(token)} has_token={(data or {}).get('has_token')!r}")

        rc, out, err = self.run_unit(
            "import main; masked = main.mask_secret(main.modelscope_api_key()); "
            "print(main.modelscope_api_key(masked) == main.modelscope_api_key() and main.modelscope_api_key('real-explicit') == 'real-explicit')"
        )
        self.record(g, "masked_token_sent_back_is_ignored", rc == 0 and out == "True", f"rc={rc} out={out!r} {err[-120:] if rc else ''}")

    # ---------- A8 ----------
    def check_A8(self):
        g = "A8"
        src = self.main_source()
        if "INFINITE_CANVAS_PORT" not in src or "resolve_bind_host" not in src:
            self.record(g, "default_bind_loopback", False, "main.py 仍写死 host=0.0.0.0 / port=3000（未运行，避免与正式服务端口冲突）")
            self.record(g, "lan_requires_explicit_env", False, "未实现")
        else:
            port = "3199"
            proc = subprocess.Popen([self.args.python, "main.py"], cwd=self.args.test_dir,
                                    env={**os.environ, "INFINITE_CANVAS_PORT": port, "INFINITE_CANVAS_ALLOW_LAN": "", "INFINITE_CANVAS_HOST": ""},
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            listen = ""
            try:
                for _ in range(100):
                    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
                    m = re.search(r"TCP (\S+):" + port, out)
                    if m:
                        listen = m.group(1)
                        break
                    time.sleep(0.2)
            finally:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            self.record(g, "default_bind_loopback", listen == "127.0.0.1", f"python main.py listened on {listen or '(nothing)'}:{port}")
            cases = {"": "127.0.0.1", "1": "0.0.0.0", "true": "0.0.0.0", "0": "127.0.0.1"}
            ok = True
            seen = {}
            for value, expected in cases.items():
                rc, out, err = self.run_unit("import main; print(main.resolve_bind_host())",
                                             env={"INFINITE_CANVAS_ALLOW_LAN": value, "INFINITE_CANVAS_HOST": ""})
                seen[value or "(unset)"] = out
                ok = ok and rc == 0 and out == expected
            self.record(g, "lan_requires_explicit_env", ok, f"ALLOW_LAN -> host: {seen}")
        script = open(os.path.join(self.args.test_dir, "mac-启动服务.sh"), encoding="utf-8").read()
        self.record(g, "mac_script_opens_lan_url_only_when_enabled", "INFINITE_CANVAS_ALLOW_LAN" in script,
                    "mac-启动服务.sh 根据 INFINITE_CANVAS_ALLOW_LAN 决定打开的地址" if "INFINITE_CANVAS_ALLOW_LAN" in script else "脚本仍无条件打开局域网地址")

    # ---------- A1 ----------
    def check_A1(self):
        g = "A1"
        cases = [
            ("canvas_image_task", "POST", "/api/canvas-image-tasks", {"prompt": "audit", "provider_id": UNKNOWN_PROVIDER, "model": "gpt-image-2"}),
            ("canvas_video", "POST", "/api/canvas-video", {"prompt": "audit", "provider_id": UNKNOWN_PROVIDER, "model": "veo3-fast"}),
            ("canvas_llm", "POST", "/api/canvas-llm", {"message": "audit", "provider": UNKNOWN_PROVIDER}),
            ("online_image", "POST", "/api/online-image", {"prompt": "audit", "provider_id": UNKNOWN_PROVIDER, "model": "gpt-image-2"}),
        ]
        for name, method, path, body in cases:
            st, _, data, _ = self.c.request(method, path, body, timeout=60)
            text = detail_text(data)
            if st == 200 and isinstance(data, dict) and data.get("task_id"):
                text = f"task_id={data['task_id']} 已创建并在后台回退到其他平台"
            self.record(g, f"unknown_provider_rejected[{name}]", st == 400 and UNKNOWN_PROVIDER in text, f"status={st} {text[:90]}")
        for alias in ("comfly", ""):
            st, _, data, _ = self.c.request("POST", "/api/canvas-llm", {"message": "audit", "provider": alias}, timeout=60)
            text = detail_text(data)
            self.record(g, f"legacy_default_alias_still_resolves[{alias or 'empty'}]", "未找到 API 平台" not in text, f"status={st} {text[:90]}")

    # ---------- C5 ----------
    def check_C5(self):
        g = "C5"
        cid = self.new_canvas(nodes=[{"id": "c5", "type": "smart-text", "x": 1, "y": 1, "w": 440, "h": 250, "title": "c5", "text": ""}])
        path = self.canvas_file(cid)
        before = os.stat(path).st_ino
        cur = self.get_canvas(cid)
        st, _, _, _ = self.c.request("PUT", f"/api/canvases/{cid}", {"title": cur["title"], "icon": cur["icon"], "nodes": cur["nodes"],
                                                                    "connections": [], "viewport": cur["viewport"], "logs": [], "settings": {},
                                                                    "base_updated_at": cur["updated_at"]})
        after = os.stat(path).st_ino
        self.record(g, "canvas_save_replaces_file_atomically", st == 200 and before != after, f"inode {before} -> {after} (atomic replace changes inode)")
        leftovers = [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")]
        self.record(g, "no_temp_file_leftovers", not leftovers, f"tmp files: {leftovers[:3]}")
        rc, out, err = self.run_unit(
            "import main, os, json, tempfile; d = tempfile.mkdtemp(); p = os.path.join(d, 'state.json'); "
            "main.atomic_write_json(p, {'a': 1}); "
            "exec(\"try:\\n    main.atomic_write_json(p, {'b': 'x' * 100000, 'bad': object()})\\nexcept TypeError:\\n    pass\"); "
            "print(json.load(open(p)) == {'a': 1} and not [n for n in os.listdir(d) if n != 'state.json'])"
        )
        self.record(g, "failed_write_keeps_previous_file", rc == 0 and out == "True", f"rc={rc} out={out!r} {err[-120:] if rc else ''}")
        rc, out, err = self.run_unit(
            "import main, os, tempfile; d = tempfile.mkdtemp(); p = os.path.join(d, 'x.json'); open(p, 'w').write('{}'); os.chmod(p, 0o640); "
            "main.atomic_write_json(p, {'a': 1}); print(oct(os.stat(p).st_mode & 0o777))"
        )
        self.record(g, "file_mode_preserved", rc == 0 and out == "0o640", f"rc={rc} mode={out!r}")

    # ---------- C6 ----------
    def check_C6(self):
        g = "C6"
        cid = self.new_canvas(nodes=[{"id": "c6", "type": "smart-text", "x": 1, "y": 1, "w": 440, "h": 250, "title": "c6", "text": ""}],
                              logs=[{"id": "c6-log", "prompt": "p"}], settings={"audit": 1})
        cur = self.get_canvas(cid)
        title, icon = cur["title"], cur["icon"]
        moved = [dict(cur["nodes"][0], x=999)]
        st, _, _, _ = self.c.request("PUT", f"/api/canvases/{cid}", {"nodes": moved, "base_updated_at": cur["updated_at"]})
        after = self.get_canvas(cid)
        ok = (st == 200 and after["title"] == title and after["icon"] == icon and len(after["logs"]) == 1
              and after["settings"] == {"audit": 1} and bool(after["nodes"]) and after["nodes"][0].get("x") == 999)
        self.record(g, "omitted_fields_preserved", ok,
                    f"status={st} title={'kept' if after['title'] == title else after['title']!r} icon={after['icon']!r} logs={len(after['logs'])} settings={after['settings']} nodes={len(after['nodes'])}")

        st, _, _, _ = self.c.request("PUT", f"/api/canvases/{cid}", {"logs": [], "settings": {}, "base_updated_at": after["updated_at"]})
        after2 = self.get_canvas(cid)
        nodes_kept = bool(after2["nodes"]) and after2["nodes"][0].get("x") == 999
        self.record(g, "explicit_fields_still_update", st == 200 and after2["logs"] == [] and after2["settings"] == {} and nodes_kept,
                    f"status={st} logs={len(after2['logs'])} settings={after2['settings']} nodes_kept={nodes_kept}")

        st, _, data, _ = self.c.request("PUT", f"/api/canvases/{cid}", {"nodes": moved, "base_updated_at": after2["updated_at"] - 1})
        keys = sorted((data or {}).get("detail", {}).keys()) if isinstance((data or {}).get("detail"), dict) else []
        self.record(g, "stale_base_409_contract_unchanged", st == 409 and keys == ["canvas", "message", "updated_at"], f"status={st} detail keys={keys}")
        st, _, _, _ = self.c.request("PUT", f"/api/canvases/{cid}", {"nodes": moved, "base_updated_at": 0})
        self.record(g, "base_zero_still_accepted(unchanged)", st == 200, f"status={st}")


    # ---------- RACE（C5 + C6）----------
    def check_RACE(self):
        """logs/delete（在线程里持锁读改写）与 PUT（同一 base）几乎同时到达。

        正确实现：两者串行，一个 200、另一个 409。
        问题表现：PUT 读到对方写了一半的文件 → 5xx（C5）；或两者都 200，已删除的日志被 PUT 带回（C6 丢失更新）。
        """
        g = "RACE"
        big = "提示词" * 12000
        logs = [{"id": f"race-{i}", "prompt": big} for i in range(150)]
        rid = self.new_canvas(nodes=[{"id": "r", "type": "smart-text", "x": 0, "y": 0, "w": 440, "h": 250, "title": "r", "text": ""}], logs=logs)
        dist = {}
        both_ok = resurrected = server_errors = 0
        iterations = self.args.race_iterations
        for i in range(iterations):
            cur = self.get_canvas(rid)
            base = cur["updated_at"]
            target = cur["logs"][0]["id"]
            out = {}
            barrier = threading.Barrier(2)
            delete_delay = 0.005 if i % 2 else 0.0  # 交替两种到达顺序（基线下这两种最容易触发）

            def do_delete():
                barrier.wait()
                if delete_delay:
                    time.sleep(delete_delay)
                out["delete"] = self.c.request("POST", f"/api/canvases/{rid}/logs/delete", {"log_id": target, "base_updated_at": base})[0]

            def do_put():
                barrier.wait()
                out["put"] = self.c.request("PUT", f"/api/canvases/{rid}", {
                    "title": cur["title"], "icon": cur["icon"], "nodes": [dict(cur["nodes"][0], x=i)], "connections": [],
                    "viewport": cur["viewport"], "logs": cur["logs"], "settings": cur["settings"], "base_updated_at": base})[0]

            threads = [threading.Thread(target=do_delete), threading.Thread(target=do_put)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            key = f"delete={out.get('delete')},put={out.get('put')}"
            dist[key] = dist.get(key, 0) + 1
            if out.get("delete", 0) >= 500 or out.get("put", 0) >= 500:
                server_errors += 1
            if out.get("delete") == 200 and out.get("put") == 200:
                both_ok += 1
                if any(l["id"] == target for l in self.get_canvas(rid)["logs"]):
                    resurrected += 1
        self.record(g, "no_torn_read_5xx", server_errors == 0, f"{iterations} 轮中 {server_errors} 轮出现 5xx（读到写了一半的文件）")
        self.record(g, "no_lost_update", both_ok == 0, f"{iterations} 轮中 {both_ok} 轮两者都 200，其中 {resurrected} 轮已删除的日志被复活")
        self.record(g, "status_distribution", True, json.dumps(dist, ensure_ascii=False))

    def cleanup(self):
        for cid in self.created:
            try:
                self.c.request("DELETE", f"/api/canvases/{cid}")
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:3100")
    ap.add_argument("--test-dir", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "ic-audit-test"))
    ap.add_argument("--python", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".venv", "bin", "python"))
    ap.add_argument("--only", default="A7,A8,A1,C5,C6,RACE")
    ap.add_argument("--race-iterations", type=int, default=20)
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    args.test_dir = os.path.abspath(args.test_dir)
    args.python = os.path.abspath(args.python)
    if urllib.parse.urlparse(args.base).port == 3000:
        sys.exit("refusing to run against port 3000 (the real service); start an isolated instance with run_test_instance.sh")
    checks = Checks(args)
    try:
        for group in [g.strip() for g in args.only.split(",") if g.strip()]:
            try:
                getattr(checks, f"check_{group}")()
            except Exception as exc:  # 一个组失败不影响其他组
                checks.record(group, "error", False, repr(exc)[:300])
    finally:
        checks.cleanup()
    passed = sum(r["pass"] for r in checks.results)
    print(f"\n{passed}/{len(checks.results)} checks passed")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(checks.results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
