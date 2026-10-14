import json

from brandscan.auth import import_cookies, load_cookies, parse_cookie_data
from brandscan.graph import parse_graph_media
from brandscan.instagram import (InstagramClient, parse_feed_item, parse_graphql_node, parse_profile,
                                 parse_profile_page)
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


PROFILE_PAGE = (
    '<html><script type="application/json">{"require":[["x",{"__bbox":{"result":{"data":{"xig_user_by_username":'
    '{"pk":"13460080","id":"1784","username":"engifest_dtu","full_name":"Engifest DTU","is_private":false,'
    '"follower_count":37079,"bio_links":[{"url":"https://engifest.in"}]}}}}}]]}</script>'
    '<script type="application/json">{"require":[["y",{"__bbox":{"result":{"data":{"xig_user_by_username":'
    '{"username":null,"polaris_ordered_timeline_connection":{"edges":[{"node":{"pk":"9","code":"DQx1",'
    '"caption":{"text":"Powered by @redbullindia #ad"},"display_uri":"img",'
    '"accessibility_caption":"May be an image of text"}}]}}}}}}]]}</script></html>'
)


def test_profile_page_fallback_parses_profile_and_posts():
    user = parse_profile_page(PROFILE_PAGE)
    profile = parse_profile(user)
    assert (profile.username, profile.user_id, profile.followers) == ("engifest_dtu", "13460080", 37079)
    assert profile.external_url == "https://engifest.in" and not profile.is_private
    posts = list(InstagramClient(None, delay=(0, 0)).iter_posts(profile, user))
    assert [(p.code, p.caption, p.media[0].image_url, p.media[0].alt_text) for p in posts] == [
        ("DQx1", "Powered by @redbullindia #ad", "img", "May be an image of text")]
    assert parse_profile_page("<html>Page not found</html>") is None


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
