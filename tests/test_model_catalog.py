"""Provider labels and routed IDs must retain their source model metadata."""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model_catalog import build_provider_catalog
from provider_routing import split_model_alias


class ModelCatalogTests(unittest.TestCase):
    def test_catalog_has_distinct_labels_and_routeable_ids(self):
        source = {"models": [
            {"slug": "gpt-test", "display_name": "GPT Test (Xingchi)", "context_window": 1000000,
             "comp_hash": "source-cache-hash",
             "model_messages": {"instructions_template": "Original instructions"}},
            {"slug": "gpt-other", "display_name": "GPT Other", "supported_reasoning_levels": [{"effort": "high"}]},
        ]}
        original = copy.deepcopy(source)
        catalog = build_provider_catalog(source)
        self.assertEqual(len(catalog["models"]), 10)
        self.assertEqual(source, original)
        self.assertEqual([item["priority"] for item in catalog["models"]], list(range(10)))
        for provider, label, offset in (("xingchi", "Xingchi", 0), ("omniai", "OmniAi", 2),
                                        ("freegg", "FreeGG", 4), ("moyuu", "Moyuu", 6)):
            first = catalog["models"][offset]
            self.assertEqual(split_model_alias(first["slug"]), (provider, "gpt-test"))
            self.assertEqual(first["display_name"], label + " · GPT Test")
            self.assertEqual(first["model_messages"], original["models"][0]["model_messages"])
            self.assertEqual(first["context_window"], 1000000)
            self.assertEqual(first["comp_hash"], provider + "/source-cache-hash")
            self.assertEqual(catalog["models"][offset + 1]["supported_reasoning_levels"], original["models"][1]["supported_reasoning_levels"])
        for old, compatibility in zip(original["models"], catalog["models"][8:]):
            self.assertEqual(compatibility["slug"], old["slug"])
            self.assertEqual(compatibility["visibility"], "hide")
            self.assertEqual(compatibility.get("model_messages"), old.get("model_messages"))
        catalog["models"][0]["model_messages"]["instructions_template"] = "Changed copy"
        self.assertEqual(source, original)

    def test_invalid_sources_are_rejected(self):
        for source in (None, {}, {"models": []}, {"models": [None]}, {"models": [{"slug": ""}]},
                       {"models": [{"slug": "omniai/gpt-test"}]}, {"models": [{"slug": "gpt-test"}] * 2}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                build_provider_catalog(source)


if __name__ == "__main__":
    unittest.main()
