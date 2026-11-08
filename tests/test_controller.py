"""Tests for the parts where a bug would silently cause harm: the statistics and idempotent ingestion."""
from controller.stats import holm, tv_distance, two_prop_z
from controller.store import Store


def test_z_test_detects_real_difference_and_ignores_noise():
    assert two_prop_z(400, 1000, 500, 1000) < 1e-5      # 40% vs 50% on 1000 each: clearly real
    assert two_prop_z(40, 100, 42, 100) > 0.3            # 40% vs 42% on 100 each: noise
    assert two_prop_z(500, 1000, 400, 1000) > 0.99       # one-sided: B worse is never "significant"
    assert two_prop_z(0, 0, 5, 10) == 1.0                 # no data -> no decision


def test_holm_is_step_down_and_deterministic():
    p = {"a": 0.001, "b": 0.03, "c": 0.04}
    assert holm(p, 0.05) == {"a"}                         # 0.001<=0.05/3 ok; 0.03>0.05/2 stop
    assert holm({"a": 0.001, "b": 0.03, "c": 0.04}, 0.1) == {"a", "b", "c"}
    assert holm({}, 0.05) == set()


def test_tv_distance_normalises():
    assert tv_distance({"D1": 0.5, "D2": 0.5}, {"D2": 1}) == 0.5
    assert tv_distance({"V1": 1 / 3, "V2": 1 / 3, "V3": 1 / 3}, {"V1": 1, "V2": 1, "V3": 1}) < 1e-9


def test_ingest_is_idempotent(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    rows = [("wf", 1, 2, "voice", "V1", 10, 4, 2, 0)]
    assert s.ingest_chunk(5, rows, [])
    assert not s.ingest_chunk(5, rows, [])                # replay after a crash: ignored
    assert not s.ingest_chunk(4, rows, [])                # older chunk: ignored
    assert s.arm_stats("wf", "voice", 0)["V1"]["dials"] == 10
    assert s.ingest_chunk(6, rows, [])
    assert s.arm_stats("wf", "voice", 0)["V1"]["dials"] == 20
