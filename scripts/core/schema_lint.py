#!/usr/bin/env python3
"""Lint one design.json against the design.schema.json that sits beside this file.

Pure standard library. Implements a deliberately small JSON Schema (draft-07)
subset -- type / required / properties / additionalProperties / items / minItems
/ maxItems / enum / const / minimum / maximum / exclusiveMinimum /
exclusiveMaximum / minLength / pattern / local $ref -- and reports the instance
path of every violation. Assertion keywords outside that subset are not treated
as passing: they are collected once into warnings.

Exit codes: 0 = no violations, 1 = violations found, 2 = usage / file / JSON
parse error (never a traceback).

  python scripts/core/schema_lint.py <design.json> [--json] [--schema <path>] [--quiet]
"""
import argparse
import json
import os
import re
import sys

SCHEMA_NAME = "design.schema.json"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEMA = os.path.join(HERE, SCHEMA_NAME)

# Assertions this linter does not implement: reported as warnings, never as pass.
UNSUPPORTED = frozenset([
  "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
  "format", "patternProperties", "minProperties", "maxProperties",
  "propertyNames", "additionalItems", "dependencies", "dependentRequired",
  "dependentSchemas", "uniqueItems", "contains", "minContains", "maxContains",
  "maxLength", "multipleOf", "contentEncoding", "contentMediaType",
  "contentSchema", "unevaluatedItems", "unevaluatedProperties",
])

# No assertion at all: safe to ignore while scanning the schema document.
ANNOTATIONS = frozenset([
  "$schema", "$id", "$comment", "id", "title", "description", "default",
  "examples", "deprecated", "readOnly", "writeOnly", "definitions", "$defs",
])

# Values of these keywords are instance data, not subschemas: never walk into them.
DATA_KEYWORDS = frozenset(["enum", "const", "default", "examples", "required"])

KNOWN_TYPES = frozenset(["object", "array", "string", "boolean", "null", "integer", "number"])


def preview(value, limit=120):
  text = json.dumps(value, ensure_ascii=False, sort_keys=False)
  return text if len(text) <= limit else text[: limit - 3] + "..."


def json_type(value):
  if value is None:
    return "null"
  if isinstance(value, bool):
    return "boolean"
  if isinstance(value, int):
    return "integer"
  if isinstance(value, float):
    return "number"
  if isinstance(value, str):
    return "string"
  if isinstance(value, list):
    return "array"
  if isinstance(value, dict):
    return "object"
  return type(value).__name__


def type_ok(value, name):
  if name == "object":
    return isinstance(value, dict)
  if name == "array":
    return isinstance(value, list)
  if name == "string":
    return isinstance(value, str)
  if name == "boolean":
    return isinstance(value, bool)
  if name == "null":
    return value is None
  if name == "integer":
    return isinstance(value, int) and not isinstance(value, bool)
  if name == "number":
    return isinstance(value, (int, float)) and not isinstance(value, bool)
  return False


def same(left, right):
  """Strict equality: JSON true must not equal 1, and 1.0 equals 1."""
  if isinstance(left, bool) or isinstance(right, bool):
    return isinstance(left, bool) and isinstance(right, bool) and left == right
  if isinstance(left, (int, float)) and isinstance(right, (int, float)):
    return left == right
  if isinstance(left, list) and isinstance(right, list):
    return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
  if isinstance(left, dict) and isinstance(right, dict):
    return sorted(left) == sorted(right) and all(same(left[k], right[k]) for k in left)
  return type(left) is type(right) and left == right


def child_path(path, key):
  return "%s.%s" % (path, key) if path else str(key)


def item_path(path, index):
  return "%s[%d]" % (path, index)


def shown(path):
  return path or "$"


def load_json(path):
  with open(path, "rb") as handle:
    raw = handle.read()
  return json.loads(raw.decode("utf-8-sig"))


def scan_schema(schema):
  """Collect every assertion keyword this linter cannot enforce, once per location."""
  found = []
  seen = set()

  def walk(node, path):
    if isinstance(node, dict):
      for key in node:
        marker = (key, path)
        if key in UNSUPPORTED and marker not in seen:
          seen.add(marker)
          found.append({
            "keyword": key,
            "schema_path": path or "$",
            "message": 'keyword "%s" at %s is not implemented; instances are not checked against it'
              % (key, path or "$"),
          })
      for key, sub in node.items():
        if key in DATA_KEYWORDS:
          continue
        walk(sub, child_path(path, key))
    elif isinstance(node, list):
      for index, sub in enumerate(node):
        walk(sub, item_path(path, index))

  walk(schema, "")
  return found


