import pytest

from brandscan.config import Settings
from brandscan.instagram import InstagramClient, NotFound
from brandscan.models import Profile

TARGET = Profile(username="mokshansut", full_name="Moksha NSUT", user_id="1")

PROFILES = {
    "mokshansut": Profile("mokshansut", "1", "Moksha NSUT", is_business=True, category="College & university", followers=40000),
    "redbullindia": Profile("redbullindia", "2", "Red Bull India", is_business=True, category="Beverages",
                            followers=900000, is_verified=True),
    "djbassworm": Profile("djbassworm", "3", "Bassworm", is_professional=True, category="Musician/band"),
    "salimsulaimanmusic": Profile("salimsulaimanmusic", "4", "Salim Sulaiman", is_business=True,
                                  business_category="Creators & Celebrities", category="Musician/band"),
    "gethingyms": Profile("gethingyms", "5", "Gethin Gyms", is_business=True, category="Gym/Physical fitness center"),
    "unstop.world": Profile("unstop.world", "6", "Unstop", is_business=True, category="Education website"),
    "rahul_99": Profile("rahul_99", "7", "Rahul"),
}

FEED = [
    {"code": "p1", "caption": {"text": "EDM night is LIVE! Get ready for @djbassworm and @Ollyesse! Powered by @redbullindia"},
     "usertags": {"in": [{"user": {"username": "djbassworm"}}]}},
    {"code": "p2", "caption": {"text": "Salim Sulaiman live at Moksha Fest! Beverage partner: Red Bull"},
     "usertags": {"in": [{"user": {"username": "salimsulaimanmusic"}}, {"user": {"username": "rahul_99"}}]}},
    {"code": "p3", "caption": {"text": "#GymEvent in collaboration with @gethingyms #ad"},
     "coauthor_producers": [{"username": "gethingyms"}], "is_paid_partnership": True,
     "sponsor_tags": [{"sponsor": {"username": "gethingyms"}}]},
    {"code": "p4", "caption": {"text": "Thanks to our sponsors! Associate partner: @unstop.world. Mail team@mokshansut.com"},
     "image_versions2": {"candidates": [{"url": "img-p4", "width": 1}]}},
]


class FakeClient(InstagramClient):
    """InstagramClient answering from the fixtures above instead of the network."""

    def __init__(self, feed=FEED, profiles=PROFILES):
        super().__init__({"sessionid": "x"}, delay=(0, 0))
        self.feed, self.profiles = feed, profiles

    def get_json(self, path, params=None, referer=""):
        if "web_profile_info" in path:
            p = self.profiles.get(params["username"])
            if not p:
                raise NotFound(params["username"])
            return {"data": {"user": {
                "username": p.username, "id": p.user_id, "full_name": p.full_name,
                "is_business_account": p.is_business, "is_professional_account": p.is_professional,
                "category_name": p.category, "business_category_name": p.business_category,
                "edge_followed_by": {"count": p.followers}, "is_verified": p.is_verified,
                "is_private": p.is_private}}}
        return {"items": self.feed, "more_available": False}

    def get_bytes(self, url):
        return b"image"


class PosterReader:
    """Fake OCR: the p4 image is an 'Our sponsors' poster."""

    def read(self, data):
        return "OUR SPONSORS\nSAMSUNG\nHans Outfit\nCONGRATULATIONS!\nGOLD SPONSOR\nREPLIT"


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, ocr_backend="none", max_lookups=50)
