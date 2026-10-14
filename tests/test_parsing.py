import json

from brandscan.auth import import_cookies, load_cookies, parse_cookie_data
from brandscan.graph import parse_graph_media
from brandscan.instagram import InstagramClient, parse_feed_item, parse_graphql_node
from brandscan.models import Profile

FEED_ITEM = {
    "code": "ABC123",
    "taken_at": 1700000000,
    "caption": {"text": "Moksha is here! Powered by @redbullindia #ad"},
    "is_paid_partnership": True,
    "sponsor_tags": [{"sponsor": {"username": "redbullindia"}}],
    "coauthor_producers": [{"username": "spotifyindia"}],
    "carousel_media": [
        {"media_type": 1,
         "image_versions2": {"candidates": [{"url": "small", "width": 320}, {"url": "big", "width": 1080}]},
         "accessibility_caption": "May be an image of text that says 'BEVERAGE PARTNER: COCA COLA'",
         "usertags": {"in": [{"user": {"username": "djsnake"}}]}},
        {"media_type": 2, "image_versions2": {"candidates": [{"url": "cover", "width": 640}]}},
    ],
}

GRAPHQL_NODE = {
    "shortcode": "XYZ789",
    "taken_at_timestamp": 1700000000,
    "edge_media_to_caption": {"edges": [{"node": {"text": "Thanks @boat.nirvana"}}]},
    "edge_media_to_tagged_user": {"edges": [{"node": {"user": {"username": "boat.nirvana"}}}]},
    "edge_sidecar_to_children": {"edges": [
        {"node": {"display_url": "img1", "accessibility_caption": "",
                  "edge_media_to_tagged_user": {"edges": [{"node": {"user": {"username": "zomato"}}}]}}},
    ]},
}


def test_parse_feed_item():
    post = parse_feed_item(FEED_ITEM)
    assert post.code == "ABC123"
    assert post.sponsors == ["redbullindia"] and post.is_paid_partnership
    assert post.coauthors == ["spotifyindia"]
    assert [m.image_url for m in post.media] == ["big", "cover"]
    assert post.tagged == ["djsnake"]


def test_parse_feed_item_without_caption_or_carousel():
    post = parse_feed_item({"code": "Q", "caption": None, "media_type": 1})
    assert post.caption == "" and len(post.media) == 1 and not post.is_paid_partnership


def test_parse_graphql_node():
    post = parse_graphql_node(GRAPHQL_NODE)
    assert post.code == "XYZ789"
    assert post.caption == "Thanks @boat.nirvana"
    assert post.tagged == ["boat.nirvana", "zomato"]
    assert post.media[0].image_url == "img1"


def test_parse_graph_api_media():
    post = parse_graph_media({
        "id": "1", "caption": "Powered by @redbullindia", "media_type": "CAROUSEL_ALBUM",
        "permalink": "https://www.instagram.com/p/DAbc_12/", "timestamp": "2025-03-01T10:00:00+0000",
        "children": {"data": [{"media_type": "IMAGE", "media_url": "a"},
                              {"media_type": "VIDEO", "media_url": "v", "thumbnail_url": "thumb"}]}})
    assert post.code == "DAbc_12" and post.timestamp
    assert [m.image_url for m in post.media] == ["a", "thumb"]


def test_iter_posts_paginates_when_logged_in(monkeypatch):
    client = InstagramClient({"sessionid": "s"}, delay=(0, 0))
    pages = [
        {"items": [{"code": "a"}, {"code": "b"}], "more_available": True, "next_max_id": "m1"},
        {"items": [{"code": "c"}], "more_available": False},
    ]
    calls = []

    def fake_get_json(path, params=None, referer=""):
        calls.append(params)
        return pages[len(calls) - 1]
    monkeypatch.setattr(client, "get_json", fake_get_json)
    assert [p.code for p in client.iter_posts(Profile("t", user_id="1"), {})] == ["a", "b", "c"]
    assert calls[1]["max_id"] == "m1"


def test_iter_posts_anonymous_uses_profile_edges():
    client = InstagramClient(None, delay=(0, 0))
    raw = {"edge_owner_to_timeline_media": {"edges": [{"node": GRAPHQL_NODE}]}}
    assert [p.code for p in client.iter_posts(Profile("t"), raw)] == ["XYZ789"]


def test_hourly_cap_counts_calls():
    client = InstagramClient(None, delay=(0, 0), hourly_cap=5)
    for _ in range(3):
        client._throttle()
    assert len(client._calls) == 3


def test_cookie_export_import(tmp_path):
    export = [  # Cookie-Editor style export
        {"name": "sessionid", "value": "123%3Aabc", "domain": ".instagram.com"},
        {"name": "csrftoken", "value": "tok", "domain": ".instagram.com"},
        {"name": "NID", "value": "x", "domain": ".google.com"},
    ]
    f = tmp_path / "export.json"
    f.write_text(json.dumps(export))
    import_cookies(tmp_path / "sessions", "me", f)
    assert load_cookies(tmp_path / "sessions", "me") == {"sessionid": "123:abc", "csrftoken": "tok"}
    assert parse_cookie_data({"sessionid": "s", "junk": "j"}) == {"sessionid": "s"}
