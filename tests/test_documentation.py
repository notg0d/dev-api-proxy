"""Validate release docs and credential-free client examples without network calls."""

import json
from pathlib import Path
import re
import sys
import unittest
from urllib.parse import unquote, urlsplit

try:
    import tomllib
except ImportError:
    tomllib = None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from version import VERSION


def prose(text):
    return re.sub(r"```[^\n]*\n.*?```", "", text, flags=re.DOTALL)


def anchors(text):
    found = set()
    counts = {}
    for title in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$", prose(text)):
        slug = re.sub(r"[^\w\- ]", "", title.lower()).strip().replace(" ", "-")
        number = counts.get(slug, 0)
        counts[slug] = number + 1
        found.add(slug if number == 0 else slug + "-" + str(number))
    return found


class DocumentationTests(unittest.TestCase):
    def test_local_links_and_anchors_resolve(self):
        files = [ROOT / "README.md", ROOT / "CHANGELOG.md", *sorted((ROOT / "docs").rglob("*.md"))]
        for file in files:
            text = file.read_text(encoding="utf-8")
            self.assertEqual(text.count("```") % 2, 0, str(file))
            for link in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", prose(text)):
                url = urlsplit(link)
                if url.scheme or url.netloc:
                    continue
                target = (file.parent / unquote(url.path)).resolve() if url.path else file
                with self.subTest(file=str(file.relative_to(ROOT)), link=link):
                    self.assertTrue(target.exists(), "Missing local link target")
                    self.assertTrue(target.is_relative_to(ROOT), "Link escapes the project")
                    if url.fragment and target.suffix == ".md":
                        self.assertIn(unquote(url.fragment), anchors(target.read_text(encoding="utf-8")))

    def test_release_notes_use_the_shared_version(self):
        notes = ROOT / "docs/releases" / ("v" + VERSION + ".md")
        self.assertTrue(notes.is_file())
        self.assertIn("v" + VERSION, notes.read_text(encoding="utf-8"))
        self.assertIn("v" + VERSION, (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("## " + VERSION, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))

    def test_opencode_example_has_separate_provider_routes_and_no_literal_keys(self):
        text = (ROOT / "examples/opencode.jsonc").read_text(encoding="utf-8")
        config = json.loads(re.sub(r"(?m)^\s*//.*$", "", text))
        expected = {"Xingchi": "xingchi", "OmniAi": "omniai", "FreeGG": "freegg", "FreeGG-Anthropic": "freegg"}
        for provider, route in expected.items():
            definition = config["providers"][provider]
            self.assertEqual(definition["settings"]["baseURL"], "http://127.0.0.1:8789/" + route + "/v1")
            self.assertNotIn("apiKey", definition)
            self.assertNotIn("apiKey", definition["settings"])

    @unittest.skipIf(tomllib is None, "Standard-library TOML validation requires Python 3.11+")
    def test_codex_examples_have_distinct_auth_sources(self):
        config = tomllib.loads((ROOT / "examples/codex.toml").read_text(encoding="utf-8"))
        for provider in ("xingchi", "omniai", "freegg"):
            definition = config["model_providers"][provider]
            self.assertEqual(definition["env_key"], provider.upper() + "_API_KEY")
            self.assertNotIn("experimental_bearer_token", definition)
        picker = tomllib.loads((ROOT / "examples/chatgpt-picker.toml").read_text(encoding="utf-8"))
        self.assertEqual(picker["model"], "xingchi/gpt-6.1-sol")
        self.assertFalse(picker["features"]["enable_request_compression"])
        self.assertEqual(picker["model_providers"]["local-proxy"]["http_headers"], {"X-Proxy-Model-Routing": "1"})


if __name__ == "__main__":
    unittest.main()
