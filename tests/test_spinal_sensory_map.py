import copy
import unittest

from mojum.spinal_sensory_map import (
    CONTACT_SCHEMA,
    MAP_SCHEMA,
    MAX_CANONICAL_BYTES,
    MAX_DENSITY_PPM,
    MAX_LOD,
    MAX_REGIONS,
    MAX_ROUTES_PER_REGION,
    MAX_SIGNAL_PPM,
    MAX_TOTAL_ROUTES,
    ROUTE_SCHEMA,
    SPINAL_SEGMENTS,
    SpinalSensoryMapError,
    compile_sensory_map,
    route_contact,
)


def region(region_id, pathway="spinal", routes=None, **overrides):
    value = {
        "region_id": region_id,
        "pathway": pathway,
        "routes": ["C5", "C6"] if routes is None else routes,
        "density_ppm": 250_000,
        "lod": 2,
    }
    value.update(overrides)
    return value


def specification(regions=None, **overrides):
    value = {
        "schema": MAP_SCHEMA,
        "visibility": "PUBLIC_SYNTHETIC",
        "consent": "explicit",
        "map_id": "fixture-surface-map",
        "revision": 3,
        "regions": [region("arm.left")] if regions is None else regions,
    }
    value.update(overrides)
    return value


def contact(**overrides):
    value = {
        "schema": CONTACT_SCHEMA,
        "visibility": "PUBLIC_SYNTHETIC",
        "consent": "explicit",
        "map_id": "fixture-surface-map",
        "map_revision": 3,
        "event_id": "contact-0001",
        "tick": 42,
        "source_id": "synthetic-contact-fixture",
        "source_revision": 7,
        "region_id": "arm.left",
        "signal_ppm": 125_000,
    }
    value.update(overrides)
    return value


