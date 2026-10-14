import pytest

from brandscan import classify as C
from brandscan import config
from brandscan.models import Profile, Signal
from brandscan.report import aggregate, is_brand_candidate, write_csv
from brandscan.store import Checkpoint, ProfileCache


def sig(key, source, post, kind="handle", role="Mentioned"):
    return Signal(key, ("@" + key) if kind == "handle" else key, kind, source, role,
                  config.WEIGHTS[source], post)


@pytest.mark.parametrize("profile, expected", [
    (Profile("redbull", is_business=True, is_professional=True, category="Beverages"), C.BRAND),
    (Profile("djsnake", is_professional=True, category="Musician/band"), C.CREATOR),
    (Profile("someone", is_professional=True, category=""), C.CREATOR),
    (Profile("nsut", is_business=True, category="College & university"), C.ORGANIZATION),
    (Profile("star", is_business=True, business_category="Creators & Celebrities", category="Product/service"), C.CREATOR),
    (Profile("unstop", is_business=True, category="Education website"), C.BRAND),
    (Profile("rahul_99"), C.PERSONAL),
    (None, C.UNVERIFIED),
])
def test_classify(profile, expected):
    assert C.classify(profile) == expected


def test_aggregate_merges_names_into_handles_and_ranks():
    entities = aggregate([
        sig("redbullindia", "caption_mention", "p1"),
        sig("redbull", "role_phrase", "p2", kind="name", role="Powered By"),
        sig("djsnake", "photo_tag", "p1"),
    ])
    assert [e.key for e in entities] == ["redbullindia", "djsnake"]
    rb = entities[0]
    assert rb.score == 35 and set(rb.posts) == {"p1", "p2"}
    assert rb.primary_role == "Powered By"


def test_brand_candidate_rules():
    entities = {e.key: e for e in aggregate([
        sig("onceonly", "name_mention", "p1", kind="name"),
        sig("twice", "name_mention", "p1", kind="name"),
        sig("twice", "name_mention", "p2", kind="name"),
        sig("samsung", "sponsor_board", "p1", kind="name", role="Listed Sponsor"),      # known brand
        sig("congratulations", "sponsor_board", "p1", kind="name", role="Listed Sponsor"),
        sig("hackstrom", "sponsor_board", "p1", kind="name", role="Listed Sponsor"),
        sig("hackstrom", "sponsor_board", "p2", kind="name", role="Listed Sponsor"),
        sig("hackstrom", "sponsor_board", "p3", kind="name", role="Listed Sponsor"),
    ])}
    keep = {k for k, e in entities.items() if is_brand_candidate(e, posts_scanned=4)}
    assert keep == {"twice", "samsung"}   # hackstrom is on 3 of 4 posts: own branding
    assert entities["samsung"].known


def test_write_csv(tmp_path):
    entities = aggregate([sig("redbullindia", "paid_partnership", "p1", role="Paid Partnership")])
    entities[0].profile = Profile("redbullindia", full_name="Red Bull India", is_business=True, followers=1000)
    entities[0].type = C.BRAND
    path = tmp_path / "out.csv"
    write_csv(path, entities)
    assert "Red Bull India,@redbullindia,brand,50,1,Paid Partnership" in path.read_text(encoding="utf-8-sig")


def test_checkpoint_roundtrip(tmp_path):
    cp = Checkpoint(tmp_path, "t")
    cp.add("p1", [sig("x", "photo_tag", "p1")])
    cp.save()
    again = Checkpoint(tmp_path, "t")
    assert again.done == {"p1"} and again.signals[0].key == "x"
    assert Checkpoint(tmp_path, "t", fresh=True).done == set()


def test_profile_cache(tmp_path):
    cache = ProfileCache(tmp_path / "p.json")
    cache.put("a", Profile("a", is_business=True))
    cache.put("missing", None)
    cache.save()
    again = ProfileCache(tmp_path / "p.json")
    assert again.get("a")[1].is_business
    assert again.get("missing") == (True, None)
    assert again.get("never") == (False, None)
