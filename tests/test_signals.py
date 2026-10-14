import pytest

from brandscan import config
from brandscan.instagram import parse_feed_item
from brandscan.models import Media, Post, Profile
from brandscan.signals import Exclusions, SignalExtractor, board_names, role_phrases

from .conftest import TARGET
from .test_parsing import FEED_ITEM


@pytest.mark.parametrize("text, expected", [
    ("Powered by Red Bull", [("name", "Red Bull", "Powered By")]),
    ("POWERED BY\nRED BULL", [("name", "Red Bull", "Powered By")]),
    ("Title sponsor: @boat.nirvana", [("handle", "boat.nirvana", "Title Sponsor")]),
    ("Our beverage partner is Coca Cola!", [("name", "Coca Cola", "Beverage Partner")]),
    ("Beverage Partner - Coca Cola", [("name", "Coca Cola", "Beverage Partner")]),
    ("in association with Zomato & Swiggy", [("name", "Zomato", "Associate Partner"),
                                             ("name", "Swiggy", "Associate Partner")]),
    ("Powered by the amazing Spotify for the night", [("name", "Spotify", "Powered By")]),
    ("GOLD SPONSOR\nSAMSUNG", [("name", "Samsung", "Gold Sponsor")]),
    ("Special thanks to Devfolio", [("name", "Devfolio", "Supporter")]),
    ("We partnered with everyone", []),
])
def test_role_phrases(text, expected):
    assert role_phrases(text) == expected


def test_board_names_after_header():
    text = "HACKSTROM'25\nOUR SPONSORS\nSAMSUNG\nHans Outfit | Mom's Kitchen\nCONGRATULATIONS!\njoin us on discord\n2025"
    assert board_names(text, caption_says_sponsors=False) == ["Samsung", "Hans Outfit", "Mom's Kitchen"]


def test_board_names_need_header_or_caption():
    assert board_names("SAMSUNG\nAPPLE", caption_says_sponsors=False) == []
    assert board_names("SAMSUNG\nAPPLE", caption_says_sponsors=True) == ["Samsung", "Apple"]


def test_exclusions_drop_target_and_its_branding():
    excluded = Exclusions(TARGET)
    assert excluded("mokshansut")
    assert excluded("mokshansut.official")
    assert excluded("mokshafest")   # name starting with "moksha"
    assert not excluded("redbullindia")
    hwi = Exclusions(Profile("hwi_mmmut", full_name="Hack With India MMMUT"))
    assert hwi("withindia") and hwi("hackwithindia")
    assert not hwi("samsung")


def test_extract_signals():
    post = parse_feed_item(FEED_ITEM)
    signals = SignalExtractor(TARGET, use_ner=False).extract(post)
    by = {(s.key, s.source): s for s in signals}
    assert by[("redbullindia", "paid_partnership")].role == "Paid Partnership"
    assert ("redbullindia", "role_phrase") in by
    assert by[("redbullindia", "caption_mention")].weight == config.WEIGHTS["caption_mention"] * config.SPONSORED_POST_MULTIPLIER
    assert by[("spotifyindia", "collab_author")]
    assert by[("cocacola", "role_phrase")].role == "Beverage Partner"   # from Instagram's alt text
    assert ("djsnake", "photo_tag") in by
    assert not any(s.key.startswith("moksha") for s in signals)


def test_extract_ocr_poster_and_ignores_emails():
    class Reader:
        def read(self, data):
            return "ASSOCIATE PARTNER\n@unstop.world\nOUR SPONSORS\nREPLIT"
    post = Post(code="P1", caption="Mail us at team@mokshansut.com", media=[Media(image_url="u")])
    ex = SignalExtractor(TARGET, use_ner=False, image_reader=Reader(), fetch_image=lambda url: b"img")
    got = {(s.key, s.source) for s in ex.extract(post)}
    assert got == {("unstop.world", "role_phrase"), ("unstop.world", "image_mention"), ("replit", "sponsor_board")}


def test_domain_mentions():
    post = Post(code="P", caption="Grab passes at www.zomato.com! Register: forms.gle/abc, linktr.ee/moksha")
    got = {(s.key, s.display) for s in SignalExtractor(TARGET, use_ner=False).extract(post)}
    assert got == {("zomato", "www.zomato.com")}


def test_ner_finds_org_names():
    ex = SignalExtractor(TARGET, use_ner=True)
    if ex.nlp is None:
        pytest.skip("spaCy model not installed")
    signals = ex.extract(Post(code="P", caption="Huge thanks to Google and Microsoft for coming to campus"))
    assert {"google", "microsoft"} & {s.key for s in signals if s.source == "name_mention"}