class SpinalSensoryMapTests(unittest.TestCase):
    def test_overlap_and_cranial_pathways_are_explicit_and_separate(self):
        compiled = compile_sensory_map(specification([
            region("arm.left", routes=["C6", "C5"]),
            region("hand.left", routes=["C7", "C6"], density_ppm=900_000, lod=5),
            region(
                "head.face",
                pathway="cranial",
                routes=["cranial:face.secondary", "cranial:face.primary"],
                density_ppm=700_000,
            ),
        ]))

        self.assertEqual(compiled.spinal_region_count, 2)
        self.assertEqual(compiled.cranial_region_count, 1)
        self.assertEqual(compiled.route_assignment_count, 6)
        self.assertEqual(compiled.shared_spinal_segments, ("C6",))
        self.assertEqual(compiled.regions[0].routes, ("C5", "C6"))
        self.assertEqual(
            compiled.regions[2].routes,
            ("cranial:face.primary", "cranial:face.secondary"),
        )

    def test_routing_receipt_is_deterministic_and_carries_provenance(self):
        compiled = compile_sensory_map(specification())
        first = route_contact(compiled, contact())
        second = route_contact(compiled, copy.deepcopy(contact()))

        self.assertEqual(first, second)
        self.assertEqual(first.schema, ROUTE_SCHEMA)
        self.assertEqual(first.map_revision, 3)
        self.assertEqual(first.map_sha256, compiled.sha256)
        self.assertEqual(first.source_id, "synthetic-contact-fixture")
        self.assertEqual(first.source_revision, 7)
        self.assertEqual(first.routes, ("C5", "C6"))
        self.assertEqual(len(first.sha256), 64)

    def test_map_digest_ignores_input_order_but_commits_to_metadata(self):
        arm = region("arm.left", routes=["C6", "C5"])
        head = region(
            "head.face", pathway="cranial",
            routes=["cranial:z", "cranial:a"],
        )
        first = compile_sensory_map(specification([arm, head]))
        reordered = compile_sensory_map(specification([
            dict(head, routes=list(reversed(head["routes"]))),
            dict(arm, routes=list(reversed(arm["routes"]))),
        ]))
        changed = compile_sensory_map(specification([
            dict(arm, density_ppm=arm["density_ppm"] + 1), head,
        ]))

        self.assertEqual(first.sha256, reordered.sha256)
        self.assertEqual(first.regions, reordered.regions)
        self.assertNotEqual(first.sha256, changed.sha256)

    def test_compilation_and_receipt_are_detached_and_immutable(self):
        supplied = specification()
        compiled = compile_sensory_map(supplied)
        routed = route_contact(compiled, contact())
        supplied["regions"][0]["routes"][0] = "T1"
        supplied["regions"][0]["region_id"] = "mutated"

        self.assertEqual(compiled.regions[0].region_id, "arm.left")
        self.assertEqual(compiled.regions[0].routes, ("C5", "C6"))
        with self.assertRaises((AttributeError, TypeError)):
            compiled.revision = 4
        with self.assertRaises((AttributeError, TypeError)):
            routed.routes = ()

    def test_all_31_logical_spinal_segments_are_admitted_in_canonical_order(self):
        regions = [
            region(f"r{index}", routes=[segment])
            for index, segment in enumerate(reversed(SPINAL_SEGMENTS))
        ]
        compiled = compile_sensory_map(specification(regions))
        by_id = {item.region_id: item.routes[0] for item in compiled.regions}

        self.assertEqual(len(SPINAL_SEGMENTS), 31)
        self.assertEqual(set(by_id.values()), set(SPINAL_SEGMENTS))

    def test_spinal_and_cranial_namespaces_cannot_be_mixed(self):
        invalid = (
            region("bad", routes=["C9"]),
            region("bad", routes=["cranial:face"]),
            region("bad", pathway="cranial", routes=["C1"]),
            region("bad", pathway="cranial", routes=["cranial:"]),
            region("bad", pathway="cranial", routes=["cranial:face:branch"]),
        )
        for bad_region in invalid:
            with self.subTest(bad_region=bad_region), self.assertRaises(SpinalSensoryMapError):
                compile_sensory_map(specification([bad_region]))

    def test_duplicate_region_and_intra_region_route_fail_closed(self):
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(specification([region("same"), region("same", routes=["T1"])]))
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(specification([region("dup", routes=["C1", "C1"])]))

    def test_density_lod_and_signal_boundaries(self):
        compiled = compile_sensory_map(specification([
            region("minimum", routes=["C1"], density_ppm=1, lod=0),
            region(
                "maximum", routes=["Co1"],
                density_ppm=MAX_DENSITY_PPM, lod=MAX_LOD,
            ),
        ]))
        low = route_contact(compiled, contact(region_id="minimum", signal_ppm=0))
        high = route_contact(
            compiled, contact(region_id="maximum", signal_ppm=MAX_SIGNAL_PPM)
        )
        self.assertEqual((low.density_ppm, low.lod, low.signal_ppm), (1, 0, 0))
        self.assertEqual(
            (high.density_ppm, high.lod, high.signal_ppm),
            (MAX_DENSITY_PPM, MAX_LOD, MAX_SIGNAL_PPM),
        )

        for field, value in (
            ("density_ppm", 0),
            ("density_ppm", MAX_DENSITY_PPM + 1),
            ("lod", -1),
            ("lod", MAX_LOD + 1),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(
                SpinalSensoryMapError
            ):
                compile_sensory_map(specification([region("bad", **{field: value})]))
        for value in (-1, MAX_SIGNAL_PPM + 1):
            with self.subTest(signal_ppm=value), self.assertRaises(SpinalSensoryMapError):
                route_contact(compiled, contact(region_id="minimum", signal_ppm=value))

    def test_region_route_and_byte_budgets(self):
        maximum_routes = SPINAL_SEGMENTS[:MAX_ROUTES_PER_REGION]
        admitted_routes = compile_sensory_map(
            specification([region("route-limit", routes=list(reversed(maximum_routes)))])
        )
        self.assertEqual(admitted_routes.regions[0].routes, maximum_routes)
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(specification([
                region(
                    "route-overflow",
                    routes=list(SPINAL_SEGMENTS[:MAX_ROUTES_PER_REGION + 1]),
                )
            ]))

        maximum_regions = [
            region(f"r{index}", pathway="cranial", routes=[f"cranial:r{index}"])
            for index in range(MAX_REGIONS)
        ]
        self.assertEqual(
            len(compile_sensory_map(specification(maximum_regions)).regions),
            MAX_REGIONS,
        )
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(specification(maximum_regions + [region("overflow")]))

        route_budget = []
        for index in range(MAX_REGIONS):
            routes = [f"cranial:r{index}.{route_index}" for route_index in range(4)]
            route_budget.append(region(f"r{index}", pathway="cranial", routes=routes))
        admitted = compile_sensory_map(specification(route_budget))
        self.assertEqual(admitted.route_assignment_count, MAX_TOTAL_ROUTES)
        route_budget[0]["routes"].append("cranial:overflow")
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(specification(route_budget))

        long_records = []
        for index in range(MAX_REGIONS):
            region_id = f"r{index:03d}." + "x" * 91
            routes = [
                f"cranial:r{index:03d}.{route_index}." + "x" * 79
                for route_index in range(4)
            ]
            long_records.append(region(region_id, pathway="cranial", routes=routes))
        with self.assertRaisesRegex(SpinalSensoryMapError, str(MAX_CANONICAL_BYTES)):
            compile_sensory_map(specification(long_records))

    def test_exact_shape_rejects_privileged_world_fields(self):
        compiled = compile_sensory_map(specification())
        for extra_key in ("world_position", "object_id", "material", "payload"):
            bad = contact()
            bad[extra_key] = "untrusted"
            with self.subTest(extra_key=extra_key), self.assertRaises(SpinalSensoryMapError):
                route_contact(compiled, bad)

        bad_map = specification()
        bad_map["runtime"] = "spinal"
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(bad_map)

    def test_envelopes_require_public_synthetic_explicit_consent(self):
        for field, value in (
            ("schema", "future/2"),
            ("visibility", "PRIVATE"),
            ("consent", "implicit"),
        ):
            bad_map = specification(**{field: value})
            with self.subTest(target="map", field=field), self.assertRaises(SpinalSensoryMapError):
                compile_sensory_map(bad_map)

            compiled = compile_sensory_map(specification())
            bad_contact = contact(**{field: value})
            with self.subTest(target="contact", field=field), self.assertRaises(SpinalSensoryMapError):
                route_contact(compiled, bad_contact)

    def test_native_types_only_and_hostile_container_subclasses_fail_closed(self):
        class EvilDict(dict):
            pass

        class EvilList(list):
            pass

        bad_revision = specification(revision=True)
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(bad_revision)
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(EvilDict(specification()))
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(specification(regions=EvilList([region("arm.left")])))
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(specification([EvilDict(region("arm.left"))]))
        with self.assertRaises(SpinalSensoryMapError):
            compile_sensory_map(specification([region("arm.left", routes=EvilList(["C1"]))]))

        compiled = compile_sensory_map(specification())
        with self.assertRaises(SpinalSensoryMapError):
            route_contact(compiled, contact(tick=True))
        with self.assertRaises(SpinalSensoryMapError):
            route_contact(compiled, EvilDict(contact()))

    def test_identifiers_are_bounded_ascii(self):
        for bad_id in ("", "x" * 97, " leading", ".leading", "emoji-☃", "line\nbreak"):
            with self.subTest(bad_id=bad_id), self.assertRaises(SpinalSensoryMapError):
                compile_sensory_map(specification(map_id=bad_id))

    def test_contact_must_match_map_revision_and_known_region(self):
        compiled = compile_sensory_map(specification())
        for overrides in (
            {"map_id": "other-map"},
            {"map_revision": 4},
            {"region_id": "unknown"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(SpinalSensoryMapError):
                route_contact(compiled, contact(**overrides))

    def test_route_contact_requires_compiled_exact_type(self):
        with self.assertRaises(SpinalSensoryMapError):
            route_contact(specification(), contact())


if __name__ == "__main__":
    unittest.main()
