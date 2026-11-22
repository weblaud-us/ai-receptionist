"""Links the assistant texts are cleaned: no visitor session, portal login ids or ad tracking."""

from core.tenancy.models import Links, clean_link

D4W = ("https://www.centaurportal.com/d4w/org-2589/extended_search?sourceRef=https:%2F%2Feforms.centaurportal.com"
       "%2FeForms%2F05558-401956%2Fman%2Fshowmain_man&randomNumber=77c2940b&shortVer=false&su=3&userId=401956"
       "&webLogin=05558&sessionId=HZDBC2BC43F344457CA08307278AB1B2BF&sourceID=null")


def test_d4w_portal_keeps_only_the_practice_page():
    assert clean_link(D4W) == "https://www.centaurportal.com/d4w/org-2589/extended_search"


def test_session_and_tracking_params_are_dropped_and_real_ones_kept():
    url = "https://book.example.com/s?practice=42&utm_source=fb&gclid=x&sessionId=abc&PHPSESSID=y&location=2"
    assert clean_link(url) == "https://book.example.com/s?practice=42&location=2"


def test_clean_links_are_left_exactly_as_typed():
    url = "https://book.example.com/s?practice=42&name=Bilby%20Dental#top"
    assert clean_link(url) == url
    assert clean_link("bella.example/book") == "https://bella.example/book"
    assert clean_link("") == ""


def test_stored_links_are_cleaned_when_read():
    assert Links.model_validate({"booking": D4W}).booking.endswith("/org-2589/extended_search")