class Linter(object):
  def __init__(self, schema):
    self.schema = schema
    self.errors = []
    self.warnings = scan_schema(schema)
    self._known = set((w["keyword"], w["schema_path"]) for w in self.warnings)

  def error(self, path, message):
    self.errors.append({"path": shown(path), "message": message})

  def warn(self, keyword, schema_path, message):
    marker = (keyword, schema_path)
    if marker in self._known:
      return
    self._known.add(marker)
    self.warnings.append({"keyword": keyword, "schema_path": schema_path or "$", "message": message})

  def _pointer(self, ref, schema_path):
    node = self.schema
    pointer = ref[1:]
    if pointer in ("", "/"):
      return self.schema
    for part in pointer.lstrip("/").split("/"):
      part = part.replace("~1", "/").replace("~0", "~")
      if isinstance(node, dict) and part in node:
        node = node[part]
      elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
        node = node[int(part)]
      else:
        self.warn("$ref", schema_path,
                  'unresolvable $ref "%s" at %s; instance is not checked against it' % (ref, schema_path or "$"))
        return None
    return node

  def deref(self, node, schema_path):
    hops = 0
    while isinstance(node, dict) and "$ref" in node:
      ref = node["$ref"]
      if not isinstance(ref, str) or not ref.startswith("#"):
        self.warn("$ref", schema_path,
                  'non-local $ref %s at %s is not resolved; instance is not checked against it'
                  % (preview(ref), schema_path or "$"))
        return None
      node = self._pointer(ref, schema_path)
      if node is None:
        return None
      hops += 1
      if hops > 32:
        self.warn("$ref", schema_path,
                  'circular $ref chain at %s; instance is not checked against it' % (schema_path or "$"))
        return None
    return node

  def validate(self, value, node, schema_path, inst_path):
    node = self.deref(node, schema_path)
    if not isinstance(node, dict):
      return

    declared = node.get("type")
    if declared is not None:
      names = [declared] if isinstance(declared, str) else [n for n in declared if isinstance(n, str)]
      unknown = [n for n in names if n not in KNOWN_TYPES]
      if unknown or not names:
        self.warn("type", schema_path,
                  'unknown type name %s at %s; instance is not checked against it'
                  % (preview(unknown or declared), schema_path or "$"))
        return
      if not any(type_ok(value, name) for name in names):
        self.error(inst_path, "expected %s, got %s" % ("|".join(names), json_type(value)))
        return

    if "const" in node and not same(value, node["const"]):
      self.error(inst_path, "expected const %s, got %s" % (preview(node["const"]), preview(value)))
    if "enum" in node and not any(same(value, option) for option in node["enum"]):
      self.error(inst_path, "%s is not one of the %d allowed values %s"
                % (preview(value), len(node["enum"]), preview(node["enum"])))

    if isinstance(value, dict):
      self._object(value, node, schema_path, inst_path)
    elif isinstance(value, list):
      self._array(value, node, schema_path, inst_path)
    elif isinstance(value, str):
      self._string(value, node, schema_path, inst_path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
      self._number(value, node, inst_path)

  def _object(self, value, node, schema_path, inst_path):
    required = node.get("required")
    if isinstance(required, list):
      for key in required:
        if isinstance(key, str) and key not in value:
          self.error(child_path(inst_path, key), "missing required property %s" % preview(key))
    props = node.get("properties")
    props = props if isinstance(props, dict) else {}
    extra = node.get("additionalProperties", True)
    for key, sub in value.items():
      if key in props:
        self.validate(sub, props[key], child_path(child_path(schema_path, "properties"), key),
                      child_path(inst_path, key))
      elif extra is False:
        self.error(child_path(inst_path, key), "additional property %s is not allowed here" % preview(key))
      elif isinstance(extra, dict):
        self.validate(sub, extra, child_path(schema_path, "additionalProperties"), child_path(inst_path, key))

  def _array(self, value, node, schema_path, inst_path):
    if "minItems" in node and len(value) < node["minItems"]:
      self.error(inst_path, "%d items < minItems %d" % (len(value), node["minItems"]))
    if "maxItems" in node and len(value) > node["maxItems"]:
      self.error(inst_path, "%d items > maxItems %d" % (len(value), node["maxItems"]))
    items = node.get("items")
    if isinstance(items, dict):
      for index, item in enumerate(value):
        self.validate(item, items, child_path(schema_path, "items"), item_path(inst_path, index))
    elif isinstance(items, list):
      for index, sub in enumerate(items):
        if index < len(value):
          self.validate(value[index], sub, item_path(child_path(schema_path, "items"), index),
                        item_path(inst_path, index))

  def _string(self, value, node, schema_path, inst_path):
    if "minLength" in node and len(value) < node["minLength"]:
      self.error(inst_path, "string shorter than minLength %d (%d)" % (node["minLength"], len(value)))
    if "pattern" in node:
      try:
        matched = re.search(node["pattern"], value)
      except re.error as exc:
        self.warn("pattern", schema_path,
                  'invalid pattern %s at %s (%s); instance is not checked against it'
                  % (preview(node["pattern"]), schema_path or "$", exc))
        return
      if not matched:
        self.error(inst_path, "%s does not match pattern %s" % (preview(value), preview(node["pattern"])))

  def _number(self, value, node, inst_path):
    if "minimum" in node and value < node["minimum"]:
      self.error(inst_path, "%s < minimum %s" % (preview(value), preview(node["minimum"])))
    if "maximum" in node and value > node["maximum"]:
      self.error(inst_path, "%s > maximum %s" % (preview(value), preview(node["maximum"])))
    if "exclusiveMinimum" in node and value <= node["exclusiveMinimum"]:
      self.error(inst_path, "%s <= exclusiveMinimum %s" % (preview(value), preview(node["exclusiveMinimum"])))
    if "exclusiveMaximum" in node and value >= node["exclusiveMaximum"]:
      self.error(inst_path, "%s >= exclusiveMaximum %s" % (preview(value), preview(node["exclusiveMaximum"])))


def build_parser():
  parser = argparse.ArgumentParser(
    prog="schema_lint.py",
    description="Validate a design.json against design.schema.json and print violating paths.")
  parser.add_argument("target", help="design.json to validate")
  parser.add_argument("--json", dest="as_json", action="store_true",
                      help="print one JSON report object instead of text")
  parser.add_argument("--schema", dest="schema", default=None,
                      help="schema path (default: %s beside this script)" % SCHEMA_NAME)
  parser.add_argument("--quiet", action="store_true",
                      help="hide warning lines and the unsupported-keyword notice")
  return parser


def emit(args, report, code):
  if args.as_json:
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return code
  lines = []
  for err in report["errors"]:
    lines.append("%s: %s" % (err["path"], err["message"]))
  if not args.quiet:
    for warn in report["warnings"]:
      lines.append("warning: %s" % warn["message"])
  lines.append("errors %d / warnings %d" % (len(report["errors"]), len(report["warnings"])))
  sys.stdout.write("\n".join(lines) + "\n")
  return code


def fatal(args, schema_path, target_path, message):
  report = {
    "ok": False,
    "errors": [{"path": "", "message": message}],
    "warnings": [],
    "schema": schema_path,
    "target": target_path,
  }
  if args.as_json:
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
  else:
    sys.stderr.write("error: %s\n" % message)
  return 2


def main(argv=None):
  for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
      stream.reconfigure(encoding="utf-8")
  args = build_parser().parse_args(argv)
  schema_path = os.path.abspath(args.schema or DEFAULT_SCHEMA)
  target_path = os.path.abspath(args.target)

  try:
    schema = load_json(schema_path)
  except OSError as exc:
    return fatal(args, schema_path, target_path,
                "cannot read schema %s: %s" % (schema_path, exc.strerror or exc))
  except ValueError as exc:
    return fatal(args, schema_path, target_path, "schema %s is not valid UTF-8 JSON: %s" % (schema_path, exc))

  try:
    design = load_json(target_path)
  except OSError as exc:
    return fatal(args, schema_path, target_path,
                "cannot read target %s: %s" % (target_path, exc.strerror or exc))
  except ValueError as exc:
    return fatal(args, schema_path, target_path, "target %s is not valid UTF-8 JSON: %s" % (target_path, exc))

  linter = Linter(schema)
  linter.validate(design, schema, "", "")
  report = {
    "ok": not linter.errors,
    "errors": linter.errors,
    "warnings": linter.warnings,
    "schema": schema_path,
    "target": target_path,
  }
  return emit(args, report, 0 if report["ok"] else 1)


if __name__ == "__main__":
  sys.exit(main())
