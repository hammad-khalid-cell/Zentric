from app.core.language_detect import detect_language


def test_plain_english_is_english():
    assert detect_language("Where is my order? It's very late.") == "english"


def test_roman_urdu_keyword_is_detected():
    assert detect_language("mera parcel kab tak aayega") == "roman_urdu"


def test_mixed_message_with_urdu_marker_is_roman_urdu():
    assert detect_language("bhai yeh delivery abhi tak nahi aai, kab milegi?") == "roman_urdu"


def test_word_boundary_does_not_false_positive():
    # "der" (roman urdu for "late") must not match inside "order" or "delivered"
    assert detect_language("Please check the order status, it was delivered yesterday.") == "english"
