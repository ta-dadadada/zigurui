#!/usr/bin/env python3
"""配列透過pathの独立期待値と文書単位集計・境界fixtureを確認する。"""
from pathlib import Path
import unittest

import evaluate_frontmatter as base
import evaluate_frontmatter_objects as evaluation


class ObjectArrayOracleTest(unittest.TestCase):
    def test_schema_counts_documents_and_immediate_element_types(self):
        schema = evaluation.expected_schema(evaluation.fixture_documents())
        self.assertEqual((schema["document_count"], schema["frontmatter_count"]), (10, 9))
        fields = {field["pointer"]: field for field in schema["fields"]}
        self.assertEqual(fields["/items"], dict(pointer="/items", types=["array", "null", "object"], present=9, missing=1, null_count=1,
                                              element_types=["array", "boolean", "float", "integer", "null", "object", "string"]))
        self.assertEqual(fields["/items/name"], dict(pointer="/items/name", types=["null", "string"], present=4, missing=6, null_count=3, element_types=[]))
        self.assertEqual(fields["/items/score"], dict(pointer="/items/score", types=["float", "integer", "null", "string"], present=3, missing=7, null_count=2, element_types=[]))
        self.assertEqual(fields["/items/children"]["element_types"], ["array", "object"])
        self.assertEqual(fields["/items/children/name"]["present"], 2)
        self.assertNotIn("/items/0/name", fields)
        self.assertEqual(list(fields), sorted(fields, key=lambda key: key.encode()))

    def test_empty_containers_do_not_invent_descendants(self):
        docs = [base.document("a.md", "items: [[[]], {}]\n", "BodyNeedle\n", {"items": [[[]], {}]})]
        self.assertEqual(evaluation.expected_schema(docs)["fields"], [dict(pointer="/items", types=["array"], present=1, missing=0,
                                                                          null_count=0, element_types=["array", "object"])])

    def test_terminal_scalar_is_not_recursively_flattened(self):
        metadata = {"items": [[1], [[None]], [{"name": "Alice"}], 1.0]}
        self.assertEqual(evaluation.values_at(metadata, "/items/name"), ["Alice"])
        self.assertFalse(evaluation.where_matches(metadata, [("/items", 1)]))
        self.assertFalse(evaluation.where_matches(metadata, [("/items", None)]))
        self.assertTrue(evaluation.where_matches(metadata, [("/items", 1.0)]))
        self.assertFalse(evaluation.where_matches(metadata, [("/items/0/name", "Alice")]))

    def test_independent_conditions_can_match_different_elements(self):
        metadata = {"items": [{"name": "Alice", "state": "draft"}, {"name": "Bob", "state": "ready"}]}
        self.assertTrue(evaluation.where_matches(metadata, [("/items/name", "Alice"), ("/items/state", "ready")]))

    def test_handwritten_hit_sets_and_original_lines(self):
        docs = evaluation.fixture_documents()
        for mode in ("literal", "normalized"):
            for case in evaluation.fixture_cases():
                _, paths = evaluation.expected_output(docs, case, mode, Path("/synthetic"))
                expected = case["expected"][mode] if type(case["expected"]) is dict else case["expected"]
                self.assertEqual(paths, expected, case["id"])
        case = next(case for case in evaluation.fixture_cases() if case["id"] == "normalized-expansion")
        output, _ = evaluation.expected_output(docs, case, "normalized", Path("/synthetic"))
        b = next(doc for doc in docs if doc["path"] == "b.md")
        line = b["raw"].splitlines().index("BodyNeedle 前㌔後") + 1
        self.assertEqual(output, f"/synthetic/b.md:{line}: BodyNeedle 前㌔後 \n".encode())

    def test_old_fixture_oracle_agrees(self):
        self.assertEqual(evaluation.expected_schema(base.fixture_documents()), base.expected_schema(base.fixture_documents()))
        for mode in ("literal", "normalized"):
            for case in base.fixture_cases():
                self.assertEqual(evaluation.expected_output(base.fixture_documents(), case, mode, Path("/synthetic")),
                                 base.original_output(base.fixture_documents(), case["terms"], case["filters"], mode, Path("/synthetic")))

    def test_object_resources_have_exact_node_and_depth_boundaries(self):
        cases = {case["id"]: case for case in evaluation.resource_cases()}

        def nodes(value):
            if type(value) is dict:
                return 1 + sum(1 + nodes(child) for child in value.values())
            return 1 + sum(map(nodes, value)) if type(value) is list else 1

        def depth(value):
            children = value.values() if type(value) is dict else value if type(value) is list else []
            return 1 + max([0, *map(depth, children)])

        for maximum in (4096, 4097):
            self.assertEqual(nodes(cases[f"object-nodes-{maximum}"]["documents"][0]["metadata"]), maximum)
            key = next(iter(cases[f"object-pointer-{maximum}"]["documents"][0]["metadata"]["items"][0]))
            self.assertEqual(len(("/items/" + base.escape(key)).encode()), maximum)
        for maximum in (16, 17):
            self.assertEqual(depth(cases[f"object-depth-{maximum}"]["documents"][0]["metadata"]), maximum)
            self.assertEqual(depth(cases[f"array-depth-{maximum}"]["documents"][0]["metadata"]), maximum)

    def test_old_unsupported_array_examples_are_now_positive_scope(self):
        names = {case["id"] for case in evaluation.invalid_cases()}
        self.assertNotIn("array-object", names)
        self.assertNotIn("array-array", names)
        self.assertIn("duplicate-in-element", names)
        self.assertIn("escaped-duplicate-in-element", names)


if __name__ == "__main__":
    unittest.main()
