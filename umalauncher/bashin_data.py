"""Persistent, server-validated cache for Bashin's public JSON data."""

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from loguru import logger


class BashinJsonCache:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.entries = {}

    def load_json(self, url):
        """Revalidate cached data; network failures never serve an old copy."""
        path = self.directory / (hashlib.sha256(url.encode('utf-8')).hexdigest() + '.json')
        cached = self.entries.get(url)
        if cached is None and path.is_file():
            try:
                cached = json.loads(path.read_bytes())
            except json.JSONDecodeError:
                cached = None
            if not isinstance(cached, dict) or cached.get('url') != url or 'data' not in cached:
                cached = None

        headers = {'User-Agent': 'UmaLauncher skill helper', 'Accept-Encoding': 'gzip'}
        if cached:
            if cached.get('etag'):
                headers['If-None-Match'] = cached['etag']
            elif cached.get('lastModified'):
                headers['If-Modified-Since'] = cached['lastModified']
        started = time.monotonic()
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
                content = gzip.decompress(body) if response.headers.get('Content-Encoding') == 'gzip' else body
                entry = dict(url=url, etag=response.headers.get('ETag'),
                             lastModified=response.headers.get('Last-Modified'), data=json.loads(content))
        except urllib.error.HTTPError as error:
            if error.code != 304 or cached is None:
                raise
            error.close()
            self.entries[url] = cached
            logger.debug(f'Bashin data unchanged: {url}, {time.monotonic()-started:.3f}s')
            return cached['data']

        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(entry, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        os.replace(temporary, path)
        self.entries[url] = entry
        logger.debug(f'Bashin data downloaded: {url}, {len(body)} bytes, {time.monotonic()-started:.3f}s')
        return entry['data']
