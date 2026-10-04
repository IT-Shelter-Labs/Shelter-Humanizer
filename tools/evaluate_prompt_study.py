"""Reproduce frozen first-pass prompts using the current application workflow.

manifest.json schema (paths relative to its directory):
{"schema_version":1,"corpus":{"path":"corpus.json","sha256":"..."},
 "templates":{"path":"user-templates.json","sha256":"..."},
 "systems":{"baseline":{"path":"baseline.txt","sha256":"..."},
            "selected":{"path":"selected.txt","sha256":"..."}}}
Templates: {"baseline":{"CASE_ID":"exact user content"},
            "selected":{"CASE_ID":"exact user content"}}.
The corpus is a list of case objects from the frozen study. Responses omit the
provider's thinking field. This script never changes GUI or model configuration.
An optional manifest "models" map pins exact tags to expected digest strings.
Optional "generation" maps tags to {"options": {...without seed...},
"think": bool, "stream": false}; "runtime_version" pins the runtime release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# A standalone checkout runner must bootstrap src before importing the application.
sys.path.insert(0, str(ROOT / "src"))
from shelter_humanizer import service  # noqa: E402
from shelter_humanizer.managed_runtime import RUNTIME_VERSION, ManagedRuntime  # noqa: E402
from shelter_humanizer.prompts import DEPTHS  # noqa: E402
from shelter_humanizer.providers import Client, ProviderConfig  # noqa: E402
from shelter_humanizer.style import GENRES  # noqa: E402

DEFAULT_MANIFEST = ROOT / "evaluations" / "russian-prompt-2026-10-04" / "manifest.json"
LOCAL_TIMEZONE = timezone(timedelta(hours=5), "Asia/Yekaterinburg")


def verified_bytes(base, entry):
    path = (base / entry["path"]).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError("Manifest artifact must stay within its directory")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("Manifest artifact SHA mismatch: " + entry["path"])
    return data


def load_inputs(manifest_path, variant, requested_cases):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema")
    base = manifest_path.parent
    corpus = json.loads(verified_bytes(base, manifest["corpus"]).decode("utf-8"))
    templates = json.loads(verified_bytes(base, manifest["templates"]).decode("utf-8"))
    systems = {
        name: verified_bytes(base, spec).decode("utf-8")
        for name, spec in manifest["systems"].items()
    }
    cases = {case["id"]: case for case in corpus}
    if len(cases) != len(corpus):
        raise ValueError("Duplicate frozen case ID")
    selected = list(cases) if requested_cases is None else requested_cases
    for case_id in selected:
        if case_id not in cases or case_id not in templates[variant]:
            raise ValueError("Missing frozen case/template: " + case_id)
        case = cases[case_id]
        data = json.loads(templates[variant][case_id].split("\n", 1)[1])
        expected = {
            "original": case["source"],
            "genre": GENRES[case["genre"]],
            "edit_goal": DEPTHS[case.get("depth", "edit")],
            "voice_sample": case.get("voice_sample", case.get("voice", "")),
            "preserve_terms": case.get("terms", []),
        }
        for field, value in expected.items():
            if field == "voice_sample" and not value.strip():
                value = ""
            actual = data.get(field, [] if field == "preserve_terms" else "")
            if actual != value:
                raise ValueError("Frozen template/corpus mismatch: " + case_id + ":" + field)
    return manifest, cases, templates[variant], systems[variant], selected


class CaptureClient(Client):
    def __init__(self, config, seed, generation=None):
        super().__init__(config)
        self.seed = seed
        self.generation = generation
        self.calls = []

    def _request(self, path, payload=None):
        if payload is None:
            return super()._request(path, payload)
        payload["options"]["seed"] = self.seed
        # These fields contain the exact sent generation contract, without host/key.
        request = {
            k: payload[k]
            for k in ("model", "messages", "stream", "options", "think")
            if k in payload
        }
        call = {"request": request, "sent": False}
        self.calls.append(call)
        started = time.monotonic()
        try:
            actual = {
                "options": {k: v for k, v in payload["options"].items() if k != "seed"},
                "think": payload.get("think"),
                "stream": payload.get("stream"),
            }
            if self.generation is not None and actual != self.generation:
                raise ValueError("Generation settings differ from manifest: " + self.config.model)
            call["sent"] = True
            response = super()._request(path, payload)
            message = response.get("message", {})
            call["response"] = {
                "done": response.get("done"),
                "done_reason": response.get("done_reason"),
                "message": {"content": message.get("content")} if isinstance(message, dict) else {},
            }
            return response
        except Exception as exc:
            call["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            call["seconds"] = round(time.monotonic() - started, 6)


def model_metadata(runtime, model, manifest):
    inventory = Client(ProviderConfig(url=runtime.url, timeout=180))._request("/api/tags")
    matches = [item for item in inventory.get("models", []) if item.get("name") == model]
    if len(matches) != 1:
        raise ValueError("Requested model tag is not uniquely installed: " + model)
    metadata = matches[0]
    expected_digest = manifest.get("models", {}).get(model)
    if expected_digest is not None and metadata.get("digest") != expected_digest:
        raise ValueError("Installed model digest differs from manifest: " + model)
    return metadata


def run(args, runtime_factory=None):
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError("Refusing to overwrite existing evidence: " + str(output))
    manifest_path = Path(args.manifest).resolve()
    manifest, cases, templates, system, selected = load_inputs(
        manifest_path, args.variant, args.case
    )
    expected_runtime = manifest.get("runtime_version")
    if expected_runtime is not None and expected_runtime != RUNTIME_VERSION:
        raise ValueError("Runtime version differs from manifest")
    generation = manifest.get("generation", {}).get(args.model)
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime = (ManagedRuntime if runtime_factory is None else runtime_factory)()
    original_messages = service.messages
    try:
        # Exclusive creation also protects against a race after the exists check.
        with output.open("x", encoding="utf-8") as journal:
            runtime.start(threading.Event(), lambda _: None)
            metadata = model_metadata(runtime, args.model, manifest)
            for case_id in selected:
                case = cases[case_id]
                print(json.dumps({"case": case_id, "status": "running"}), flush=True)

                def frozen_messages(text, genre, voice="", *, depth="edit", terms=()):
                    if (
                        text != case["source"]
                        or genre != case["genre"]
                        or depth != case.get("depth", "edit")
                        or tuple(terms) != tuple(case.get("terms", []))
                        or voice != case.get("voice_sample", case.get("voice", ""))
                    ):
                        raise ValueError("Workflow requested a different frozen input")
                    return [
                        {"role": "system", "content": system},
                        {"role": "user", "content": templates[case_id]},
                    ]

                service.messages = frozen_messages
                client = CaptureClient(
                    ProviderConfig(url=runtime.url, model=args.model, timeout=180),
                    args.seed,
                    generation,
                )
                now = datetime.now(LOCAL_TIMEZONE)
                record = {
                    "case": case_id,
                    "model": args.model,
                    "model_metadata": metadata,
                    "variant": args.variant,
                    "seed": args.seed,
                    "second_pass": args.second_pass,
                    "genre": case["genre"],
                    "depth": case.get("depth", "edit"),
                    "started_at": now.isoformat(),
                    "date": now.date().isoformat(),
                    "runtime_version": RUNTIME_VERSION,
                    "generation_pinned": generation is not None,
                    "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                    "corpus_sha256": manifest["corpus"]["sha256"],
                    "templates_sha256": manifest["templates"]["sha256"],
                    "system_sha256": manifest["systems"][args.variant]["sha256"],
                    "calls": client.calls,
                    "raw_first_output": None,
                    "final_output": None,
                }
                started = time.monotonic()
                try:
                    result = service.rewrite(
                        case["source"],
                        client,
                        genre=case["genre"],
                        voice=case.get("voice_sample", case.get("voice", "")),
                        depth=case.get("depth", "edit"),
                        terms=tuple(case.get("terms", [])),
                        second_pass=args.second_pass,
                    )
                    record.update(
                        status="accepted_by_app", final_output=result.text, warnings=result.warnings
                    )
                except Exception as exc:
                    record.update(status="error", error=f"{type(exc).__name__}: {exc}")
                if client.calls:
                    record["raw_first_output"] = (
                        client.calls[0].get("response", {}).get("message", {}).get("content")
                    )
                record["seconds"] = round(time.monotonic() - started, 6)
                journal.write(json.dumps(record, ensure_ascii=False) + "\n")
                journal.flush()
                print(
                    json.dumps(
                        {"case": case_id, "status": record["status"], "seconds": record["seconds"]}
                    ),
                    flush=True,
                )
                if record["status"] == "error" and "вовремя" in record.get("error", ""):
                    runtime.stop()
                    runtime.start(threading.Event(), lambda _: None)
                    metadata = model_metadata(runtime, args.model, manifest)
    finally:
        service.messages = original_messages
        runtime.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--model", required=True, help="Exact local model tag")
    parser.add_argument("--variant", required=True, choices=("baseline", "selected"))
    parser.add_argument("--case", action="append", help="Frozen case ID; repeat, or omit for all")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--second-pass", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
