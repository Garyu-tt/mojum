import unittest
from mojum.world_temperature import SCHEMA, observe_temperature

def source(identifier="heater", x=0, y=0, delta=20_000, decay=0, born=0):
    return {"id": identifier, "x_mm": x, "y_mm": y, "delta_milli_c": delta,
            "decay_milli_c_per_tick": decay, "observed_tick": born}

def point(identifier="p", x=0, y=0):
    return {"id": identifier, "x_mm": x, "y_mm": y}

class WorldTemperatureTests(unittest.TestCase):
    def sample(self, **kwargs):
        args = dict(sources=[source()], points=[point()], baseline_milli_c=20_000, tick=0, consent=True)
        args.update(kwargs)
        return observe_temperature(**args)

    def test_schema_and_origin(self):
        result = self.sample()
        self.assertEqual(result.schema, SCHEMA)
        self.assertEqual(result.observations[0].temperature_milli_c, 40_000)

    def test_distance_attenuation(self):
        result = self.sample(points=[point("near"), point("far", 1000, 0)])
        self.assertEqual({x.point_id: x.temperature_milli_c for x in result.observations},
                         {"far": 30_000, "near": 40_000})

    def test_logical_decay(self):
        self.assertEqual(self.sample(sources=[source(decay=2_000)], tick=5).observations[0].temperature_milli_c, 30_000)

    def test_expired_source(self):
        self.assertEqual(self.sample(sources=[source(decay=10_000)], tick=2).observations[0].temperature_milli_c, 20_000)

    def test_negative_source(self):
        self.assertEqual(self.sample(sources=[source(delta=-5_000)]).observations[0].temperature_milli_c, 15_000)

    def test_order_independent_digest(self):
        a = self.sample(sources=[source("b", delta=1000), source("a", delta=2000)], points=[point("z"), point("a")])
        b = self.sample(sources=[source("a", delta=2000), source("b", delta=1000)], points=[point("a"), point("z")])
        self.assertEqual(a.digest, b.digest)

    def test_repeatable_digest(self):
        self.assertEqual(self.sample().digest, self.sample().digest)

    def test_consent_required(self):
        with self.assertRaises(ValueError): self.sample(consent=False)

    def test_bool_is_not_integer(self):
        with self.assertRaises(ValueError): self.sample(tick=True)

    def test_duplicate_source(self):
        with self.assertRaises(ValueError): self.sample(sources=[source(), source()])

    def test_duplicate_point(self):
        with self.assertRaises(ValueError): self.sample(points=[point(), point()])

    def test_unknown_source_field(self):
        row = source(); row["secret"] = "no"
        with self.assertRaises(ValueError): self.sample(sources=[row])

    def test_out_of_bounds(self):
        with self.assertRaises(ValueError): self.sample(points=[point(x=100_001)])

    def test_future_source_tick(self):
        with self.assertRaises(ValueError): self.sample(sources=[source(born=1)], tick=0)

    def test_source_count_bound(self):
        with self.assertRaises(ValueError): self.sample(sources=[source(str(i)) for i in range(65)])

    def test_result_detached_and_frozen(self):
        points = [point()]
        result = self.sample(points=points)
        points[0]["x_mm"] = 999
        self.assertEqual(result.observations[0].temperature_milli_c, 40_000)
        with self.assertRaises((AttributeError, TypeError)):
            result.observations[0].temperature_milli_c = 1

if __name__ == "__main__":
    unittest.main()
