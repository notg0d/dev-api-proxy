"""Build provider-labelled Codex model catalogs from existing model metadata."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import tempfile

from provider_routing import PROVIDER_LABELS, split_model_alias


def build_provider_catalog(source: dict) -> dict:
    if not isinstance(source, dict):
        raise ValueError("Source catalog must be a JSON object")
    models = source.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("Source catalog must contain a non-empty models array")
    output = {key: copy.deepcopy(value) for key, value in source.items() if key != "models"}
    output["models"] = []
    seen = set()
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("slug"), str) or not model["slug"].strip():
            raise ValueError("Every source model must have a non-empty slug")
        if split_model_alias(model["slug"]) is not None:
            raise ValueError("Use an unqualified source catalog, not an already generated catalog")
        if model["slug"] in seen:
            raise ValueError("Source catalog contains duplicate model slugs")
        seen.add(model["slug"])
    for provider, label in PROVIDER_LABELS.items():
        for model in models:
            item = copy.deepcopy(model)
            name = re.sub(r"\s*\((?:" + "|".join(PROVIDER_LABELS.values()) + r")\)\s*$", "", item.get("display_name") or item["slug"])
            item["slug"] = provider + "/" + model["slug"]
            item["display_name"] = label + " · " + name
            item["description"] = f"{label} through the local proxy. Model access depends on the provider account."
            if item.get("comp_hash") is not None:
                item["comp_hash"] = provider + "/" + str(item["comp_hash"])
            item["priority"] = len(output["models"])
            output["models"].append(item)
    # Keep metadata available for existing chats without adding unlabelled
    # entries to the main picker.
    for model in models:
        item = copy.deepcopy(model)
        item["visibility"] = "hide"
        item["priority"] = len(output["models"])
        output["models"].append(item)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing generated catalog")
    args = parser.parse_args(argv)
    if args.source.resolve() == args.output.resolve():
        parser.error("source and output must be different files")
    if args.output.exists() and not args.overwrite:
        parser.error("output already exists; use --overwrite to replace a generated catalog")
    source = json.loads(args.source.read_text(encoding="utf-8-sig"))
    catalog = build_provider_catalog(source)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=args.output.parent,
            prefix=args.output.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(catalog, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(args.output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Saved {len(source['models']) * len(PROVIDER_LABELS)} provider-labelled picker models "
          f"and {len(source['models'])} hidden compatibility models to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
