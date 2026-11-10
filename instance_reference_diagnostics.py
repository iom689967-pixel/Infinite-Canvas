"""Opt-in, allowlisted reference diagnostics. Never serialize exceptions or URLs."""
import json
import secrets
import time
import zlib

import httpx
import httpcore


def exception_class(exc):
    """Identity allowlist, never trust attacker-defined class/module names."""
    if exc is None:
        return "none"
    for module in (httpx, httpcore):
        for name in ("ReadError", "ReadTimeout", "ConnectError", "ConnectTimeout",
                     "WriteError", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
                     "LocalProtocolError", "DecodingError", "StreamClosed", "StreamConsumed",
                     "ResponseNotRead", "NetworkError", "ProtocolError", "TimeoutException"):
            cls = getattr(module, name, None)
            if cls is not None and type(exc) is cls:
                return module.__name__ + "." + name
    if type(exc) is zlib.error:
        return "zlib.error"
    from providers.kie.uploads import KieReferenceError
    from fastapi import HTTPException
    for cls in (KieReferenceError, HTTPException, TimeoutError, TypeError, ValueError, PermissionError):
        if type(exc) is cls:
            return cls.__name__
    return "OtherException"



class ReferenceDiagnostics:
    STAGES = {'prepare', 'resolve', 'cache', 'read', 'normalize', 'normalized',
              'upload', 'response', 'json', 'extract', 'media', 'dns',
              'http', 'redirect', 'complete', 'body_read', 'response_rebuild'}
    NUMBERS = {'reference_index', 'file_size', 'width', 'height', 'http_status', 'redirect_count', 'content_length', 'body_bytes_read'}
    BOOLEANS = {'exists', 'empty', 'json_ok', 'has_data', 'has_download_url', 'has_file_url', 'content_length_present',
                'streaming', 'headers_received', 'body_read_started'}

    def __init__(self):
        self.trace = secrets.token_hex(8)
        self.started = time.perf_counter()
        self.stage = 'prepare'
        self.index = 0

    def __call__(self, stage, **values):
        if stage not in self.STAGES:
            return
        self.stage = stage
        if type(values.get('reference_index')) is int:
            self.index = values['reference_index']
        record = {'event': 'instance_reference_stage', 'trace_id': self.trace,
                  'stage': stage, 'reference_index': self.index,
                  'elapsed_ms': round((time.perf_counter()-self.started)*1000, 3)}
        for k, v in values.items():
            if k in self.NUMBERS and type(v) is int and 0 <= v <= 100_000_000:
                record[k] = v
            elif k in self.BOOLEANS and type(v) is bool:
                record[k] = v
            elif k == 'media_type':
                record[k] = v if v in {'image/png', 'image/jpeg', 'image/webp', 'application/json', 'text/html'} else 'other'
            elif k in {'content_encoding', 'transfer_encoding'}:
                record[k] = v if v in {'identity', 'gzip', 'deflate', 'br', 'zstd', 'chunked', 'none'} else 'other'
            elif k == 'http_version':
                record[k] = v if v in {'HTTP/1.0', 'HTTP/1.1', 'HTTP/2', 'HTTP/3'} else 'other'
            elif k == 'destination' and v in {'public', 'rejected', 'mock', 'approved_private'}:
                record[k] = v
        print(json.dumps(record, sort_keys=True), flush=True)

    def failed(self, exc):
        outer = exception_class(exc)
        families = {'httpx_family': 'none', 'httpcore_family': 'none'}
        pending, seen = [exc], set()
        while pending and len(seen) < 16:
            item = pending.pop(0)
            if item is None or id(item) in seen:
                continue
            seen.add(id(item))
            kind = exception_class(item)
            for module in ('httpx', 'httpcore'):
                if kind.startswith(module+'.') and families[module+'_family'] == 'none':
                    families[module+'_family'] = kind
            pending.extend((item.__cause__, item.__context__))
        print(json.dumps({'event': 'instance_reference_failed', 'trace_id': self.trace,
                          'stage': self.stage, 'reference_index': self.index,
                          'error_category': outer, 'outer_exception': outer,
                          'cause_exception': exception_class(exc.__cause__),
                          'context_exception': exception_class(exc.__context__),
                          'elapsed_ms': round((time.perf_counter()-self.started)*1000, 3),
                          **families}, sort_keys=True), flush=True)
