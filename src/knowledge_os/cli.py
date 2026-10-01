"""KnowledgeOS command line interface."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import shlex
import subprocess
from pathlib import Path

from . import __version__
from .api import AccessControl, KnowledgeServer
from .core import Config, KnowledgeError, Registry, ValidationError
from .recovery import backup, restore
from .service import Service


LOCAL_DEV_TOKEN = "local-admin-token"
LOCAL_DEV_REVIEWER_TOKEN = "local-reviewer-token"


def local_dev_access_control():
    """Explicit loopback development credential; never used by normal serve."""
    return AccessControl.from_env({"KNOWLEDGEOS_AUTH_MODE": "required", "KNOWLEDGEOS_AUTH_TOKENS": json.dumps({
        LOCAL_DEV_TOKEN: {"principal": "local:admin", "roles": ["admin"]},
        LOCAL_DEV_REVIEWER_TOKEN: {"principal": "local:reviewer", "roles": ["admin"]},
    })})


def parser():
    root = argparse.ArgumentParser(prog="knowledgeos")
    root.add_argument("--root")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("doctor", "control", "rebuild", "compile", "verify-ledger", "version"):
        commands.add_parser(name)
    for name, label in (("get", "id"), ("resolve", "query"), ("search", "query"), ("history", "id"),
                        ("explain", "target"), ("agent-skill", "id")):
        sub = commands.add_parser(name)
        sub.add_argument(label)
        if name == "get":
            sub.add_argument("--history", action="store_true")
    sub = commands.add_parser("neighbors")
    sub.add_argument("id")
    sub.add_argument("--depth", type=int, default=1)
    sub.add_argument("--relations")
    sub = commands.add_parser("context")
    sub.add_argument("id")
    sub.add_argument("--domain", required=True)
    sub.add_argument("--as-of")
    sub.add_argument("--recorded-as-of")
    sub = commands.add_parser("calculate")
    sub.add_argument("model")
    sub.add_argument("--inputs", required=True)
    sub.add_argument("--scenario", default="default")
    sub = commands.add_parser("agent-capabilities")
    sub.add_argument("--domain")
    sub = commands.add_parser("agent-request")
    sub.add_argument("question")
    sub.add_argument("--domain", required=True)
    sub.add_argument("--target")
    sub.add_argument("--query")
    sub.add_argument("--skill")
    sub.add_argument("--as-of")
    sub.add_argument("--max-items", type=int, default=25)
    sub = commands.add_parser("agent-respond")
    sub.add_argument("--request", required=True)
    sub.add_argument("--model-response", required=True)
    sub.add_argument("--tool-results")
    sub = commands.add_parser("agent-invoke")
    sub.add_argument("operation")
    sub.add_argument("--domain", required=True)
    sub.add_argument("--arguments", default="{}")
    sub = commands.add_parser("extract-contract")
    sub.add_argument("--source-type", required=True)
    sub.add_argument("--source")
    sub.add_argument("--content-file")
    sub.add_argument("--locator")
    sub.add_argument("--profile", default="default")
    sub.add_argument("--materializer-command")
    sub.add_argument("--materializer-timeout", type=int, default=120)
    sub = commands.add_parser("extract")
    sub.add_argument("--source-type", required=True)
    sub.add_argument("--source")
    sub.add_argument("--content-file")
    sub.add_argument("--locator")
    sub.add_argument("--profile", default="default")
    group = sub.add_mutually_exclusive_group(required=True)
    group.add_argument("--model-response")
    group.add_argument("--model-command")
    sub.add_argument("--timeout", type=int, default=120)
    sub.add_argument("--materializer-command")
    sub.add_argument("--materializer-timeout", type=int, default=120)
    sub = commands.add_parser("ingest")
    sub.add_argument("--input", required=True)
    sub.add_argument("--mapping", required=True)
    sub.add_argument("--isolate-errors", action="store_true")
    sub = commands.add_parser("review")
    sub.add_argument("--priority")
    sub.add_argument("--limit", type=int, default=100)
    sub = commands.add_parser("propose")
    sub.add_argument("--actor", required=True)
    sub.add_argument("--target-source", required=True)
    sub.add_argument("--patch", required=True)
    sub.add_argument("--reason", required=True)
    sub.add_argument("--risk", default="normal")
    sub = commands.add_parser("review-changeset")
    sub.add_argument("id")
    sub.add_argument("--reviewer", required=True)
    sub.add_argument("--decision", required=True)
    sub = commands.add_parser("publish-changeset")
    sub.add_argument("id")
    sub.add_argument("--publisher", required=True)
    sub.add_argument("--source-revision", required=True)
    for name in ("backup", "restore"):
        sub = commands.add_parser(name)
        sub.add_argument("directory")
    sub = commands.add_parser("serve")
    sub.add_argument("--bind", default="127.0.0.1")
    sub.add_argument("--port", type=int, default=8787)
    sub = commands.add_parser("dev-serve", help="loopback-only development API with a fixed local admin token")
    sub.add_argument("--port", type=int, default=8787)
    return root


def _source(args):
    content = None
    if args.content_file:
        content = open(args.content_file, encoding="utf-8").read()
    elif args.source and args.source_type in ("file", "meeting_minutes") and Path(args.source).suffix.lower() in (
            ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".html", ".htm", ".xml", ".vtt", ".srt"):
        content = open(args.source, encoding="utf-8").read()
    source = {"kind": args.source_type, "locator": args.locator or args.source, "content": content, "metadata": {}}
    if not content and args.materializer_command:
        materialized = _command(args.materializer_command, {"protocol_version": "knowledgeos.materialization.v1", "source": source},
                                args.materializer_timeout)
        if not materialized.get("content"):
            raise ValidationError("materializer output is missing content")
        source.update({"content": materialized["content"], "mime_type": materialized.get("mime_type"),
                       "metadata": materialized.get("metadata") or {}})
    if not source["content"]:
        raise ValidationError(f"{args.source_type} source requires content or a materializer adapter")
    return source


def _command(command, payload, timeout):
    argv = shlex.split(command)
    if not argv:
        raise ValidationError("adapter command is required")
    try:
        completed = subprocess.run(argv, input=json.dumps(payload, ensure_ascii=False), text=True,
                                   capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValidationError(f"adapter command timed out after {timeout}s") from exc
    if completed.returncode:
        raise ValidationError(f"adapter command failed ({completed.returncode}): {completed.stderr[:500]}")
    output = completed.stdout.strip()
    if output.startswith("```") and output.endswith("```"):
        output = output[3:-3].removeprefix("json").strip()
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"adapter returned invalid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise ValidationError("adapter output must be a JSON object")
    return result


def run(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (["--version"], ["-v"]):
        print(__version__)
        return 0
    args = parser().parse_args(argv)
    config = Config(args.root)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "doctor":
        checks = {"root": str(config.root), "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                  "control_dir": config.control.is_dir(), "knowledge_dir": config.knowledge.is_dir(),
                  "predicates": len(Registry(config).predicates)}
        checks["healthy"] = checks["control_dir"] and checks["knowledge_dir"]
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 0 if checks["healthy"] else 1
    if args.command == "backup":
        result = backup(config, args.directory)
    elif args.command == "restore":
        result = restore(config, args.directory)
    else:
        service = Service(config)
        try:
            command = args.command
            if command in ("rebuild", "compile"):
                result = service.compile(rebuild=command == "rebuild")
            elif command == "control":
                result = service.control_plane()
            elif command == "verify-ledger":
                result = service.ledger.verify()
            elif command == "get":
                result = service.get(args.id, args.history)
            elif command == "resolve":
                result = service.resolve(args.query)
            elif command == "search":
                result = service.search(args.query)
            elif command == "neighbors":
                result = service.neighbors(args.id, args.relations, args.depth)
            elif command == "history":
                result = service.history(args.id)
            elif command == "explain":
                result = service.explain(args.target)
            elif command == "context":
                result = service.context(args.id, domain=args.domain, as_of=args.as_of, recorded_as_of=args.recorded_as_of)
            elif command == "calculate":
                result = service.calculate(args.model, json.loads(args.inputs), args.scenario)
            elif command == "agent-capabilities":
                result = service.agent_capabilities(args.domain)
            elif command == "agent-skill":
                result = service.agent_skill(args.id)
            elif command == "agent-request":
                result = service.agent_request(question=args.question, domain=args.domain, target=args.target, query=args.query,
                                               skill=args.skill, as_of=args.as_of, max_items=args.max_items)
            elif command == "agent-respond":
                request = json.load(open(args.request))
                if isinstance(request.get("data"), dict):
                    request = request["data"]
                tool_results = json.load(open(args.tool_results)) if args.tool_results else []
                result = service.agent_respond(request=request, model_output=json.load(open(args.model_response)), tool_results=tool_results)
            elif command == "agent-invoke":
                result = service.agent_invoke(domain=args.domain, operation=args.operation, arguments=json.loads(args.arguments))
            elif command == "extract-contract":
                result = service.extraction_request(source=_source(args), profile=args.profile)
            elif command == "extract":
                request = service.extraction_request(source=_source(args), profile=args.profile)["data"]
                model_output = json.load(open(args.model_response)) if args.model_response else _command(args.model_command, request, args.timeout)
                result = service.extraction_candidates(request=request, model_output=model_output, profile=args.profile)
            elif command == "ingest":
                from .connector import Connector
                result = Connector(service).ingest_ndjson(args.input, args.mapping, args.isolate_errors)
            elif command == "review":
                result = service.review(args.priority, args.limit)
            elif command == "propose":
                result = service.propose(actor=args.actor, target_source=args.target_source, patch=json.loads(args.patch),
                                         reason=args.reason, risk=args.risk)
            elif command == "review-changeset":
                result = service.review_changeset(id=args.id, reviewer=args.reviewer, decision=args.decision)
            elif command == "publish-changeset":
                result = service.publish_changeset(id=args.id, publisher=args.publisher, source_revision=args.source_revision)
            elif command in ("serve", "dev-serve"):
                bind = args.bind if command == "serve" else "127.0.0.1"
                auth = AccessControl.from_env() if command == "serve" else local_dev_access_control()
                server = KnowledgeServer((bind, args.port), service, auth)
                print(f"KnowledgeOS API listening on http://{bind}:{args.port}", file=sys.stderr)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    server.server_close()
                return 0
            else:
                raise ValidationError(f"unknown command: {command}")
        finally:
            service.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main():
    try:
        return run()
    except (KnowledgeError, KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
