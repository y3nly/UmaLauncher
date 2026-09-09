"""Local simulator inputs and seed-aware reuse between skill-helper requests."""

import hashlib
import json
import os
import secrets
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path


def canonical_payload(payload):
    result = dict(payload)
    for key in ("acquiredSkillIds", "unacquiredSkillIds", "forceActivateSkillIds"):
        if key in result:
            result[key] = sorted(set(result[key]))
    return result


def fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


CM_INDEX_URL = "https://bashin.app/cm/cm_index.json"


def load_cm_configs():
    """Load the complete published selector/config from Bashin, without a fallback."""
    request = urllib.request.Request(CM_INDEX_URL, headers={"User-Agent": "UmaLauncher skill helper"})
    with urllib.request.urlopen(request, timeout=5) as response:
        data = json.load(response)
    entries = data.get("cms") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("Bashin CM index is empty or invalid")
    configs = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid Bashin CM definition")
        for field in ("cmId", "courseId", "location", "season", "weather"):
            if type(entry.get(field)) is not int or entry[field] <= 0:
                raise ValueError(f"Invalid Bashin CM field: {field}")
        if not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError("Bashin CM name is missing")
        if entry.get("groundCondition") not in ("GOOD", "YAYAOMO", "OMO", "BAD"):
            raise ValueError("Invalid Bashin CM ground condition")
        cm_id = entry["cmId"]
        if cm_id in configs:
            raise ValueError(f"Duplicate Bashin CM definition: {cm_id}")
        configs[cm_id] = dict(name=entry["name"], course=entry["courseId"],
                              location=entry["location"], season=entry["season"],
                              weather=entry["weather"], ground_condition=entry["groundCondition"])
    return configs


class SkillDataSnapshot:
    """Revalidate downloaded data before use; failed requests never use stale data."""

    URL = "https://bashin.app/data/skill_data.txt"

    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_path = self.cache_dir / "current.json"
        self.path = None
        self.digest = None
        self.etag = None
        self.modified = None
        try:
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            cached = self.cache_dir / (metadata["digest"] + ".json")
            if hashlib.sha256(cached.read_bytes()).hexdigest() == metadata["digest"]:
                self.path = cached
                self.digest = metadata["digest"]
                self.etag = metadata.get("etag")
                self.modified = metadata.get("modified")
        except (OSError, ValueError, KeyError):
            pass  # No usable HTTP cache; the next request must download data.

    def refresh(self):
        headers = {"User-Agent": "UmaLauncher skill helper"}
        if self.etag:
            headers["If-None-Match"] = self.etag
        if self.modified:
            headers["If-Modified-Since"] = self.modified
        try:
            request = urllib.request.Request(self.URL, headers=headers)
            try:
                response = urllib.request.urlopen(request, timeout=5)
            except urllib.error.HTTPError as error:
                if error.code != 304:
                    raise
                if self.path is None or hashlib.sha256(self.path.read_bytes()).hexdigest() != self.digest:
                    raise ValueError("Revalidated skill-data cache is missing or invalid")
                return
            with response:
                content = response.read()
                skills = json.loads(content)
                if not isinstance(skills, list) or not skills or not all(
                    isinstance(skill, dict) and "id" in skill and "invokes" in skill
                    for skill in skills
                ):
                    raise ValueError("Invalid simulator skill-data response")
                digest = hashlib.sha256(content).hexdigest()
                path = self.cache_dir / (digest + ".json")
                temporary = path.with_suffix(f".{os.getpid()}.tmp")
                temporary.write_bytes(content)
                os.replace(temporary, path)
                self.path, self.digest = path, digest
                self.etag = response.headers.get("ETag")
                self.modified = response.headers.get("Last-Modified")
            metadata = dict(digest=self.digest, etag=self.etag, modified=self.modified)
            temporary = self.metadata_path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(metadata), encoding="utf-8")
            os.replace(temporary, self.metadata_path)
        except Exception:
            self.path = None
            self.digest = None
            self.etag = None
            self.modified = None
            raise


class CandidateCache:
    """Access under the worker condition; mutable race state never enters this cache."""

    def __init__(self, limit=12):
        self.limit = limit
        self.contexts = OrderedDict()

    @staticmethod
    def context_key(payload, engine):
        baseline = canonical_payload(payload)
        baseline.pop("unacquiredSkillIds", None)
        return fingerprint([engine, baseline])

    def discard(self, payload, engine):
        self.contexts.pop(self.context_key(payload, engine), None)

    def get(self, payload, engine):
        key = self.context_key(payload, engine)
        entry = self.contexts.get(key)
        requested = set(payload["unacquiredSkillIds"])
        if entry is None or entry["result"] is None or not requested <= entry["evaluated"]:
            return None
        self.contexts.move_to_end(key)
        result = dict(entry["result"])
        result["candidates"] = {
            key: value for key, value in result["candidates"].items()
            if int(key) in requested
        }
        return result

    def missing_request(self, payload, engine):
        key = self.context_key(payload, engine)
        if key not in self.contexts:
            self.contexts[key] = dict(seed=payload.get("seedBase", secrets.randbits(63)),
                                      evaluated=set(), result=None)
        entry = self.contexts[key]
        self.contexts.move_to_end(key)
        while len(self.contexts) > self.limit:
            self.contexts.popitem(last=False)
        request = canonical_payload(payload)
        request["seedBase"] = entry["seed"]
        request["unacquiredSkillIds"] = sorted(
            set(request["unacquiredSkillIds"]) - entry["evaluated"]
        )
        return key, request

    def merge(self, key, request, result):
        entry = self.contexts[key]
        if result.get("seedBase") != entry["seed"]:
            raise ValueError("Simulator returned a different comparison seed")
        previous = entry["result"]
        metadata = {key: value for key, value in result.items() if key != "candidates"}
        if previous is not None:
            if metadata != {key: value for key, value in previous.items() if key != "candidates"}:
                raise ValueError("Simulator baseline changed during incremental evaluation")
            result = dict(result, candidates={**previous["candidates"], **result["candidates"]})
        entry["result"] = result
        # The CLI can omit unknown candidates. Remember that they were evaluated.
        entry["evaluated"].update(request["unacquiredSkillIds"])
